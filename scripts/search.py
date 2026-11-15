"""Search the literature from the command line.

Usage (from the repo root, with the venv active):
    python -m scripts.search "retrieval augmented generation hallucination"
    python -m scripts.search "small language models" --limit 5 --from 2022 --to 2026
    python -m scripts.search "rag evaluation" --sources arxiv        (arXiv only: free)
    python -m scripts.search "rag evaluation" --no-cache             (always ask the APIs)

Responses are cached for 7 days in .cache/http/, so repeating a search costs nothing.
OpenAlex requests use a little of your daily OpenAlex budget; arXiv is free.
Exit code: 0 on success, 1 if every source failed, 2 for invalid arguments.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from src.retrieval.cache import FileResponseCache
from src.retrieval.records import SearchRequest, SearchResult
from src.retrieval.search import LiteratureSearch

DEFAULT_CACHE_DIR = Path(".cache") / "http"
TITLE_WIDTH = 78


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m scripts.search", description="Search OpenAlex and arXiv.")
    parser.add_argument("query", help="what to search for")
    parser.add_argument("--limit", type=int, default=10, help="results per source (default: 10)")
    parser.add_argument("--from", dest="year_from", type=int, help="earliest publication year")
    parser.add_argument("--to", dest="year_to", type=int, help="latest publication year")
    parser.add_argument("--sources", help="comma-separated, e.g. openalex,arxiv (default: all)")
    parser.add_argument("--no-cache", action="store_true", help="don't read or write the response cache")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help="cache folder (default: .cache/http)")
    parser.add_argument("--verbose", action="store_true", help="show request logs")
    return parser.parse_args(argv)


def _shorten(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def format_result(result: SearchResult, cache: FileResponseCache | None = None) -> str:
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
    if cache is not None:
        lines.append(f"Cache: {cache.hits} hit(s), {cache.misses} miss(es)  [{cache.directory}]")
    else:
        lines.append("Cache: off")
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    request = SearchRequest(query=args.query, year_from=args.year_from, year_to=args.year_to, limit=args.limit)
    cache = None if args.no_cache else FileResponseCache(args.cache_dir)
    sources = [name.strip() for name in args.sources.split(",") if name.strip()] if args.sources else None

    async with LiteratureSearch.from_settings(cache=cache, sources=sources) as search:
        result = await search.search(request)

    print(format_result(result, cache))
    return 1 if result.all_failed else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # paper titles contain any characters
    try:
        return asyncio.run(run(args))
    except ValueError as error:  # invalid query, limit, years or source names
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
