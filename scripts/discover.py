"""Find a research idea (Workflow B, Mode 2) from the command line.

Usage (from the repo root, venv active, database running, Anthropic key in .env):
    python -m scripts.discover "NLP"
    python -m scripts.discover "NLP" --interests "evaluation of LLM agents" --technologies "PyTorch" \\
        --exclude medical --domain education --complexity beginner \\
        --constraint "available time=10 weeks" --count 3 --budget 12
    python -m scripts.discover --continue <investigation id>      resume where it stopped
    python -m scripts.discover "NLP" --yes                        skip the questions

So far (steps 5.1a-5.4):
  1. the Idea Analyst's field brief (4-8 subtopics of your field, each with why it fits
     you and its landscape searches); you answer its questions (Enter skips one), may
     remove subtopics, and confirm;
  2. the landscape search: every search of the brief is run as recent work, surveys,
     highly cited work and newest preprints on OpenAlex and arXiv (no Claude calls;
     responses cached 7 days); the papers found are stored;
  3. reading the landscape: the relevance screener (a small model) scores every paper
     against the subtopics, code picks about 45 to read (at least 5 per subtopic, surveys
     first) and the Paper Reader reads them into the library;
  4. mining: recurring open problems (future work and negative or mixed findings, grouped
     by a small model, kept by code only if 3+ papers from 2+ author groups), confirmed
     contradictions, subtopic summaries and datasets, stored for the Scout;
  5. the Scout proposes 8-12 candidate ideas from that material; code keeps an idea only if
     it is grounded in at least 2 verified quotes (others are screened out, with the reason);
  6. pre-screening: near-duplicates, your time limit and compute ceiling (code), and one quick
     feasibility call for all candidates; the best --max-light (default 5) go on, varied by
     origin strategy, the rest wait in reserve;
  7. light evaluation, one candidate at a time: Mode 1's evaluation at depth light (about
     $1-1.40 each), then triage: advanced, needs refinement or rejected;
  8. the candidate loop (scripts/candidate_loop.py): an idea sent back for refinement gets its
     next version from the Scout (about 10-20 cents) and a light re-evaluation, at most 2 per
     idea and 4 in all.
Replenishing comes with step 5.5b; full evaluation of the best and the ranking with step 5.6.

This calls the paid Anthropic API (the brief: a few cents per call; screening the landscape:
a few cents; reading: about 3 cents per paper not already in the library; mining: a few
cents; the Scout: one call, roughly 20-40 cents; quick feasibility: about 15 cents; light
evaluations as above) and the free
OpenAlex and arXiv APIs.
Exit code: 0 ok, 1 a step failed, 2 invalid input or an unavailable database, 3 the budget
is used up (resume with --continue and a higher --budget).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from collections.abc import Callable

from src.ai.schemas.field_brief import FieldBriefOutput
from src.ai.workflows.budget import budget_left, cost_cap, set_cost_cap, set_light_cap
from src.ai.workflows.clarification import NotWaitingForClarification, answers_for
from src.ai.workflows.evaluation import Sessions
from src.ai.workflows.landscape import LandscapeOutcome, LandscapeReading, landscape_reading_step, landscape_step
from src.ai.workflows.mining import MiningOutcome, mining_step
from src.ai.workflows.runner import EvaluationModels
from scripts.candidate_loop import LoopModels, continue_at, evaluate_light, generate
from src.ai.workflows.field import (
    apply_brief_answers,
    confirm_brief,
    load_pending_brief,
    remove_subtopics,
    understand_field,
)
from src.ai.workflows.states import Stage
from src.config import ConfigError
from src.db.models import Investigation
from src.db.models.run import COMPLEXITY_LEVELS
from src.db.repositories.cache import DbResponseCache
from src.db.repositories.runs import create_investigation
from src.retrieval.search import LiteratureSearch
from src.retrieval.sources.arxiv import ArxivSource
from src.retrieval.sources.openalex import OpenAlexSource
from src.db.session import Database
from src.llm import LlmError

DEFAULT_BUDGET_USD = 12.0  # doc 05 §6, Mode 2
Ask = Callable[[str], str]


class InputError(ValueError):
    """Invalid input, or the database isn't usable."""


def parse_constraint(text: str) -> tuple[str, str]:
    name, separator, value = text.partition("=")
    if not separator or not name.strip() or not value.strip():
        raise argparse.ArgumentTypeError(f"expected name=value, got {text!r}")
    return name.strip(), value.strip()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m scripts.discover", description="Find a research idea.")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("field", nargs="?", help="your field, e.g. NLP, Robotics, Computer Vision")
    which.add_argument("--continue", dest="continue_id", type=uuid.UUID, metavar="INVESTIGATION",
                       help="resume an investigation where it stopped")
    parser.add_argument("--interests", help="interests or subtopics, e.g. 'evaluation of LLM agents'")
    parser.add_argument("--technologies", help="preferred technologies, e.g. 'PyTorch, small language models'")
    parser.add_argument("--exclude", action="append", default=[], metavar="TOPIC",
                        help="a topic or method to avoid (repeatable)")
    parser.add_argument("--domain", help="application domain, e.g. education")
    parser.add_argument("--complexity", choices=COMPLEXITY_LEVELS, default="intermediate")
    parser.add_argument("--constraint", action="append", default=[], type=parse_constraint, metavar="NAME=VALUE",
                        help='a constraint, e.g. "available time=10 weeks" (repeatable)')
    parser.add_argument("--count", type=int, default=3, help="how many research ideas you want (default: 3)")
    parser.add_argument("--budget", type=float, default=None, metavar="USD",
                        help=f"cost cap for the investigation's Claude calls (default: {DEFAULT_BUDGET_USD:g})")
    parser.add_argument("--max-light", type=int, default=None, metavar="N",
                        help="how many candidates get a light evaluation (1-8, default 5; about $1-1.30 each)")
    parser.add_argument("--yes", action="store_true", help="skip the questions and confirm the brief")
    parser.add_argument("--verbose", action="store_true", help="show logs")
    return parser.parse_args(argv)


def investigation_input(args: argparse.Namespace) -> dict:
    field = (args.field or "").strip()
    if not field:
        raise InputError("The field is empty.")
    if not 1 <= args.count <= 10:
        raise InputError("--count must be between 1 and 10.")
    if args.budget is not None and args.budget <= 0:
        raise InputError("--budget must be more than 0.")
    data = {"field": field, "count": args.count}
    for key, value in (("interests", args.interests), ("technologies", args.technologies), ("domain", args.domain)):
        if value and value.strip():
            data[key] = value.strip()
    exclusions = [e.strip() for e in args.exclude if e.strip()]
    if exclusions:
        data["exclusions"] = exclusions
    return data


# --- output -------------------------------------------------------------------------------------------

def format_brief(brief: FieldBriefOutput, *, questions: bool = True) -> str:
    lines = ["Subtopics to explore:"]
    for number, subtopic in enumerate(brief.subtopics, start=1):
        lines.append(f"  {number}. {subtopic.name}")
        lines.append(f"     why it fits you: {subtopic.why_it_fits_you}")
        lines.append(f"     searches: {' | '.join(subtopic.queries)}")
    if brief.assumptions:
        lines.append("\nAssumed:")
        lines.extend(f"  - {item}" for item in brief.assumptions)
    if questions:
        if brief.clarifying_questions:
            lines.append("\nQuestions (Eureka uses the assumption after → if you skip):")
            for number, question in enumerate(brief.clarifying_questions, start=1):
                lines.append(f"  {number}. {question.question}")
                lines.append(f"     → {question.if_skipped}")
        else:
            lines.append("\nQuestions: none.")
    return "\n".join(lines)


# --- clarification (interactive) -----------------------------------------------------------------------

def _ask(ask: Ask, prompt: str) -> str | None:
    try:
        return ask(prompt)
    except EOFError:
        return None


def _paused(investigation_id: uuid.UUID) -> int:
    print(f"\nPaused. Continue with: python -m scripts.discover --continue {investigation_id}")
    return 0


async def clarify_brief(sessions: Sessions, investigation_id: uuid.UUID, *, yes: bool, ask: Ask = input,
                        settings=None, model=None) -> int:
    pending = await load_pending_brief(sessions, investigation_id)
    brief, removed = pending.brief, []
    if yes:
        answers = answers_for(brief, [None] * len(brief.clarifying_questions))
    else:
        replies = []
        for number, question in enumerate(brief.clarifying_questions, start=1):
            print(f"\n{number}. {question.question}")
            reply = _ask(ask, f"   your answer (Enter = skip → {question.if_skipped}): ")
            if reply is None:
                return _paused(investigation_id)
            replies.append(reply)
        answers = answers_for(brief, replies)
        if any(not a.skipped for a in answers):
            print("\nApplying your answers (one Idea Analyst call)...")
        revision = await apply_brief_answers(sessions, pending, answers, settings=settings, model=model)
        if revision.error:
            print(f"Applying your answers failed: {revision.error}")
            print(f"Nothing was lost; try again with: python -m scripts.discover --continue {investigation_id}")
            return 1
        brief = revision.brief
        if revision.revised:
            print("\n" + format_brief(brief, questions=False))
        while True:
            choice = _ask(ask, "\nConfirm these subtopics? [y] confirm  [r] remove some  [q] quit for now: ")
            choice = (choice or "q").strip().lower()
            if choice == "q":
                return _paused(investigation_id)
            if choice == "y":
                break
            if choice == "r":
                numbers = (_ask(ask, "   subtopic numbers to remove (e.g. 2 5): ") or "").split()
                if not all(n.isdigit() for n in numbers) or not numbers:
                    print("   Give subtopic numbers, separated by spaces.")
                    continue
                try:
                    names = [brief.subtopics[int(n) - 1].name for n in numbers if 1 <= int(n) <= len(brief.subtopics)]
                    brief = remove_subtopics(brief, [int(n) for n in numbers])
                except ValueError as error:
                    print(f"   Not changed: {error}")
                    continue
                removed += names
                print("\n" + format_brief(brief, questions=False))
    final = await confirm_brief(sessions, investigation_id, final=brief, answers=answers, removed=removed)
    print(f"\nConfirmed: {len(final.subtopics)} subtopics to explore.")
    return 0


# --- the landscape search -------------------------------------------------------------------------------

def format_landscape(outcome: LandscapeOutcome) -> str:
    kinds = ", ".join(f"{kind} {count}" for kind, count in outcome.by_kind.items())
    lines = [f"  {outcome.searches} searches -> {outcome.retrieved} results ({kinds}) -> "
             f"{outcome.unique_papers} unique papers"
             + (f" | failed source searches: {outcome.failed_source_searches}" if outcome.failed_source_searches else "")]
    lines.append("  Papers per subtopic:")
    lines.extend(f"    {count:>4}  {name}" for name, count in outcome.by_subtopic.items())
    if outcome.most_cited:
        lines.append("  Most cited in the landscape:")
        lines.extend(f"    {paper.citation_count or 0:>6} cites  {paper.title[:80]} ({paper.year or 'n.d.'})"
                     for paper in outcome.most_cited)
    return "\n".join(lines)


async def landscape(sessions: Sessions, investigation_id: uuid.UUID, *, search_for=None) -> int:
    print("\nSearching the landscape: recent work, surveys, highly cited, newest preprints...")
    if search_for is not None:
        outcome = await landscape_step(sessions, investigation_id, search_for)
    else:
        # One client per source, shared by every combination: two arXiv clients each kept their own
        # 3-second spacing and together got rate-limited (step 5.1b live run: 16 of 34 failed).
        cache = DbResponseCache(sessions)
        openalex, arxiv = OpenAlexSource.from_settings(cache=cache), ArxivSource(cache=cache)
        searches = {("openalex", "arxiv"): LiteratureSearch([openalex, arxiv]),
                    ("openalex",): LiteratureSearch([openalex]), ("arxiv",): LiteratureSearch([arxiv])}
        try:
            outcome = await landscape_step(sessions, investigation_id, lambda sources: searches[tuple(sources)])
        finally:
            await openalex.aclose()
            await arxiv.aclose()
    print(format_landscape(outcome))
    if outcome.failed:
        print("  Every search failed on every source: nothing could be looked at.")
        return 1
    return 0


# --- reading the landscape ------------------------------------------------------------------------------

def format_reading(outcome: LandscapeReading) -> str:
    scores = ", ".join(f"{score}: {count}" for score, count in outcome.score_counts.items())
    lines = [f"  Screened {outcome.screened} papers (scores {scores})"
             + (f" | not screened: {outcome.not_screened}" if outcome.not_screened else "")]
    lines.append(f"  Chosen to read: {len(outcome.selected)} "
                 f"({sum(1 for *_, survey in outcome.selected if survey)} surveys)")
    per_subtopic: dict[str, int] = {}
    for _, names, _ in outcome.selected:
        for name in names:
            per_subtopic[name] = per_subtopic.get(name, 0) + 1
    lines.extend(f"    {count:>4}  {name}" for name, count in per_subtopic.items())
    lines.append(f"  Read now: {outcome.read_now} | from the library: {outcome.reused} | failed: {outcome.failed}")
    return "\n".join(lines)


async def over_budget(sessions: Sessions, investigation_id: uuid.UUID, before: str) -> bool:
    async with sessions() as session:
        investigation = await session.get(Investigation, investigation_id)
        if budget_left(investigation):
            return False
        print(f"\nBudget used up: ${investigation.cost_usd:.2f} of ${cost_cap(investigation)}. Stopped before "
              f"{before}. Resume with: python -m scripts.discover --continue {investigation_id} --budget <more>")
        return True


async def read_landscape(sessions: Sessions, investigation_id: uuid.UUID, *, screen_model=None,
                         read_model=None, mine_model=None, scout_model=None) -> int:
    if await over_budget(sessions, investigation_id, "reading the landscape"):
        return 3
    print("\nReading the landscape: screening every paper, then reading the best ~45...")
    outcome = await landscape_reading_step(sessions, investigation_id, screen_model=screen_model,
                                           read_model=read_model)
    print(format_reading(outcome))
    return await mine(sessions, investigation_id, model=mine_model, scout_model=scout_model)


# --- mining the landscape -------------------------------------------------------------------------------

def format_mining(outcome: MiningOutcome) -> str:
    lines = [f"  {outcome.statements} verified statements from {outcome.papers} papers -> {outcome.groups} groups"]
    lines.append(f"  Recurring open problems: {len(outcome.clusters)}")
    lines.extend(f"    {item.counts['papers']:>2} papers, {item.counts['author_groups']} author groups  {item.label}"
                 for item in sorted(outcome.clusters, key=lambda i: -i.counts["papers"]))
    if outcome.dropped:
        lines.append(f"  Not recurring enough (fewer than 3 papers or 2 author groups): {len(outcome.dropped)}")
    lines.append(f"  Contradictions: {len(outcome.contradictions)} of {outcome.pairs_checked} suspected pairs")
    lines.extend(f"    {item.label}" for item in outcome.contradictions)
    lines.append("  Subtopics (landscape papers / read):")
    lines.extend(f"    {item.counts['landscape_papers']:>4} / {item.counts['read']:<3} {item.label}"
                 for item in outcome.subtopics)
    shared = [item for item in outcome.datasets if item.counts["papers"] > 1]
    lines.append(f"  Datasets named: {len(outcome.datasets)}"
                 + (f" (in 2+ papers: {', '.join(item.label for item in shared)})" if shared else ""))
    return "\n".join(lines)


async def mine(sessions: Sessions, investigation_id: uuid.UUID, *, model=None, scout_model=None,
               quick_model=None) -> int:
    if await over_budget(sessions, investigation_id, "mining the landscape"):
        return 3
    print("\nMining the landscape: recurring problems, contradictions, subtopics, datasets...")
    try:
        outcome = await mining_step(sessions, investigation_id, model=model)
    except LlmError as error:
        print(f"  The opportunity miner failed: {error}. Nothing was stored; run --continue to try again.")
        return 1
    print(format_mining(outcome))
    return await scout(sessions, investigation_id, model=scout_model, quick_model=quick_model)


# --- generating candidates ------------------------------------------------------------------------------

async def scout(sessions: Sessions, investigation_id: uuid.UUID, *, model=None, quick_model=None,
                evaluation_models: EvaluationModels | None = None, search_factory=None) -> int:
    """The Scout's first ideas, then pre-screening and the candidate loop (scripts/candidate_loop.py)."""
    return await generate(sessions, investigation_id,
                          LoopModels(evaluation_models, scout_model=model, search_factory=search_factory,
                                     quick_model=quick_model), command="discover")


async def continue_after_brief(sessions: Sessions, investigation_id: uuid.UUID, code: int, *, search_for=None) -> int:
    async with sessions() as session:
        stage = (await session.get(Investigation, investigation_id)).stage
    if code != 0 or stage != Stage.LANDSCAPE_RETRIEVAL:
        return code
    code = await landscape(sessions, investigation_id, search_for=search_for)
    return code if code != 0 else await read_landscape(sessions, investigation_id)


# --- main -----------------------------------------------------------------------------------------------

async def resume(sessions: Sessions, investigation_id: uuid.UUID, *, yes: bool, budget: float | None,
                 max_light: int | None = None) -> int:
    async with sessions() as session:
        investigation = await session.get(Investigation, investigation_id)
        if investigation is not None and budget is not None:
            set_cost_cap(investigation, budget)
        if investigation is not None and max_light is not None:
            set_light_cap(investigation, max_light)
    if investigation is None or investigation.mode != "discover":
        raise InputError(f"No Mode 2 investigation {investigation_id}.")
    print(f"Investigation {investigation_id} | stage {investigation.stage} ({investigation.status})")
    if investigation.stage == Stage.AWAITING_CLARIFICATION:
        try:
            pending = await load_pending_brief(sessions, investigation_id)
        except NotWaitingForClarification as error:
            raise InputError(str(error)) from None
        print("\n" + format_brief(pending.brief))
        code = await clarify_brief(sessions, investigation_id, yes=yes)
        return await continue_after_brief(sessions, investigation_id, code)
    if investigation.stage == Stage.LANDSCAPE_RETRIEVAL:
        code = await landscape(sessions, investigation_id)
        return code if code != 0 else await read_landscape(sessions, investigation_id)
    if investigation.stage == Stage.LANDSCAPE_READING:
        return await read_landscape(sessions, investigation_id)
    if investigation.stage == Stage.OPPORTUNITY_MINING:
        return await mine(sessions, investigation_id)
    if investigation.stage in (Stage.GENERATING_CANDIDATES, Stage.PRESCREENING, Stage.LIGHT_EVALUATION, Stage.TRIAGE, Stage.REFINING, Stage.FULL_EVALUATION,
                               Stage.RANKING):
        return await continue_at(sessions, investigation_id, command="discover")
    raise InputError(f"Investigation {investigation_id} can't be continued from stage {investigation.stage}.")


async def run(args: argparse.Namespace) -> int:
    if args.max_light is not None and not 1 <= args.max_light <= 8:
        raise InputError("--max-light must be 1-8.")
    data = investigation_input(args) if args.continue_id is None else None
    try:
        database = Database.from_settings()
    except ConfigError as error:
        raise InputError(str(error)) from None
    try:
        if not await database.ping():
            raise InputError("The database isn't reachable: start it with `docker compose up -d db`.")
        if args.continue_id is not None:
            return await resume(database.session, args.continue_id, yes=args.yes, budget=args.budget,
                                max_light=args.max_light)
        budget = args.budget if args.budget is not None else DEFAULT_BUDGET_USD
        async with database.session() as session:
            investigation = await create_investigation(session, mode="discover", complexity=args.complexity,
                                                       input=data, constraints=dict(args.constraint))
            set_cost_cap(investigation, budget)
            if args.max_light is not None:
                set_light_cap(investigation, args.max_light)
            investigation_id = investigation.id
        print(f"Investigation {investigation_id} | field {data['field']} | complexity {args.complexity} | "
              f"budget ${budget:g}\n")
        print("Mapping your field (Idea Analyst, one call)...")
        outcome = await understand_field(database.session, investigation_id)
        if outcome.error:
            print(f"The Idea Analyst failed: {outcome.error}")
            return 1
        print("\n" + format_brief(outcome.brief))
        if outcome.problems:
            print("\nNote: some subtopics touch your exclusions; they are removed when you confirm:")
            print("\n".join(f"  - {problem}" for problem in outcome.problems))
        code = await clarify_brief(database.session, investigation_id, yes=args.yes)
        return await continue_after_brief(database.session, investigation_id, code)
    finally:
        await database.dispose()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        return asyncio.run(run(args))
    except InputError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
