"""Evaluate a research idea (Workflow A) from the command line.

Usage (from the repo root, venv active, database running, Anthropic key in .env):
    python -m scripts.evaluate "use RAG to reduce hallucinations of small LMs for Georgian QA"
    python -m scripts.evaluate "..." --field NLP --complexity beginner --seed 2401.12345 \\
        --constraint "available time=8 weeks" --constraint "compute=laptop only"
    python -m scripts.evaluate --continue <investigation id>     resume at the clarification
    python -m scripts.evaluate "..." --yes                       skip the questions

So far (steps 4.2-4.3):
  1. understanding: the Idea Analyst's reading of the idea (facets, assumptions,
     vagueness flags, clarifying questions), stored as the investigation's Intake;
  2. clarification: you answer the questions (Enter skips one), may edit facets, and
     confirm. Answers are applied by one more Idea Analyst call. Your idea becomes
     Candidate v1 and the investigation moves to full_evaluation.
If Eureka had to propose a KEY facet, you must confirm the reading yourself: --yes is
refused. Quitting at the confirmation keeps everything; resume with --continue.
Later steps extend this until the whole evaluation runs (step 4.8).

This calls the paid Anthropic API (one Opus call, plus one if you answer questions;
roughly 4 cents each).
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

from src.ai.schemas.structured_idea import FACET_KINDS, StructuredIdeaOutput
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
from src.ai.workflows.understanding import understand_idea
from src.config import ConfigError
from src.db.models import Investigation
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
    parser.add_argument("--yes", action="store_true",
                        help="skip the questions and confirm (refused if Eureka proposed a key facet)")
    parser.add_argument("--verbose", action="store_true", help="show logs")
    return parser.parse_args(argv)


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
             "Facets (* = key, [proposed] = Eureka's suggestion, not in your idea):"]
    for kind in FACET_KINDS:
        for facet in reading.facets_of(kind):
            proposed = "" if facet.stated_by_user else "  [proposed]"
            lines.append(f"  {kind.replace('_', '-'):<13}{'*' if facet.is_key else ' '} {facet.text}{proposed}")
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
    print("Next: the evaluation itself (query plan, search, reading) comes with step 4.4.")
    return 0


# --- main -----------------------------------------------------------------------------------------------

async def run(args: argparse.Namespace) -> int:
    data = investigation_input(args) if args.continue_id is None else None
    try:
        database = Database.from_settings()
    except ConfigError as error:
        raise InputError(str(error)) from None
    try:
        if not await database.ping():
            raise InputError("The database isn't reachable: start it with `docker compose up -d db`.")
        if args.continue_id is not None:
            try:
                pending = await load_pending(database.session, args.continue_id)
            except NotWaitingForClarification as error:
                raise InputError(str(error)) from None
            print(f"Investigation {args.continue_id}\n\n{format_reading(pending.reading)}")
            return await clarify(database.session, args.continue_id, yes=args.yes)

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
        return await clarify(database.session, investigation_id, yes=args.yes)
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
