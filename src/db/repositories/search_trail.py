"""Saving a whole search: the search trail (ER region 2) plus the papers it found.

save_search(...) writes, for one SearchResult:
  - one search_queries row per source, including failed sources with their error;
  - every paper into the global library (upsert_paper: deduplicated against stored papers);
  - one retrieval_hits row per paper in the scope (the landscape phase, or one evaluation),
    with the queries that found it and its rank in the result. Finding the same paper
    again in the same scope updates its hit instead of duplicating it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Paper, RetrievalHit, SearchQuery
from src.db.repositories.papers import upsert_paper
from src.retrieval.records import SearchResult


@dataclass(frozen=True)
class SavedSearch:
    queries: tuple[SearchQuery, ...]
    """One row per source asked."""

    papers: tuple[Paper, ...]
    """The stored papers, in the result's order (1 = top)."""

    hits: tuple[RetrievalHit, ...]
    """The hit of each paper in this scope, same order as `papers`."""


async def _find_hit(
    session: AsyncSession, investigation_id: uuid.UUID, evaluation_id: uuid.UUID | None, paper_id: uuid.UUID
) -> RetrievalHit | None:
    return (await session.execute(
        select(RetrievalHit).where(
            RetrievalHit.investigation_id == investigation_id,
            RetrievalHit.evaluation_id.is_not_distinct_from(evaluation_id),
            RetrievalHit.paper_id == paper_id,
        )
    )).scalar_one_or_none()


async def save_search(
    session: AsyncSession,
    investigation_id: uuid.UUID,
    result: SearchResult,
    *,
    origin: str,
    evaluation_id: uuid.UUID | None = None,
    query_type: str | None = None,
    round: int | None = None,
) -> SavedSearch:
    """Stores one search's trail and papers. `origin` is analyst / landscape / scout / skeptic."""
    request = result.request
    filters = {
        name: value
        for name, value in {"year_from": request.year_from, "year_to": request.year_to, "limit": request.limit}.items()
        if value is not None
    }

    queries: dict[str, SearchQuery] = {}
    for report in result.reports:
        query = SearchQuery(
            investigation_id=investigation_id, evaluation_id=evaluation_id, origin=origin, query_type=query_type,
            round=round, source=report.source, query_text=request.query, filters=filters,
            result_count=report.result_count, latency_ms=report.latency_ms, error=report.error,
        )
        session.add(query)
        queries[report.source] = query
    await session.flush()

    papers: list[Paper] = []
    hits: list[RetrievalHit] = []
    for rank, record in enumerate(result.papers, start=1):
        paper = await upsert_paper(session, record)
        found_by = [queries[source].id for source in record.sources if source in queries]
        hit = await _find_hit(session, investigation_id, evaluation_id, paper.id)
        if hit is None:
            hit = RetrievalHit(
                investigation_id=investigation_id, evaluation_id=evaluation_id, paper_id=paper.id,
                found_by_query_ids=found_by, source_rank=rank,
            )
            session.add(hit)
        else:
            hit.found_by_query_ids = list(dict.fromkeys([*hit.found_by_query_ids, *found_by]))
            hit.source_rank = min(hit.source_rank, rank) if hit.source_rank is not None else rank
        await session.flush()
        papers.append(paper)
        hits.append(hit)

    return SavedSearch(queries=tuple(queries.values()), papers=tuple(papers), hits=tuple(hits))
