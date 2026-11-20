"""Evaluate a research idea (Workflow A) from the command line.

Usage (from the repo root, venv active, database running, Anthropic key in .env):
    python -m scripts.evaluate "use RAG to reduce hallucinations of small LMs for Georgian QA"
    python -m scripts.evaluate "..." --field NLP --complexity beginner --seed 2401.12345 \\
        --constraint "available time=8 weeks" --constraint "compute=laptop only"
    python -m scripts.evaluate --continue <investigation id>     resume at the clarification
    python -m scripts.evaluate "..." --yes                       skip the questions

So far (steps 4.2-4.4):
  1. understanding: the Idea Analyst's reading of the idea (facets, assumptions,
     vagueness flags, clarifying questions), stored as the investigation's Intake;
  2. clarification: you answer the questions (Enter skips one), may edit facets, and
     confirm. Answers are applied by one more Idea Analyst call. Your idea becomes
     Candidate v1 and the investigation moves to full_evaluation;
  3. the full evaluation begins: the Idea Analyst plans 15-20 searches, which run on
     arXiv and OpenAlex (responses cached 7 days); the papers found are stored;
  4. screening: every paper found is scored 0-3 for relevance (by the model in
     EUREKA_LLM_MODEL_RELEVANCE_SCREENING, e.g. Haiku: a few cents for ~600 papers);
  5. reading: the most relevant papers (scores 3, then 2; at most --max-read, default
     25) are read by the Paper Reader with quote verification (about 3.4 cents each on
     Opus; papers already in the library are free).
If Eureka had to propose a KEY facet, you must confirm the reading yourself: --yes is
refused. Quitting at the confirmation keeps everything; resume with --continue.
Later steps extend this until the whole evaluation runs (step 4.8).

This calls the paid Anthropic API (one Opus call, plus one if you answer questions,
plus one or two for the query plan; roughly 4-5 cents each; screening and reading
as above, so a first-time full evaluation costs up to about $1) and the free arXiv and
OpenAlex APIs (a small share of OpenAlex's daily allowance).
Exit code: 0 ok, 1 an Idea Analyst call failed, 2 invalid input, a confirmation that
--yes can't give, or an unavailable database.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from collections.abc import Callable

from src.ai.schemas.structured_idea import StructuredIdeaOutput
from src.ai.workflows.clarification import (
    ConfirmationRequired,
    NotWaitingForClarification,
    Sessions,
    answers_for,
    apply_answers,
    apply_edits,
    confirm_idea,
    load_pending,
)
from src.ai.schemas.query_plan import QueryPlanOutput
from src.ai.workflows.evaluation import (
    begin_evaluation,
    plan_step,
    read_step,
    retrieve_step,
    screen_step,
    selected_papers,
)
from src.ai.workflows.states import EvaluationStep, Stage
from src.ai.workflows.understanding import understand_idea
from src.config import ConfigError
from src.db.models import Evaluation, Investigation
from src.db.repositories.cache import DbResponseCache
from src.db.repositories.candidates import investigation_candidates
from src.db.repositories.evaluations import latest_evaluation, top_hits
from src.db.models.run import COMPLEXITY_LEVELS
from src.db.repositories.runs import create_investigation
from src.retrieval.search import LiteratureSearch
from src.db.session import Database

MAX_IDEA_CHARS = 2000  # doc 02 §2.1


class InputError(ValueError):
    """Invalid input, or the database isn't usable."""


def parse_constraint(text: str) -> tuple[str, str]:
    name, separator, value = text.partition("=")
    if not separator or not name.strip() or not value.strip():
        raise argparse.ArgumentTypeError(f"expected name=value, got {text!r}")
    return name.strip(), value.strip()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m scripts.evaluate", description="Evaluate a research idea.")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("idea", nargs="?", help=f"the idea in your own words (up to {MAX_IDEA_CHARS} characters)")
    which.add_argument("--continue", dest="continue_id", type=uuid.UUID, metavar="INVESTIGATION",
                       help="resume an investigation waiting for clarification")
    parser.add_argument("--field", help="field or subfield, e.g. NLP (inferred if missing)")
    parser.add_argument("--complexity", choices=COMPLEXITY_LEVELS, default="intermediate",
                        help="desired ambition (default: intermediate)")
    parser.add_argument("--seed", action="append", default=[], metavar="PAPER",
                        help="a paper you already know: DOI, arXiv ID or title (repeatable)")
    parser.add_argument("--constraint", action="append", default=[], type=parse_constraint, metavar="NAME=VALUE",
                        help='a constraint, e.g. "available time=8 weeks" (repeatable)')
    parser.add_argument("--max-read", type=int, default=None, metavar="N",
                        help="papers the Paper Reader reads at most (default: 25 for a full evaluation)")
    parser.add_argument("--yes", action="store_true",
                        help="skip the questions and confirm (refused if Eureka proposed a key facet)")
    parser.add_argument("--verbose", action="store_true", help="show logs")
    return parser.parse_args(argv)


def check_max_read(args: argparse.Namespace) -> None:
    if args.max_read is not None and not 0 <= args.max_read <= 100:
        raise InputError("--max-read must be between 0 and 100.")


def investigation_input(args: argparse.Namespace) -> dict:
    idea = args.idea.strip()
    if not idea:
        raise InputError("The idea is empty.")
    if len(idea) > MAX_IDEA_CHARS:
        raise InputError(f"The idea is {len(idea)} characters; the limit is {MAX_IDEA_CHARS}.")
    data = {"idea": idea}
    if args.field:
        data["field"] = args.field.strip()
    if args.seed:
        data["seed_papers"] = [seed.strip() for seed in args.seed if seed.strip()]
    return data


# --- output -------------------------------------------------------------------------------------------

def format_reading(reading: StructuredIdeaOutput, *, questions: bool = True) -> str:
    """The reading as text. questions=False for a revised reading (the user has already answered)."""
    lines = [f"Title:  {reading.title}", f"RQ:     {reading.research_question}", "",
             "Facets (* = key, [proposed] = Eureka's suggestion, not in your idea):", format_facet_list(reading)]
    for title, items in (("Assumed", reading.assumptions), ("Vague", reading.vagueness_flags)):
        if items:
            lines.append("")
            lines.append(f"{title}:")
            lines.extend(f"  - {item}" for item in items)
    if reading.proposed_key_facets():
        lines.append("")
        lines.append("Key facets Eureka proposed: you'll be asked to confirm these before the search.")
    if not questions:
        return "\n".join(lines)
    lines.append("")
    if reading.clarifying_questions:
        lines.append("Questions (Eureka uses the assumption after → if you skip):")
        for number, question in enumerate(reading.clarifying_questions, start=1):
            lines.append(f"  {number}. {question.question}")
            lines.append(f"     → {question.if_skipped}")
    else:
        lines.append("Questions: none, the idea is clear enough.")
    return "\n".join(lines)


def format_facet_list(reading: StructuredIdeaOutput) -> str:
    lines = []
    for number, facet in enumerate(reading.facets, start=1):
        proposed = "" if facet.stated_by_user else "  [proposed]"
        key = "*" if facet.is_key else " "
        lines.append(f"  {number}. {facet.kind.replace('_', '-'):<13}{key} {facet.text}{proposed}")
    return "\n".join(lines)


# --- clarification (interactive) ----------------------------------------------------------------------

Ask = Callable[[str], str]


def _ask(ask: Ask, prompt: str) -> str | None:
    """The user's reply, or None if input ended (Ctrl+Z / Ctrl+D, or no terminal)."""
    try:
        return ask(prompt)
    except EOFError:
        return None


def _paused(investigation_id: uuid.UUID) -> int:
    print(f"\nPaused at the clarification. Continue with: python -m scripts.evaluate --continue {investigation_id}")
    return 0


async def clarify(sessions: Sessions, investigation_id: uuid.UUID, *, yes: bool, ask: Ask = input,
                  settings=None, model=None) -> int:
    """Questions -> answers applied -> optional facet edits -> confirmation -> Candidate v1."""
    pending = await load_pending(sessions, investigation_id)
    reading, edits = pending.reading, {}

    if yes:
        answers = answers_for(reading, [None] * len(reading.clarifying_questions))
    else:
        replies = []
        for number, question in enumerate(reading.clarifying_questions, start=1):
            print(f"\n{number}. {question.question}")
            reply = _ask(ask, f"   your answer (Enter = skip → {question.if_skipped}): ")
            if reply is None:
                return _paused(investigation_id)
            replies.append(reply)
        answers = answers_for(reading, replies)
        if any(not answer.skipped for answer in answers):
            print("\nApplying your answers (one Idea Analyst call)...")
        outcome = await apply_answers(sessions, pending, answers, settings=settings, model=model)
        if outcome.error:
            print(f"Applying your answers failed: {outcome.error}")
            print(f"Nothing was lost; try again with: python -m scripts.evaluate --continue {investigation_id}")
            return 1
        reading = outcome.reading
        if outcome.revised:
            print("\n" + format_reading(reading, questions=False))

        while True:
            print("\nFacets (* = key):\n" + format_facet_list(reading))
            choice = _ask(ask, "\nConfirm this reading? [y] confirm  [e] edit a facet  [q] quit for now: ")
            choice = (choice or "q").strip().lower()
            if choice == "q":
                return _paused(investigation_id)
            if choice == "y":
                break
            if choice == "e":
                number = (_ask(ask, "   facet number: ") or "").strip()
                text = (_ask(ask, "   new text: ") or "").strip()
                if not number.isdigit() or not text:
                    print("   Give a facet number and the new text.")
                    continue
                try:
                    reading = apply_edits(reading, {int(number) - 1: text})
                except ValueError as error:
                    print(f"   Not changed: {str(error).splitlines()[0]}")
                    continue
                edits[int(number) - 1] = text

    try:
        candidate_id = await confirm_idea(sessions, investigation_id, final=reading, answers=answers, edits=edits,
                                          explicit=not yes)
    except ConfirmationRequired as error:
        print(f"\n{error}")
        print(f"Confirm it yourself: python -m scripts.evaluate --continue {investigation_id}")
        return 2
    print(f"\nConfirmed: your idea is candidate {candidate_id} (v1).")
    return 0


# --- the evaluation -------------------------------------------------------------------------------------

TITLE_WIDTH = 80


def format_plan(plan: QueryPlanOutput) -> str:
    lines = [f"Search plan: {len(plan.queries)} queries"]
    for query in plan.queries:
        since = f", from {query.year_from}" if query.year_from else ""
        facets = ",".join(str(n) for n in query.facets)
        lines.append(f"  {query.kind:<20} {query.text}   (facets {facets}{since})")
    return "\n".join(lines)


def _short(text: str, width: int = TITLE_WIDTH) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


async def evaluate(sessions: Sessions, investigation_id: uuid.UUID, *, max_read: int | None = None,
                   search_factory=None, settings=None, model=None, screen_settings=None, screen_model=None,
                   read_settings=None, read_model=None) -> int:
    """Runs the full evaluation of the investigation's confirmed idea, from wherever it stands."""
    async with sessions() as session:
        candidates = [c for c in await investigation_candidates(session, investigation_id)
                      if c.status == "full_evaluating"]
        if not candidates:
            print("No candidate is waiting for a full evaluation.")
            return 2
        candidate_id = candidates[0].id
        evaluation = await latest_evaluation(session, candidate_id)
        evaluation_id = evaluation.id if evaluation is not None and evaluation.status == "running" else None
    if evaluation_id is None:
        evaluation_id = await begin_evaluation(sessions, candidate_id, depth="full")
        print(f"\nFull evaluation {evaluation_id} started.")

    while True:
        async with sessions() as session:
            evaluation = await session.get(Evaluation, evaluation_id)
            await session.refresh(evaluation)
            step, status = evaluation.step, evaluation.status
        if status != "running":
            print(f"\nThe evaluation ended: {status} (at {step}).")
            return 1 if status == "failed" else 0

        if step == EvaluationStep.PLANNING_QUERIES:
            print("\nPlanning the searches (one Idea Analyst call)...")
            outcome = await plan_step(sessions, evaluation_id, settings=settings, model=model)
            if outcome.regenerated:
                print("  (the first plan missed a quota and was rewritten once)")
            if outcome.fallback_reason:
                print(f"  The Idea Analyst failed ({outcome.fallback_reason}); using a simple plan from the facets.")
            for problem in outcome.problems:
                print(f"  quota still missed: {problem}")
            print(format_plan(outcome.plan))
        elif step == EvaluationStep.RETRIEVING:
            async with sessions() as session:
                planned = len((await session.get(Evaluation, evaluation_id)).query_plan["queries"])
            print(f"\nSearching arXiv and OpenAlex ({planned} queries)...")
            factory = search_factory or (lambda: LiteratureSearch.from_settings(cache=DbResponseCache(sessions)))
            async with factory() as search:
                outcome = await retrieve_step(sessions, evaluation_id, search)
            print(f"  {outcome.searches} searches -> {outcome.retrieved} results -> {outcome.unique_papers} unique papers"
                  + (f" | failed source searches: {outcome.failed_source_searches}"
                     if outcome.failed_source_searches else ""))
            if outcome.failed:
                print("  Every search failed on every source: nothing could be looked at.")
                return 1
            async with sessions() as session:
                top = await top_hits(session, evaluation_id, limit=10)
            print("\nMost-found papers (a rough order; screening comes next):")
            for number, (paper, hit) in enumerate(top, start=1):
                found = len(hit.found_by_query_ids)
                print(f"  {number:>2}. {_short(paper.title)} ({paper.year or 'n.d.'}) | found {found} times")
        elif step == EvaluationStep.SCREENING:
            print("\nScreening the papers for relevance (relevance screener, batches of 25)...")
            outcome = await screen_step(sessions, evaluation_id, max_read=max_read, settings=screen_settings,
                                        model=screen_model)
            counts = outcome.score_counts
            print(f"  scores: 3 (direct prior work) {counts[3]} | 2 (shares a key facet) {counts[2]} | "
                  f"1 {counts[1]} | 0 {counts[0]}")
            if outcome.not_screened:
                print(f"  not screened (failed batches): {outcome.not_screened}")
            more = (f" ({outcome.relevant_not_selected} more relevant papers over the limit; raise it with --max-read)"
                    if outcome.relevant_not_selected else "")
            print(f"  selected for reading: {outcome.selected}{more}")
        elif step == EvaluationStep.READING:
            async with sessions() as session:
                chosen = await selected_papers(session, evaluation_id)
            print(f"\nReading the {len(chosen)} selected papers (Paper Reader; papers already in the library are free)...")
            outcome = await read_step(sessions, evaluation_id, settings=read_settings, model=read_model)
            for number, item in enumerate(outcome.papers, start=1):
                result = item.outcome
                status = (f"FAILED: {result.error}" if result.error
                          else f"{'reused' if result.reused else 'read'}, quotes verified {result.verified_quotes}/{result.quotes}")
                print(f"  {number:>2}. [{item.hit.llm_score}] {_short(item.paper.title, 70)} ({item.paper.year or 'n.d.'})")
                print(f"      {item.hit.llm_reason} | {status}")
            async with sessions() as session:
                cost = (await session.get(Investigation, investigation_id)).cost_usd
            print(f"  {outcome.read_now} read now, {outcome.reused} reused, {outcome.failed} failed | "
                  f"cost so far: ${cost:.4f}")
        else:
            print(f"\nNext: the novelty assessment comes with step 4.6 (now at {step}).")
            return 0


# --- main -----------------------------------------------------------------------------------------------

async def run(args: argparse.Namespace) -> int:
    check_max_read(args)
    data = investigation_input(args) if args.continue_id is None else None
    try:
        database = Database.from_settings()
    except ConfigError as error:
        raise InputError(str(error)) from None
    try:
        if not await database.ping():
            raise InputError("The database isn't reachable: start it with `docker compose up -d db`.")
        if args.continue_id is not None:
            return await resume(database.session, args.continue_id, yes=args.yes, max_read=args.max_read)

        async with database.session() as session:
            investigation = await create_investigation(session, mode="evaluate", complexity=args.complexity,
                                                       input=data, constraints=dict(args.constraint))
            investigation_id = investigation.id
        print(f"Investigation {investigation_id} | complexity {args.complexity}\n")

        outcome = await understand_idea(database.session, investigation_id)

        async with database.session() as session:
            stored = await session.get(Investigation, investigation_id)
            stage, status, cost = stored.stage, stored.status, stored.cost_usd
        if outcome.error:
            print(f"The Idea Analyst failed: {outcome.error}")
        else:
            print(format_reading(outcome.reading))
        print(f"\nStage: {stage} ({status}) | cost so far: ${cost:.4f}")
        if outcome.error:
            return 1
        code = await clarify(database.session, investigation_id, yes=args.yes)
        return await evaluate_if_confirmed(database.session, investigation_id, code, max_read=args.max_read)
    finally:
        await database.dispose()


async def evaluate_if_confirmed(sessions: Sessions, investigation_id: uuid.UUID, code: int, *,
                                max_read: int | None = None) -> int:
    async with sessions() as session:
        stage = (await session.get(Investigation, investigation_id)).stage
    if code != 0 or stage != Stage.FULL_EVALUATION:
        return code
    return await evaluate(sessions, investigation_id, max_read=max_read)


async def resume(sessions: Sessions, investigation_id: uuid.UUID, *, yes: bool, max_read: int | None = None) -> int:
    """--continue: picks the investigation up where it stopped."""
    async with sessions() as session:
        investigation = await session.get(Investigation, investigation_id)
    if investigation is None:
        raise InputError(f"No investigation {investigation_id}.")
    print(f"Investigation {investigation_id} | stage {investigation.stage} ({investigation.status})")
    if investigation.stage == Stage.AWAITING_CLARIFICATION:
        try:
            pending = await load_pending(sessions, investigation_id)
        except NotWaitingForClarification as error:
            raise InputError(str(error)) from None
        print(f"\n{format_reading(pending.reading)}")
        code = await clarify(sessions, investigation_id, yes=yes)
        return await evaluate_if_confirmed(sessions, investigation_id, code, max_read=max_read)
    if investigation.stage == Stage.FULL_EVALUATION and investigation.status == "running":
        return await evaluate(sessions, investigation_id, max_read=max_read)
    raise InputError(f"Investigation {investigation_id} can't be continued from stage {investigation.stage} "
                     f"({investigation.status}).")


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
