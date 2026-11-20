"""Evaluate a research idea (Workflow A) from the command line.

Usage (from the repo root, venv active, database running, Anthropic key in .env):
    python -m scripts.evaluate "use RAG to reduce hallucinations of small LMs for Georgian QA"
    python -m scripts.evaluate "..." --field NLP --complexity beginner --seed 2401.12345 \\
        --constraint "available time=8 weeks" --constraint "compute=laptop only"

So far (step 4.2) it runs intake + understanding: the Idea Analyst's reading of the
idea, its assumptions, vagueness flags and clarifying questions, stored as the
investigation's Intake. It stops at the clarification checkpoint; later steps extend
it until the whole evaluation runs (step 4.7).

This calls the paid Anthropic API (one Opus call, roughly 3-6 cents).
Exit code: 0 ok, 1 the Idea Analyst failed, 2 invalid input or an unavailable database.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from src.ai.schemas.structured_idea import FACET_KINDS, StructuredIdeaOutput
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
    parser.add_argument("idea", help=f"the idea in your own words (up to {MAX_IDEA_CHARS} characters)")
    parser.add_argument("--field", help="field or subfield, e.g. NLP (inferred if missing)")
    parser.add_argument("--complexity", choices=COMPLEXITY_LEVELS, default="intermediate",
                        help="desired ambition (default: intermediate)")
    parser.add_argument("--seed", action="append", default=[], metavar="PAPER",
                        help="a paper you already know: DOI, arXiv ID or title (repeatable)")
    parser.add_argument("--constraint", action="append", default=[], type=parse_constraint, metavar="NAME=VALUE",
                        help='a constraint, e.g. "available time=8 weeks" (repeatable)')
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

def format_reading(reading: StructuredIdeaOutput) -> str:
    lines = [f"Title:  {reading.title}", f"RQ:     {reading.research_question}", "", "Facets (* = key):"]
    for kind in FACET_KINDS:
        for facet in reading.facets_of(kind):
            lines.append(f"  {kind.replace('_', '-'):<13}{'*' if facet.is_key else ' '} {facet.text}")
    for title, items in (("Assumed", reading.assumptions), ("Vague", reading.vagueness_flags)):
        if items:
            lines.append("")
            lines.append(f"{title}:")
            lines.extend(f"  - {item}" for item in items)
    lines.append("")
    if reading.clarifying_questions:
        lines.append("Questions (Eureka uses the assumption after → if you skip):")
        for number, question in enumerate(reading.clarifying_questions, start=1):
            lines.append(f"  {number}. {question.question}")
            lines.append(f"     → {question.if_skipped}")
    else:
        lines.append("Questions: none, the idea is clear enough.")
    return "\n".join(lines)


# --- main -----------------------------------------------------------------------------------------------

async def run(args: argparse.Namespace) -> int:
    data = investigation_input(args)
    try:
        database = Database.from_settings()
    except ConfigError as error:
        raise InputError(str(error)) from None
    try:
        if not await database.ping():
            raise InputError("The database isn't reachable: start it with `docker compose up -d db`.")
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
        if not outcome.error:
            print("Next: confirming or answering the questions comes with step 4.3.")
        return 1 if outcome.error else 0
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
