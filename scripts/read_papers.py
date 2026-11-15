"""Read the top papers of a saved search with the Paper Reader, and store the analyses.

Usage (from the repo root, venv active, database running, Anthropic key in .env):
    python -m scripts.read_papers --latest                       the most recent saved search
    python -m scripts.read_papers --investigation <id> --top 5
    python -m scripts.read_papers --latest --order source --details

Papers come from a saved search (scripts.search --save). They're ordered by Eureka's
ranking if the search was ranked (--rank), otherwise by the sources' order; --order
forces one. Papers already in the library (same extractor version) are reused for free;
the others are read by Claude in parallel, their quotes verified, and everything stored.

This calls the paid Anthropic API (roughly 1-4 cents per paper read on Opus).
Exit code: 0 if all papers have an analysis, 1 if some failed, 2 for invalid input or
an unavailable database.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.workflows.reading import LibraryOutcome, read_into_library
from src.config import ConfigError
from src.db.models import Investigation, Paper, PaperAnalysis, RetrievalHit
from src.db.session import Database

TITLE_WIDTH = 70
ORDERS = ("auto", "ranking", "source")


class InputError(ValueError):
    """Invalid input, or the database isn't usable."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m scripts.read_papers",
                                     description="Read the top papers of a saved search with the Paper Reader.")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--investigation", type=uuid.UUID, help="the investigation (saved search) to read from")
    which.add_argument("--latest", action="store_true", help="use the most recently created investigation")
    parser.add_argument("--top", type=int, default=5, help="how many papers to read (default: 5)")
    parser.add_argument("--order", choices=ORDERS, default="auto",
                        help="ranking = Eureka's embedding ranking, source = the sources' order, "
                             "auto = ranking if available (default)")
    parser.add_argument("--concurrency", type=int, default=4, help="papers read at the same time (default: 4)")
    parser.add_argument("--details", action="store_true", help="also print research questions and findings")
    parser.add_argument("--verbose", action="store_true", help="show logs")
    return parser.parse_args(argv)


# --- choosing papers ---------------------------------------------------------------------------------

async def resolve_investigation(session: AsyncSession, investigation_id: uuid.UUID | None) -> Investigation:
    if investigation_id is not None:
        investigation = await session.get(Investigation, investigation_id)
        if investigation is None:
            raise InputError(f"No investigation {investigation_id}.")
        return investigation
    latest = (await session.execute(
        select(Investigation).order_by(Investigation.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    if latest is None:
        raise InputError("No investigations yet: save a search first with `python -m scripts.search ... --save`.")
    return latest


@dataclass(frozen=True)
class ChosenPaper:
    paper: Paper
    hit: RetrievalHit


async def choose_papers(
    session: AsyncSession, investigation_id: uuid.UUID, *, top: int, order: str = "auto"
) -> tuple[list[ChosenPaper], str]:
    """The top papers of the investigation's landscape scope, and which order was used."""
    rows = (await session.execute(
        select(RetrievalHit, Paper).join(Paper, Paper.id == RetrievalHit.paper_id).where(
            RetrievalHit.investigation_id == investigation_id, RetrievalHit.evaluation_id.is_(None)
        )
    )).all()
    ranked = any(hit.embed_score is not None for hit, _ in rows)
    used = "ranking" if order == "ranking" or (order == "auto" and ranked) else "source"
    if used == "ranking" and not ranked:
        raise InputError("This search wasn't ranked: run the search with --rank, or use --order source.")

    def key(row):
        hit = row[0]
        if used == "ranking":
            return (hit.embed_score is None, -(hit.embed_score or 0.0), hit.source_rank or 10**9)
        return (hit.source_rank is None, hit.source_rank or 10**9)

    chosen = [ChosenPaper(paper=paper, hit=hit) for hit, paper in sorted(rows, key=key)[:top]]
    return chosen, used


# --- output -------------------------------------------------------------------------------------------

def _shorten(text: str, width: int = TITLE_WIDTH) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _mark(reference: dict | None) -> str:
    return "✓" if reference and reference.get("verified") else "✗"


def format_paper(position: int, chosen: ChosenPaper, outcome: LibraryOutcome, analysis: PaperAnalysis | None,
                 *, details: bool = False) -> str:
    paper = chosen.paper
    lines = [f"{position:>2}. {_shorten(paper.title)} ({paper.year or 'n.d.'})"]
    if outcome.error:
        lines.append(f"    FAILED: {outcome.error}")
        return "\n".join(lines)
    content = analysis.content if analysis else {}
    status = "reused from the library" if outcome.reused else "read now"
    level = analysis.source_level if analysis else "?"
    lines.append(f"    {status} | {level} | contribution: {content.get('contribution_type', '?')} "
                 f"| quotes verified: {outcome.verified_quotes}/{outcome.quotes}")
    for label, field in (("methods", "methods"), ("datasets", "datasets"), ("tasks", "tasks")):
        if content.get(field):
            lines.append(f"    {label}: {', '.join(content[field])}")
    counts = ", ".join(f"{len(content.get(f, []))} {f.replace('_', ' ')}"
                       for f in ("research_questions", "findings", "limitations", "future_work"))
    lines.append(f"    {counts}")
    missing = content.get("verification", {}).get("missing_names") or []
    if missing:
        lines.append(f"    names not found in the text: {', '.join(missing)}")
    if details:
        for question in content.get("research_questions", []):
            lines.append(f"    RQ {_mark(question.get('quote'))} {question['statement']}")
        for finding in content.get("findings", []):
            lines.append(f"    finding {_mark(finding.get('quote'))} [{finding['direction']}] {finding['claim']}")
        for limitation in content.get("limitations", []):
            lines.append(f"    limitation {_mark(limitation.get('quote'))} {limitation['statement']}")
    return "\n".join(lines)


def format_summary(outcomes: list[LibraryOutcome], cost: Decimal) -> str:
    read = sum(1 for o in outcomes if not o.error and not o.reused)
    reused = sum(1 for o in outcomes if o.reused)
    failed = sum(1 for o in outcomes if o.error)
    quotes = sum(o.quotes or 0 for o in outcomes if not o.error)
    verified = sum(o.verified_quotes or 0 for o in outcomes if not o.error)
    rate = f"{verified}/{quotes} ({verified / quotes:.0%})" if quotes else "no quotes"
    return (f"Papers: {read} read, {reused} reused, {failed} failed | quotes verified: {rate} | "
            f"cost of this run: ${cost:.4f}")


# --- main -----------------------------------------------------------------------------------------------

async def run(args: argparse.Namespace) -> int:
    if args.top < 1 or args.concurrency < 1:
        raise InputError("--top and --concurrency must be at least 1.")
    try:
        database = Database.from_settings()
    except ConfigError as error:
        raise InputError(str(error)) from None
    try:
        if not await database.ping():
            raise InputError("The database isn't reachable: start it with `docker compose up -d db`.")

        async with database.session() as session:
            investigation = await resolve_investigation(session, args.investigation)
            chosen, order = await choose_papers(session, investigation.id, top=args.top, order=args.order)
            cost_before = investigation.cost_usd
        if not chosen:
            raise InputError(f"Investigation {investigation.id} has no saved papers in its landscape scope.")

        query = (investigation.input or {}).get("query", "?")
        print(f'Investigation {investigation.id}: "{query}" | top {len(chosen)} by {order} order\n')

        outcomes = await read_into_library(database.session, [c.paper.id for c in chosen],
                                           investigation_id=investigation.id, concurrency=args.concurrency)

        async with database.session() as session:
            for position, (item, outcome) in enumerate(zip(chosen, outcomes), start=1):
                analysis = await session.get(PaperAnalysis, outcome.analysis_id) if outcome.analysis_id else None
                print(format_paper(position, item, outcome, analysis, details=args.details))
                print()
            cost_after = (await session.get(Investigation, investigation.id)).cost_usd
        print(format_summary(outcomes, cost_after - cost_before))
        return 1 if any(o.error for o in outcomes) else 0
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
