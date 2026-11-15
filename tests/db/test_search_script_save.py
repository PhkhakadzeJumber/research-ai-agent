"""Tests for saving a search from scripts/search.py into the real test database (rolled back)."""

import pytest
from sqlalchemy import select

from scripts.search import save_result
from src.db.models import Investigation, SearchQuery
from src.retrieval.records import ExternalIds, PaperRecord, SearchRequest, SearchResult, SourceReport

pytestmark = [pytest.mark.anyio, pytest.mark.db]


def result_with(*openalex_ids, query="rag hallucination"):
    papers = tuple(
        PaperRecord(ids=ExternalIds(openalex_id=wid), title=f"Distinct paper number {wid} about retrieval",
                    sources=("openalex",), tier=1, authors=(f"Author {wid}",))
        for wid in openalex_ids
    )
    return SearchResult(
        request=SearchRequest(query=query, limit=5),
        papers=papers,
        reports=(SourceReport(source="openalex", ok=True, result_count=len(papers)),
                 SourceReport(source="arxiv", ok=False, error="request timed out")),
        retrieved_count=len(papers),
    )


async def test_save_creates_an_investigation_with_its_trail(db_session):
    summary = await save_result(db_session, result_with("W1", "W2"), mode="discover", complexity="beginner")

    assert (summary.queries, summary.papers, summary.new_papers, summary.hits) == (2, 2, 2, 2)
    investigation = await db_session.get(Investigation, summary.investigation_id)
    assert (investigation.mode, investigation.status, investigation.stage) == ("discover", "created", "intake")
    assert investigation.input == {"source": "scripts.search", "query": "rag hallucination"}
    origins = (await db_session.execute(select(SearchQuery.origin))).scalars().all()
    assert set(origins) == {"landscape"}  # discover mode searches the landscape
    assert "2 queries, 2 papers (2 new, 0 already known), 2 hits" in summary.describe()


async def test_second_save_reuses_known_papers(db_session):
    await save_result(db_session, result_with("W1", "W2"), mode="discover", complexity="beginner")
    summary = await save_result(db_session, result_with("W2", "W3"), mode="evaluate", complexity="advanced")

    assert (summary.papers, summary.new_papers) == (2, 1)  # W2 was already in the library
    origins = (await db_session.execute(
        select(SearchQuery.origin).where(SearchQuery.investigation_id == summary.investigation_id)
    )).scalars().all()
    assert set(origins) == {"analyst"}  # evaluate mode
