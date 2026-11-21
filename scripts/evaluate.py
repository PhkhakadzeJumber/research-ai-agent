"""Evaluate a research idea (Workflow A) from the command line.

Usage (from the repo root, venv active, database running, Anthropic key in .env):
    python -m scripts.evaluate "use RAG to reduce hallucinations of small LMs for Georgian QA"
    python -m scripts.evaluate "..." --field NLP --complexity beginner --seed 2401.12345 \\
        --constraint "available time=8 weeks" --constraint "compute=laptop only"
    python -m scripts.evaluate --continue <investigation id>     resume at the clarification
    python -m scripts.evaluate "..." --yes                       skip the questions

The whole workflow (steps 4.2-4.9, with 5.5-5.6 for what follows a verdict):
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
     30) are read by the Paper Reader with quote verification (about 3.4 cents each on
     Opus; papers already in the library are free);
  6. novelty: the Novelty Investigator compares the idea with the 12 closest read papers
     facet by facet (one Opus call, about 25 cents); code verifies its quotes and the
     verdict engine gives a preliminary novelty verdict with the coverage snapshot;
  7. Skeptic rounds: the Skeptic plans adversarial searches, code runs them, screens and
     reads up to 5 new relevant papers, and the Skeptic challenges the analysis with
     quotes from them (about 35 cents a round); the Novelty Investigator then accepts or
     rebuts each challenge with quoted evidence (about 30 cents). Rounds repeat until one
     finds no new relevant papers (saturation) or 3 rounds are done;
  8. value and feasibility: one Opus call (about 30 cents) judges significance and
     feasibility for you; code checks datasets against the papers read and the timeline
     against your available time, then shows every verdict, the recommendation and the
     triage rule;
  9. concluding: the coverage and the verdicts are frozen, triage decides (finalist,
     rejected or refined), and then the candidate loop (scripts/candidate_loop.py): a refinement
     gets its next version and a light re-evaluation; a rejected idea gets 3-5 alternatives, which
     are pre-screened and light-evaluated; the best that advance get a full evaluation; then the
     investigation completes.
     The full report: python -m scripts.report <investigation id> [--out report.md].
Budget: --budget (default $5) caps the investigation's Claude cost. The Skeptic loop stops
at the cap, and the command stops before any further paid step (resume with --continue and
a higher --budget).
If Eureka had to propose a KEY facet, you must confirm the reading yourself: --yes is
refused. Quitting at the confirmation keeps everything; resume with --continue.

This calls the paid Anthropic API (one Opus call, plus one if you answer questions,
plus one or two for the query plan; roughly 4-5 cents each; screening and reading
as above, so a first-time full evaluation costs up to about $1) and the free arXiv and
OpenAlex APIs (a small share of OpenAlex's daily allowance).
Exit code: 0 ok, 1 an Idea Analyst call failed, 2 invalid input, a confirmation that
--yes can't give, or an unavailable database, 3 the budget is used up.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

from src.ai.schemas.structured_idea import StructuredIdeaOutput
from src.ai.workflows.budget import DEFAULT_COST_CAP_USD, cost_cap, set_cost_cap
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
from sqlalchemy import select

from src.ai.evidence.verdicts import LABELS
from src.ai.schemas.query_plan import QueryPlanOutput
from src.ai.workflows.evaluation import begin_evaluation, novelty_verdict_for, selected_papers
from src.ai.workflows.concluding import finish_investigation
from src.ai.workflows.runner import EvaluationModels, run_evaluation
from scripts.candidate_loop import LoopModels, continue_at
from src.ai.workflows.states import EvaluationStep, Stage
from src.ai.workflows.understanding import understand_idea
from src.ai.workflows.user_document import DocumentError, attach_document, prepare_document, understand_document
from src.timeutil import now
from src.config import ConfigError
from src.db.models import Evaluation, Facet, Investigation
from src.db.repositories.candidates import investigation_candidates
from src.db.repositories.evaluations import latest_evaluation, top_hits
from src.db.models.run import COMPLEXITY_LEVELS
from src.db.repositories.runs import create_investigation
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
    which = parser.add_mutually_exclusive_group()
    which.add_argument("idea", nargs="?", help=f"the idea in your own words (up to {MAX_IDEA_CHARS} characters); "
                                               "optional with --idea-file")
    which.add_argument("--continue", dest="continue_id", type=uuid.UUID, metavar="INVESTIGATION",
                       help="resume an investigation waiting for clarification")
    parser.add_argument("--idea-file", type=Path, metavar="FILE",
                        help="your own draft, proposal or paper to evaluate (.md or .txt, up to about 15-20 pages); "
                             "it is sent to Claude to be read, and stays private to this investigation")
    parser.add_argument("--consent", action="store_true",
                        help="agree to send --idea-file to Claude (needed with --yes; otherwise you are asked)")
    parser.add_argument("--published", action="store_true",
                        help="the document is already published or on arXiv (your own paper is then never counted "
                             "as prior work against you)")
    parser.add_argument("--doi", help="the published document's DOI")
    parser.add_argument("--arxiv", help="the published document's arXiv ID")
    parser.add_argument("--field", help="field or subfield, e.g. NLP (inferred if missing)")
    parser.add_argument("--complexity", choices=COMPLEXITY_LEVELS, default="intermediate",
                        help="desired ambition (default: intermediate)")
    parser.add_argument("--seed", action="append", default=[], metavar="PAPER",
                        help="a paper you already know: DOI, arXiv ID or title (repeatable)")
    parser.add_argument("--constraint", action="append", default=[], type=parse_constraint, metavar="NAME=VALUE",
                        help='a constraint, e.g. "available time=8 weeks" (repeatable)')
    parser.add_argument("--budget", type=float, default=None, metavar="USD",
                        help="cost cap for the investigation's Claude calls (default: 5; with --continue, "
                             "changes the stored cap)")
    parser.add_argument("--max-read", type=int, default=None, metavar="N",
                        help="papers the Paper Reader reads at most (default: 25 for a full evaluation)")
    parser.add_argument("--yes", action="store_true",
                        help="skip the questions and confirm (refused if Eureka proposed a key facet)")
    parser.add_argument("--verbose", action="store_true", help="show logs")
    return parser.parse_args(argv)


def check_max_read(args: argparse.Namespace) -> None:
    if args.max_read is not None and not 0 <= args.max_read <= 100:
        raise InputError("--max-read must be between 0 and 100.")
    if args.budget is not None and args.budget <= 0:
        raise InputError("--budget must be more than 0.")


def investigation_input(args: argparse.Namespace) -> dict:
    idea = (args.idea or "").strip()
    if not idea and not args.idea_file:
        raise InputError("The idea is empty: give it as text, or your document with --idea-file.")
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
                   read_settings=None, read_model=None, novelty_settings=None, novelty_model=None,
                   skeptic_settings=None, skeptic_model=None, value_settings=None, value_model=None,
                   scout_model=None) -> int:
    """Runs the full evaluation of the investigation's confirmed idea, from wherever it stands."""
    async with sessions() as session:
        # this path is the user's own idea (v1); later full evaluations (a refined version or an alternative
        # that advanced) run in the candidate loop (scripts/candidate_loop.py, step 5.6a)
        candidates = [c for c in await investigation_candidates(session, investigation_id)
                      if c.status == "full_evaluating" and c.origin == "user" and c.version == 1]
        if not candidates:  # e.g. a refined version waiting in the candidate loop
            return await continue_at(sessions, investigation_id, command="evaluate")
        candidate_id = candidates[0].id
        evaluation = await latest_evaluation(session, candidate_id)
        evaluation_id = evaluation.id if evaluation is not None and evaluation.status == "running" else None
    if evaluation_id is None:
        evaluation_id = await begin_evaluation(sessions, candidate_id, depth="full")
        print(f"\nFull evaluation {evaluation_id} started.")

    models = EvaluationModels(
        max_read=max_read, settings=settings, model=model, screen_settings=screen_settings, screen_model=screen_model,
        read_settings=read_settings, read_model=read_model, novelty_settings=novelty_settings,
        novelty_model=novelty_model, skeptic_settings=skeptic_settings, skeptic_model=skeptic_model,
        value_settings=value_settings, value_model=value_model)

    async def report(step: str, phase: str, outcome) -> None:
        if phase == "start":
            await _report_start(sessions, evaluation_id, step)
        else:
            await _report_done(sessions, evaluation_id, investigation_id, step, outcome)

    result = await run_evaluation(sessions, evaluation_id, investigation_id, models=models,
                                  search_factory=search_factory, report=report)
    if result.ended == "failed":
        print(f"\nThe evaluation failed (at {result.step}).")
        return 1
    if result.ended == "retrieval_failed":
        return 1
    if result.ended == "budget":
        async with sessions() as session:
            investigation = await session.get(Investigation, investigation_id)
        print(f"\nBudget used up: ${investigation.cost_usd:.2f} of ${cost_cap(investigation)}. Stopped "
              f"before {result.step}. Resume with: python -m scripts.evaluate --continue {investigation_id} "
              "--budget <more>")
        return 3
    return await finish(sessions, investigation_id, result.ended,
                        LoopModels(models, scout_model=scout_model, search_factory=search_factory))


async def _report_start(sessions: Sessions, evaluation_id: uuid.UUID, step: str) -> None:
    if step == EvaluationStep.PLANNING_QUERIES:
        print("\nPlanning the searches (one Idea Analyst call)...")
    elif step == EvaluationStep.RETRIEVING:
        async with sessions() as session:
            planned = len((await session.get(Evaluation, evaluation_id)).query_plan["queries"])
        print(f"\nSearching arXiv and OpenAlex ({planned} queries)...")
    elif step == EvaluationStep.SCREENING:
        print("\nScreening the papers for relevance (relevance screener, batches of 25)...")
    elif step == EvaluationStep.READING:
        async with sessions() as session:
            chosen = await selected_papers(session, evaluation_id)
        print(f"\nReading the {len(chosen)} selected papers (Paper Reader; papers already in the library are free)...")
    elif step == EvaluationStep.ASSESSING_NOVELTY:
        print("\nComparing the idea with the closest papers (Novelty Investigator, one call)...")
    elif step == EvaluationStep.SKEPTIC_REVIEW:
        print("\nSkeptic round: searching for evidence against the idea...")
    elif step == EvaluationStep.REVISING_NOVELTY:
        print("\nThe Novelty Investigator answers the challenges...")
    elif step == EvaluationStep.ASSESSING_VALUE_FEASIBILITY:
        print("\nJudging significance and feasibility for you (Value & Feasibility Evaluator, one call)...")
    elif step == EvaluationStep.CONCLUDING:
        print("\nConcluding: freezing the coverage and the verdicts...")


async def _report_done(sessions: Sessions, evaluation_id: uuid.UUID, investigation_id: uuid.UUID, step: str,
                       outcome) -> None:
    if step == EvaluationStep.PLANNING_QUERIES:
        if outcome.regenerated:
            print("  (the first plan missed a quota and was rewritten once)")
        if outcome.fallback_reason:
            print(f"  The Idea Analyst failed ({outcome.fallback_reason}); using a simple plan from the facets.")
        for problem in outcome.problems:
            print(f"  quota still missed: {problem}")
        print(format_plan(outcome.plan))
    elif step == EvaluationStep.RETRIEVING:
        print(f"  {outcome.searches} searches -> {outcome.retrieved} results -> {outcome.unique_papers} unique papers"
              + (f" | failed source searches: {outcome.failed_source_searches}"
                 if outcome.failed_source_searches else ""))
        if outcome.failed:
            print("  Every search failed on every source: nothing could be looked at.")
            return
        async with sessions() as session:
            top = await top_hits(session, evaluation_id, limit=10)
        print("\nMost-found papers (a rough order; screening comes next):")
        for number, (paper, hit) in enumerate(top, start=1):
            found = len(hit.found_by_query_ids)
            print(f"  {number:>2}. {_short(paper.title)} ({paper.year or 'n.d.'}) | found {found} times")
    elif step == EvaluationStep.SCREENING:
        counts = outcome.score_counts
        print(f"  scores: 3 (direct prior work) {counts[3]} | 2 (shares a key facet) {counts[2]} | "
              f"1 {counts[1]} | 0 {counts[0]}")
        if outcome.not_screened:
            print(f"  not screened (failed batches): {outcome.not_screened}")
        more = (f" ({outcome.relevant_not_selected} more relevant papers over the limit; raise it with --max-read)"
                if outcome.relevant_not_selected else "")
        print(f"  selected for reading: {outcome.selected}{more}")
        if outcome.relevant_unreadable:
            print(f"  relevant but unreadable (no abstract or full text): {outcome.relevant_unreadable}")
    elif step == EvaluationStep.READING:
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
    elif step == EvaluationStep.ASSESSING_NOVELTY:
        if outcome.error:
            print(f"  {outcome.error}")
        else:
            async with sessions() as session:
                facets = list((await session.execute(
                    select(Facet).join(Evaluation, Evaluation.candidate_id == Facet.candidate_id)
                    .where(Evaluation.id == evaluation_id).order_by(Facet.position)
                )).scalars())
            print(format_novelty(outcome, facets))
        async with sessions() as session:
            verdict, coverage = await novelty_verdict_for(session, evaluation_id)
        print("\n" + format_verdict(verdict, coverage))
    elif step == EvaluationStep.SKEPTIC_REVIEW:
        print(format_round(outcome))
    elif step == EvaluationStep.REVISING_NOVELTY:
        print(format_revision(outcome))
        if outcome.next_step == EvaluationStep.SKEPTIC_REVIEW:  # otherwise the final summary shows it
            async with sessions() as session:
                verdict, coverage = await novelty_verdict_for(session, evaluation_id)
            print("\n" + format_verdict(verdict, coverage))
    elif step == EvaluationStep.ASSESSING_VALUE_FEASIBILITY:
        print(format_value(outcome))
    elif step == EvaluationStep.CONCLUDING:
        print("\n" + format_verdict(outcome.novelty, outcome.coverage, final=True))
        print(format_all_verdicts(outcome, final=True))


OVERLAP_MARKS = {"same": "=", "similar": "~", "different": "x", "not_addressed": ".", "unknown": "?"}


def format_novelty(outcome, facets) -> str:
    """The prior-work matrix, the closest works and the claims, as the Novelty Investigator left them."""
    reading, papers = outcome.reading, outcome.papers
    keys = ",".join(str(f.position + 1) for f in facets if f.is_key)
    numbers = "".join(f"{f.position + 1:>4}" for f in facets)
    lines = ["Prior-work matrix  (= same, ~ similar, x different, . not addressed, ? unknown;",
             f"                    a verified quote: +, unverified: !)   key facets: {keys}",
             f"{'':>42}{numbers}"]
    cells = {(m.paper, m.facet): m for m in reading.matches}
    for label, paper in papers.items():
        row = ""
        for facet in facets:
            match = cells.get((label, facet.position + 1))
            if match is None:
                row += "   -"
                continue
            mark = OVERLAP_MARKS[match.overlap]
            check = "" if match.quote is None else ("+" if match.quote.verified else "!")
            row += f"{mark + check:>4}"
        lines.append(f"  {label:>3} {_short(paper.title, 36):<37}{row}")

    if reading.output.closest_works:
        lines.append("\nClosest works:")
        for work in reading.output.closest_works:
            if work.paper not in papers:
                continue
            minor = "  [differs only in a minor facet]" if work.minor_difference_only else ""
            lines.append(f"  {work.paper} {_short(papers[work.paper].title, 70)}{minor}")
            lines.append(f"      did: {work.what_they_did}")
            lines.append(f"      the idea differs: {work.how_the_idea_differs}")
    if reading.claims:
        lines.append("\nClaims (status set by code):")
        for claim in reading.claims:
            inferred = ", inferred" if claim.inferred else ""
            lines.append(f"  [{claim.status}] {claim.kind}{inferred}: {claim.text}")
    if reading.output.uncertainties:
        lines.append("\nUncertainties:")
        lines.extend(f"  - {item}" for item in reading.output.uncertainties)
    return "\n".join(lines)


def format_round(outcome) -> str:
    lines = [f"Round {outcome.round}"]
    if outcome.plan is not None:
        lines.append("  Attack searches:")
        lines.extend(f"    {q.angle:<22} {q.text}   ({q.why})" for q in outcome.plan.output.queries)
    lines.append(f"  {outcome.searches} searches -> {outcome.new_papers} papers new to this evaluation -> "
                 f"{outcome.new_relevant} relevant -> {len(outcome.read)} read")
    for paper, result in outcome.read:
        status = f"FAILED: {result.error}" if result.error else f"quotes verified {result.verified_quotes}/{result.quotes}"
        lines.append(f"    {_short(paper.title, 70)} ({paper.year or 'n.d.'}) | {status}")
    if outcome.challenges:
        lines.append("  Challenges (+ verified quote, ! unverified):")
        for challenge in outcome.challenges:
            target = f" -> {challenge.target_claim}" if challenge.target_claim else ""
            marks = "".join("+" if quote.verified else "!" for _, quote in challenge.evidence)
            kind = "evidence" if challenge.statement_type == "evidence" else "interpretation"
            same = f" | same on facets {challenge.same_on_facets}" if challenge.same_on_facets else ""
            lines.append(f"    [{challenge.type}{target}, {kind} {marks}] {challenge.argument}{same}")
    elif outcome.error is None:
        lines.append("  No challenges: the new papers don't damage the analysis.")
    if outcome.error:
        lines.append(f"  {outcome.error}")
    lines.append(f"  Saturation statistic: {outcome.new_relevant} new relevant papers"
                 + (" (saturated)" if outcome.saturated else ""))
    return "\n".join(lines)


def format_revision(outcome) -> str:
    lines = []
    if outcome.error:
        lines.append(f"  {outcome.error}")
    if outcome.new_cells:
        lines.append(f"  {outcome.new_cells} matrix cells added for the papers the Skeptic found")
    responses = {r.challenge: r for r in (outcome.reading.responses if outcome.reading else [])}
    for label, challenge in outcome.challenges.items():
        resolution = outcome.resolutions.get(label, "unresolved")
        lines.append(f"  {label} [{challenge.type}] -> {resolution}: {_short(challenge.argument, 90)}")
        response = responses.get(label)
        if response is not None and response.rebuttal is not None:
            holds = "verified" if response.rebuttal_holds else "NOT verified: the challenge stays open"
            lines.append(f"      rebuttal ({holds}): {response.rebuttal.text}")
    next_label = "another Skeptic round" if outcome.next_step == "skeptic_review" else "value and feasibility"
    lines.append(f"  Next: {next_label}")
    return "\n".join(lines)


def format_value(outcome) -> str:
    if outcome.error:
        return f"  {outcome.error}"
    output = outcome.reading.output
    sig, feas = output.significance, output.feasibility
    lines = ["Significance:"]
    for claim in outcome.reading.claims:
        marks = "".join("+" if quote.verified else "!" for _, quote in claim.evidence)
        lines.append(f"  [{claim.status} {marks}] {claim.text}")
    if sig.beneficiaries:
        lines.append(f"  who benefits: {'; '.join(sig.beneficiaries)}")
    lines.append(f"  contribution vs closest works: {sig.contribution_vs_closest}")
    lines.append("Feasibility:")
    for check in outcome.data_checks:
        lines.append(f"  data: {check['name']} ({check['source']}) -> {check['status']}")
    lines.append(f"  compute: {feas.compute_needed}")
    if feas.skill_gaps:
        lines.append(f"  skill gaps: {'; '.join(feas.skill_gaps)}")
    timeline = outcome.timeline
    available = f"{timeline['available_weeks']:g}" if timeline["available_weeks"] is not None else "?"
    lines.append(f"  timeline: {' + '.join(f'{p.phase} {p.weeks:g}w' for p in feas.timeline)} "
                 f"= {timeline['total_weeks']:g} weeks (available: {available})")
    for title, items in (("risks", feas.risks), ("ethics", feas.ethics),
                         ("blocking", [*feas.blocking_requirements, *outcome.code_blocking]),
                         ("simplifications", feas.simplifications)):
        if items:
            lines.append(f"  {title}:")
            lines.extend(f"    - {item}" for item in items)
    return "\n".join(lines)


async def finish(sessions: Sessions, investigation_id: uuid.UUID, evaluation_status: str,
                 models: LoopModels | None = None) -> int:
    """After the evaluation: triage, then the router: ranking -> reporting -> results ready, or a refinement
    carried out by the candidate loop (scripts/candidate_loop.py)."""
    async with sessions() as session:
        stage = (await session.get(Investigation, investigation_id)).stage
    if stage == Stage.FULL_EVALUATION:
        outcome = await finish_investigation(sessions, investigation_id)
        decision = outcome.decision
        print(f"\nTriage: rule {decision.rule_number}: {decision.action} ({decision.reason})")
        print(f"Your idea's status: {outcome.candidate_status.replace('_', ' ')}"
              f" | evaluation {evaluation_status}")
        if outcome.not_executed:
            print(f"  {outcome.not_executed}")
        if outcome.route is not None and outcome.route.stage != Stage.RANKING:
            print(f"\nNext: {outcome.route.stage.replace('_', ' ')} ({outcome.route.reason})")
            return await continue_at(sessions, investigation_id, models, command="evaluate")
    async with sessions() as session:
        cost = (await session.get(Investigation, investigation_id)).cost_usd
    print(f"\nDone. Total cost: ${cost:.2f}")
    print(f"Full report: python -m scripts.report {investigation_id} [--out report.md]")
    return 0


def format_all_verdicts(verdicts, *, final: bool = False) -> str:
    lines = []
    for name, verdict in (("Significance", verdicts.significance), ("Feasibility", verdicts.feasibility)):
        lines.append(f"{name}: {LABELS[verdict.category]}   [{verdict.rules_version}, rule {verdict.rule}]")
        lines.extend(f"  {line}" for line in verdict.trace)
    rec = verdicts.recommendation
    flag = "   + needs further investigation" if rec.needs_further_investigation else ""
    kind = "" if final else " (preview)"
    lines.append(f"\nRecommendation{kind}: {rec.label}{flag}   [{rec.rules_version}, rule {rec.rule}]")
    lines.extend(f"  {line}" for line in rec.trace)
    if not final:
        lines.append(f"Triage (preview): rule {verdicts.triage.number}: {verdicts.triage.action} "
                     f"({verdicts.triage.reason})")
    return "\n".join(lines)


def format_verdict(verdict, coverage, *, final: bool = False) -> str:
    funnel = coverage["funnel"]
    rounds = len(coverage.get("skeptic_rounds", []))
    when = "before the Skeptic" if not rounds else f"after {rounds} Skeptic round{'s' if rounds > 1 else ''}"
    state = "final" if final else f"preliminary, {when}"
    lines = [f"Novelty ({state}): {verdict.label}   [{verdict.rules_version}, rule {verdict.rule}]"]
    lines.extend(f"  {line}" for line in verdict.trace)
    if verdict.weaknesses:
        lines.append("  Limits of the search behind this:")
        lines.extend(f"    - {weakness}" for weakness in verdict.weaknesses)
    lines.append(f"Coverage: {funnel['unique']} unique papers -> {funnel['screened']} screened -> "
                 f"{funnel['relevant']} relevant -> {funnel['read']} read "
                 f"({coverage['read_abstract_only']} abstract only); "
                 f"peer-reviewed {coverage['tier_mix']['peer_reviewed']}, preprints {coverage['tier_mix']['preprint']}")
    lines.append("Blind spots:")
    lines.extend(f"  - {spot}" for spot in coverage["blind_spots"])
    return "\n".join(lines)


# --- main -----------------------------------------------------------------------------------------------

def document_consent(args: argparse.Namespace, ask: Ask = input):
    """When the user agreed to send the document to Claude (now), or None."""
    if args.consent:
        return now()
    if args.yes:
        raise InputError("--yes needs --consent with --idea-file: the document is only sent to Claude if you agree.")
    reply = _ask(ask, f"\n{args.idea_file.name} will be sent to Claude (Anthropic) to be read; it stays private to this "
                      "investigation and is never sent to the paper search services. Continue? [y/N] ")
    return now() if (reply or "").strip().lower() in ("y", "yes") else None


def format_document_reading(outcome) -> str:
    verified, total = outcome.reader_quotes
    lines = [f"Your document was read: {verified} of {total} quotes checked against your text."]
    if outcome.facet_sources:
        lines.append("Where the facets come from: " + "; ".join(
            f"{section}: {text[:60]}" for text, section in outcome.facet_sources.items()))
    if outcome.novelty_claims:
        lines.append("Your document's novelty claims (the evaluation will check them):")
        lines.extend(f"  - ({c['section']}) {c['claim']}" for c in outcome.novelty_claims)
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    check_max_read(args)
    if args.continue_id is None and not (args.idea or args.idea_file):
        raise InputError("Give your idea as text, or a document with --idea-file (or --continue <id>).")
    data = investigation_input(args) if args.continue_id is None else None
    prepared = consent_at = None
    if args.continue_id is None and args.idea_file:
        try:
            prepared = prepare_document(args.idea_file)
        except (DocumentError, OSError) as error:
            raise InputError(str(error)) from None
        consent_at = document_consent(args)
        if consent_at is None:
            print("Not sent: nothing was stored.")
            return 2
    try:
        database = Database.from_settings()
    except ConfigError as error:
        raise InputError(str(error)) from None
    try:
        if not await database.ping():
            raise InputError("The database isn't reachable: start it with `docker compose up -d db`.")
        if args.continue_id is not None:
            return await resume(database.session, args.continue_id, yes=args.yes, max_read=args.max_read,
                                budget=args.budget)

        async with database.session() as session:
            investigation = await create_investigation(session, mode="evaluate", complexity=args.complexity,
                                                       input=data, constraints=dict(args.constraint))
            set_cost_cap(investigation, args.budget if args.budget is not None else DEFAULT_COST_CAP_USD)
            investigation_id = investigation.id
            if prepared is not None:
                ids = {k: v for k, v in (("doi", args.doi), ("arxiv", args.arxiv)) if v}
                await attach_document(session, investigation_id, prepared, consent_at=consent_at,
                                      published=bool(args.published or ids) or None, external_ids=ids)
        print(f"Investigation {investigation_id} | complexity {args.complexity} | "
              f"budget ${args.budget if args.budget is not None else DEFAULT_COST_CAP_USD}\n")

        if prepared is not None:
            print(f"Reading your document {prepared.file_name} ({len(prepared.sections)} sections: the Paper Reader, "
                  "then the Idea Analyst)...")
            outcome = await understand_document(database.session, investigation_id)
        else:
            outcome = await understand_idea(database.session, investigation_id)

        async with database.session() as session:
            stored = await session.get(Investigation, investigation_id)
            stage, status, cost = stored.stage, stored.status, stored.cost_usd
        if outcome.error:
            print(f"The Idea Analyst failed: {outcome.error}")
        else:
            if prepared is not None:
                print(format_document_reading(outcome) + "\n")
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


async def resume(sessions: Sessions, investigation_id: uuid.UUID, *, yes: bool, max_read: int | None = None,
                 budget: float | None = None) -> int:
    """--continue: picks the investigation up where it stopped."""
    async with sessions() as session:
        investigation = await session.get(Investigation, investigation_id)
        if investigation is not None and budget is not None:
            set_cost_cap(investigation, budget)
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
    if investigation.stage == Stage.RESULTS_READY:
        print(f"Completed. Full report: python -m scripts.report {investigation_id}")
        return 0
    if investigation.stage in (Stage.TRIAGE, Stage.REFINING, Stage.LIGHT_EVALUATION, Stage.RANKING,
                               Stage.FULL_EVALUATION, Stage.GENERATING_CANDIDATES, Stage.PRESCREENING):
        return await continue_at(sessions, investigation_id, command="evaluate")
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
