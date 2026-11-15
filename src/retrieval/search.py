"""Multi-source literature search: ask every source at once, return one deduplicated list.

This is the function the rest of Eureka uses to find papers: the orchestrator's
retrieval stages, the Scout's and Skeptic's search tools, and scripts/search.py.

- All sources are queried in parallel, so a search takes as long as the slowest
  source, not the sum.
- A failing source never breaks the search. Expected failures (SourceRequestError),
  unexpected bugs in an adapter, and sources that take too long all become a failed
  SourceReport, while the other sources' papers are still returned.
- Results are interleaved by rank (source A #1, source B #1, A #2, ...) before
  deduplication, so the combined order reflects relevance across sources.
- If every source fails, the result says so (all_failed). The caller decides what
  that means (doc 05 §11.6: in the first retrieval, the investigation fails).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from typing import Protocol

from src.retrieval.cache import ResponseCache
from src.retrieval.dedup import deduplicate
from src.retrieval.http import SourceRequestError
from src.retrieval.records import PaperRecord, SearchRequest, SearchResult, SourceReport
from src.retrieval.sources.arxiv import ArxivSource
from src.retrieval.sources.openalex import OpenAlexSource

logger = logging.getLogger(__name__)

DEFAULT_SOURCE_TIMEOUT = 60.0
"""Seconds one source may take in total, including its retries."""


class SearchSource(Protocol):
    """What every literature source adapter provides (doc 03 §5)."""

    name: str

    async def search(self, request: SearchRequest) -> list[PaperRecord]: ...

    async def aclose(self) -> None: ...


def interleave(ranked_lists: Sequence[Sequence[PaperRecord]]) -> list[PaperRecord]:
    """[[a1, a2, a3], [b1]] -> [a1, b1, a2, a3]: rank 1 of every source first, then rank 2, ..."""
    combined: list[PaperRecord] = []
    for rank in range(max((len(papers) for papers in ranked_lists), default=0)):
        combined.extend(papers[rank] for papers in ranked_lists if rank < len(papers))
    return combined


async def run_source(
    source: SearchSource, request: SearchRequest, *, timeout: float = DEFAULT_SOURCE_TIMEOUT
) -> tuple[SourceReport, list[PaperRecord]]:
    """Runs one source's search and never raises: any failure becomes a failed report."""
    started = time.perf_counter()

    def elapsed_ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    try:
        papers = await asyncio.wait_for(source.search(request), timeout)
    except SourceRequestError as error:
        problem = error.message
    except TimeoutError:
        problem = f"no answer within {timeout:g} seconds"
    except Exception as error:  # a bug in one adapter must not lose the other sources' papers
        logger.exception("search: source %s crashed", source.name)
        problem = f"internal error ({type(error).__name__})"
    else:
        return SourceReport(source=source.name, ok=True, result_count=len(papers), latency_ms=elapsed_ms()), papers

    logger.warning("search: source %s failed: %s", source.name, problem)
    return SourceReport(source=source.name, ok=False, latency_ms=elapsed_ms(), error=problem), []


async def search_sources(
    sources: Sequence[SearchSource], request: SearchRequest, *, timeout: float = DEFAULT_SOURCE_TIMEOUT
) -> SearchResult:
    """Searches all sources in parallel, then interleaves and deduplicates their results."""
    outcomes = await asyncio.gather(*(run_source(source, request, timeout=timeout) for source in sources))
    combined = interleave([papers for _, papers in outcomes])
    deduplicated = deduplicate(combined)
    result = SearchResult(
        request=request,
        papers=deduplicated.papers,
        reports=tuple(report for report, _ in outcomes),
        retrieved_count=len(combined),
    )
    logger.info(
        "search: %r -> %d records, %d papers, failed sources: %s",
        request.query, result.retrieved_count, len(result.papers), ", ".join(result.failed_sources) or "none",
    )
    return result


class LiteratureSearch:
    """The set of sources Eureka searches. Create once, reuse, and close with aclose()."""

    def __init__(self, sources: Sequence[SearchSource], *, source_timeout: float = DEFAULT_SOURCE_TIMEOUT) -> None:
        if not sources:
            raise ValueError("LiteratureSearch needs at least one source.")
        names = [source.name for source in sources]
        if len(set(names)) != len(names):
            raise ValueError(f"Source names must be unique, got {names}.")
        self._sources = tuple(sources)
        self._timeout = source_timeout

    @classmethod
    def from_settings(
        cls, *, cache: ResponseCache | None = None, sources: Sequence[str] | None = None
    ) -> LiteratureSearch:
        """The default sources: OpenAlex (with the key from .env, if set) and arXiv.

        Args:
            cache: Optional response cache shared by all sources.
            sources: Limit to these source names (e.g. ["arxiv"]); default: all.
        """
        factories = {
            "openalex": lambda: OpenAlexSource.from_settings(cache=cache),
            "arxiv": lambda: ArxivSource(cache=cache),
        }
        names = list(sources) if sources else list(factories)
        unknown = [name for name in names if name not in factories]
        if unknown:
            raise ValueError(f"Unknown source(s): {', '.join(unknown)}. Available: {', '.join(factories)}.")
        return cls([factories[name]() for name in names])

    @property
    def source_names(self) -> tuple[str, ...]:
        return tuple(source.name for source in self._sources)

    async def search(self, request: SearchRequest) -> SearchResult:
        return await search_sources(self._sources, request, timeout=self._timeout)

    async def __aenter__(self) -> LiteratureSearch:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Closes every source's connections; one failing close doesn't stop the others."""
        results = await asyncio.gather(*(source.aclose() for source in self._sources), return_exceptions=True)
        for source, outcome in zip(self._sources, results):
            if isinstance(outcome, Exception):
                logger.warning("search: closing source %s failed: %s", source.name, outcome)
