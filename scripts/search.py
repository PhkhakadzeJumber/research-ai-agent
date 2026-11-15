"""Search the literature from the command line, optionally saving the results.

Usage (from the repo root, with the venv active):
    python -m scripts.search "retrieval augmented generation hallucination"
    python -m scripts.search "small language models" --limit 5 --from 2022 --to 2026
    python -m scripts.search "rag evaluation" --sources arxiv        (arXiv only: free)
    python -m scripts.search "rag evaluation" --save                 (store in the database)
    python -m scripts.search "rag evaluation" --no-cache             (always ask the APIs)

Caching: API responses are reused for 7 days. By default they're cached in the
database (api_cache table); if the database isn't reachable, the search still works
with the file cache in .cache/http/ and prints a warning. --file-cache forces files.

--save stores the search as a new investigation (by default mode "discover", the
landscape scope): one query row per source, the papers deduplicated against
everything already stored, and one retrieval hit per paper. It needs the database.

OpenAlex requests use a little of your daily OpenAlex budget; arXiv is free.
Exit code: 0 on success, 1 if every source failed, 2 for invalid arguments or an
unavailable database with --save.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import ConfigError
from src.db.models import Paper
from src.db.models.run import COMPLEXITY_LEVELS, MODES
from src.db.repositories.cache import DbResponseCache
from src.db.repositories.runs import create_investigation
from src.db.repositories.search_trail import save_search
from src.db.session import Database
from src.retrieval.cache import FileResponseCache, ResponseCache
from src.retrieval.records import SearchRequest, SearchResult
from src.retrieval.search import LiteratureSearch

DEFAULT_CACHE_DIR = Path(".cache") / "http"
TITLE_WIDTH = 78


class DatabaseUnavailable(RuntimeError):
    """--save was requested, but the database isn't configured or reachable."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m scripts.search", description="Search OpenAlex and arXiv.")
    parser.add_argument("query", help="what to search for")
    parser.add_argument("--limit", type=int, default=10, help="results per source (default: 10)")
    parser.add_argument("--from", dest="year_from", type=int, help="earliest publication year")
    parser.add_argument("--to", dest="year_to", type=int, help="latest publication year")
    parser.add_argument("--sources", help="comma-separated, e.g. openalex,arxiv (default: all)")
    parser.add_argument("--save", action="store_true", help="store the search in the database as a new investigation")
    parser.add_argument("--mode", choices=MODES, default="discover", help="investigation mode for --save (default: discover)")
    parser.add_argument("--complexity", choices=COMPLEXITY_LEVELS, default="beginner",
                        help="investigation complexity for --save (default: beginner)")
    cache = parser.add_mutually_exclusive_group()
    cache.add_argument("--no-cache", action="store_true", help="don't read or write any response cache")
    cache.add_argument("--file-cache", action="store_true", help="use the file cache even if the database is available")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help="file cache folder (default: .cache/http)")
    parser.add_argument("--verbose", action="store_true", help="show request logs")
    return parser.parse_args(argv)


# --- cache choice ---------------------------------------------------------------------------------

@dataclass
class CacheChoice:
    cache: ResponseCache | None
    label: str
    warning: str | None = None


async def choose_cache(args: argparse.Namespace, database: Database | None, database_ok: bool) -> CacheChoice:
    """Database cache if possible, file cache as the fallback, nothing with --no-cache."""
    if args.no_cache:
        return CacheChoice(None, "off")
    file_cache = CacheChoice(FileResponseCache(args.cache_dir), f"files in {args.cache_dir}")
    if args.file_cache:
        return file_cache
    if database is not None and database_ok:
        return CacheChoice(DbResponseCache(database.session), "database (api_cache)")
    file_cache.warning = "database not reachable: using the file cache (start it with `docker compose up -d db`)"
    return file_cache


def describe_cache(choice: CacheChoice) -> str:
    if choice.cache is None:
        return "Cache: off"
    return f"Cache: {choice.cache.hits} hit(s), {choice.cache.misses} miss(es)  [{choice.label}]"


# --- saving -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SaveSummary:
    investigation_id: uuid.UUID
    queries: int
    papers: int
    new_papers: int
    hits: int

    def describe(self) -> str:
        known = self.papers - self.new_papers
        return (f"Saved: investigation {self.investigation_id}: {_count(self.queries, 'query', 'queries')}, "
                f"{_count(self.papers, 'paper')} ({self.new_papers} new, {known} already known), "
                f"{_count(self.hits, 'hit')}")


def _count(number: int, singular: str, plural: str | None = None) -> str:
    return f"{number} {singular if number == 1 else plural or singular + 's'}"


async def save_result(session: AsyncSession, result: SearchResult, *, mode: str, complexity: str) -> SaveSummary:
    """Stores the search as a new investigation. The caller commits (or rolls back, in tests)."""
    papers_before = (await session.execute(select(func.count()).select_from(Paper))).scalar()
    investigation = await create_investigation(
        session, mode=mode, complexity=complexity,
        input={"source": "scripts.search", "query": result.request.query},
    )
    origin = "landscape" if mode == "discover" else "analyst"
    saved = await save_search(session, investigation.id, result, origin=origin)
    papers_after = (await session.execute(select(func.count()).select_from(Paper))).scalar()
    return SaveSummary(
        investigation_id=investigation.id, queries=len(saved.queries), papers=len(saved.papers),
        new_papers=papers_after - papers_before, hits=len(set(hit.id for hit in saved.hits)),
    )


# --- output -----------------------------------------------------------------------------------------

def _shorten(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def format_result(result: SearchResult, cache_line: str = "Cache: off") -> str:
    """Renders a search result as plain text: papers, source reports, counts, cache use."""
    request = result.request
    years = ""
    if request.year_from or request.year_to:
        years = f", years {request.year_from or '…'}–{request.year_to or '…'}"
    lines = [f'Search: "{request.query}"  (up to {request.limit} per source{years})', ""]

    if result.papers:
        lines.append(f"{'#':>3}  {'Year':<4}  {'Type':<9} {'Cites':>6}  {'Sources':<16} Title / first author")
        for rank, paper in enumerate(result.papers, start=1):
            kind = "published" if paper.tier == 1 else "preprint"
            cites = "" if paper.citation_count is None else str(paper.citation_count)
            sources = "+".join(paper.sources)
            lines.append(
                f"{rank:>3}  {paper.year or '':<4}  {kind:<9} {cites:>6}  {sources:<16} "
                f"{_shorten(paper.title, TITLE_WIDTH)}"
            )
            author = paper.first_author or "unknown author"
            if len(paper.authors) > 1:
                author += " et al."
            link = paper.open_access_pdf_url or (f"https://doi.org/{paper.ids.doi}" if paper.ids.doi else "")
            lines.append(f"{'':47}{author}{'  ' + link if link else ''}")
    else:
        lines.append("No papers found.")

    lines.append("")
    report_parts = []
    for report in result.reports:
        if report.ok:
            report_parts.append(f"{report.source} ok: {report.result_count} results ({report.latency_ms} ms)")
        else:
            report_parts.append(f"{report.source} FAILED: {report.error}")
    lines.append("Sources: " + " | ".join(report_parts))
    lines.append(
        f"Records: {result.retrieved_count} retrieved -> {len(result.papers)} papers "
        f"({result.duplicates_removed} duplicates merged)"
    )
    lines.append(cache_line)
    return "\n".join(lines)


# --- main -------------------------------------------------------------------------------------------

def open_database() -> Database | None:
    """The main database, or None if it isn't configured (e.g. no POSTGRES_PASSWORD)."""
    try:
        return Database.from_settings()
    except ConfigError:
        return None


async def run(args: argparse.Namespace) -> int:
    request = SearchRequest(query=args.query, year_from=args.year_from, year_to=args.year_to, limit=args.limit)
    sources = [name.strip() for name in args.sources.split(",") if name.strip()] if args.sources else None

    needs_database = args.save or not (args.no_cache or args.file_cache)
    database = open_database() if needs_database else None
    try:
        database_ok = database is not None and await database.ping()
        if args.save and not database_ok:
            raise DatabaseUnavailable(
                "--save needs the database: start it with `docker compose up -d db` "
                "and set POSTGRES_PASSWORD in .env"
            )

        choice = await choose_cache(args, database, database_ok)
        if choice.warning:
            print(f"Warning: {choice.warning}", file=sys.stderr)

        async with LiteratureSearch.from_settings(cache=choice.cache, sources=sources) as search:
            result = await search.search(request)
        print(format_result(result, describe_cache(choice)))

        if args.save and database is not None:
            async with database.session() as session:  # commits at the end
                summary = await save_result(session, result, mode=args.mode, complexity=args.complexity)
            print(summary.describe())
        return 1 if result.all_failed else 0
    finally:
        if database is not None:
            await database.dispose()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # paper titles contain any characters
    try:
        return asyncio.run(run(args))
    except (ValueError, DatabaseUnavailable) as error:  # invalid input, or --save without a database
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
