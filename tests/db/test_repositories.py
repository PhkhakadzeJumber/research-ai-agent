"""Tests for src/db/repositories against the real test database (rolled back after each test)."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from src.db.models import ApiCache, Paper, PaperAnalysis, RetrievalHit, SearchQuery
from src.db.repositories import papers as papers_repository
from src.db.repositories.cache import DbResponseCache
from src.db.repositories.papers import PaperMergeConflict, upsert_paper
from src.db.repositories.runs import create_investigation
from src.db.repositories.search_trail import save_search
from tests.db.factories import make_evaluation
from src.retrieval.records import ExternalIds, PaperRecord, SearchRequest, SearchResult, SourceReport

pytestmark = [pytest.mark.anyio, pytest.mark.db]

TITLE = "Retrieval Augmented Generation for Low Resource Languages"


def record(*, doi=None, arxiv=None, openalex=None, source="openalex", tier=1, year=2024, title=TITLE, **extra):
    return PaperRecord(ids=ExternalIds(doi=doi, arxiv_id=arxiv, openalex_id=openalex), title=title,
                       sources=(source,), tier=tier, year=year, **extra)


async def count(session, model):
    return (await session.execute(select(func.count()).select_from(model))).scalar()


async def new_investigation(session):
    return await create_investigation(session, mode="evaluate", complexity="beginner", input={"idea": "x"})


# --- upsert_paper ------------------------------------------------------------------------------

async def test_new_paper_is_inserted(db_session):
    paper = await upsert_paper(db_session, record(arxiv="2405.01234", source="arxiv", tier=2, authors=("Ada L",)))
    assert paper.id is not None
    assert (paper.arxiv_id, paper.tier, paper.authors) == ("2405.01234", 2, ["Ada L"])
    assert await count(db_session, Paper) == 1


async def test_same_paper_twice_is_one_row(db_session):
    first = await upsert_paper(db_session, record(arxiv="2405.01234", source="arxiv", tier=2))
    second = await upsert_paper(db_session, record(arxiv="arXiv:2405.01234v2", source="arxiv", tier=2))
    assert first.id == second.id
    assert await count(db_session, Paper) == 1


async def test_stored_preprint_becomes_published(db_session):
    preprint = await upsert_paper(db_session, record(
        arxiv="2405.01234", source="arxiv", tier=2, year=2023, abstract="A long and complete abstract from arXiv."))
    published = await upsert_paper(db_session, record(
        doi="10.1/pub", arxiv="2405.01234", openalex="W1", tier=1, year=2024, title="The Published Title",
        venue="ACL", citation_count=57, abstract="Short."))

    assert published.id == preprint.id
    assert published.tier == 1
    assert published.year == 2023  # earliest public appearance
    assert (published.title, published.venue, published.citation_count) == ("The Published Title", "ACL", 57)
    assert published.abstract == "A long and complete abstract from arXiv."
    assert (published.doi, published.arxiv_id, published.openalex_id) == ("10.1/pub", "2405.01234", "W1")
    assert await count(db_session, Paper) == 1


async def test_two_stored_rows_of_one_work_are_merged(db_session):
    investigation = await new_investigation(db_session)
    by_doi = await upsert_paper(db_session, record(doi="10.1/pub", openalex="W1"))
    by_arxiv = await upsert_paper(db_session, record(arxiv="2405.01234", source="arxiv", tier=2))
    db_session.add_all([
        RetrievalHit(investigation_id=investigation.id, paper_id=by_doi.id, source_rank=5),
        RetrievalHit(investigation_id=investigation.id, paper_id=by_arxiv.id, source_rank=2, screened=True),
    ])
    await db_session.flush()

    merged = await upsert_paper(db_session, record(doi="10.1/pub", arxiv="2405.01234"))  # links both rows

    assert merged.id == by_doi.id  # the oldest row is kept
    assert (merged.doi, merged.arxiv_id, merged.openalex_id) == ("10.1/pub", "2405.01234", "W1")
    assert await count(db_session, Paper) == 1
    hits = (await db_session.execute(select(RetrievalHit))).scalars().all()
    assert len(hits) == 1  # the two hits in the same scope were combined
    assert (hits[0].paper_id, hits[0].source_rank, hits[0].screened) == (merged.id, 2, True)


async def test_merging_rows_with_content_is_refused(db_session):
    by_doi = await upsert_paper(db_session, record(doi="10.1/pub", openalex="W1"))
    by_arxiv = await upsert_paper(db_session, record(arxiv="2405.01234", source="arxiv", tier=2))
    db_session.add(PaperAnalysis(paper_id=by_arxiv.id, extractor_version="v1", model="m",
                                 source_level="abstract", content={}))
    await db_session.flush()

    with pytest.raises(PaperMergeConflict, match="paper_analyses"):
        await upsert_paper(db_session, record(doi="10.1/pub", arxiv="2405.01234"))
    assert await count(db_session, Paper) == 2  # nothing was changed
    assert by_doi.id != by_arxiv.id


async def test_insert_conflict_retries_as_a_merge(db_session, monkeypatch):
    stored = await upsert_paper(db_session, record(arxiv="2405.01234", source="arxiv", tier=2))

    # Simulate another transaction: the first lookup misses the stored row, so the insert collides.
    real_find = papers_repository.find_papers_by_ids
    calls = []

    async def find_missing_once(session, ids, *, lock=False):
        calls.append(ids)
        return [] if len(calls) == 1 else await real_find(session, ids, lock=lock)

    monkeypatch.setattr(papers_repository, "find_papers_by_ids", find_missing_once)
    again = await upsert_paper(db_session, record(arxiv="2405.01234", openalex="W7"))

    assert len(calls) == 2  # first attempt collided, second merged
    assert again.id == stored.id and again.openalex_id == "W7"
    assert await count(db_session, Paper) == 1


# --- save_search -------------------------------------------------------------------------------

def search_result(*papers, query="rag hallucination", failed=None):
    reports = [SourceReport(source="openalex", ok=True, result_count=len(papers), latency_ms=800)]
    reports.append(SourceReport(source="arxiv", ok=False, error=failed) if failed
                   else SourceReport(source="arxiv", ok=True, result_count=1, latency_ms=700))
    return SearchResult(request=SearchRequest(query=query, year_from=2021, limit=10), papers=tuple(papers),
                        reports=tuple(reports), retrieved_count=len(papers))


async def test_save_search_stores_queries_papers_and_hits(db_session):
    investigation = await new_investigation(db_session)
    both = PaperRecord(ids=ExternalIds(arxiv_id="2405.01234", openalex_id="W1"), title=TITLE,
                       sources=("openalex", "arxiv"), tier=1)
    only_openalex = record(openalex="W2", title="Another Distinct Paper About Evaluation")

    saved = await save_search(db_session, investigation.id, search_result(both, only_openalex), origin="analyst")

    queries = {q.source: q for q in saved.queries}
    assert set(queries) == {"openalex", "arxiv"}
    assert queries["openalex"].filters == {"year_from": 2021, "limit": 10}
    assert [hit.source_rank for hit in saved.hits] == [1, 2]
    assert set(saved.hits[0].found_by_query_ids) == {queries["openalex"].id, queries["arxiv"].id}
    assert saved.hits[1].found_by_query_ids == [queries["openalex"].id]
    assert all(hit.evaluation_id is None for hit in saved.hits)  # landscape scope
    assert (await count(db_session, Paper), await count(db_session, RetrievalHit)) == (2, 2)


async def test_failed_sources_are_recorded(db_session):
    investigation = await new_investigation(db_session)
    saved = await save_search(db_session, investigation.id,
                              search_result(record(openalex="W1"), failed="HTTP 503 (gave up after 4 attempts)"),
                              origin="analyst")
    arxiv = next(q for q in saved.queries if q.source == "arxiv")
    assert arxiv.error == "HTTP 503 (gave up after 4 attempts)"
    assert arxiv.result_count == 0


async def test_repeated_search_updates_hits_instead_of_duplicating(db_session):
    investigation = await new_investigation(db_session)
    paper = record(openalex="W1")
    other = record(openalex="W2", title="Another Distinct Paper About Evaluation")

    first = await save_search(db_session, investigation.id, search_result(other, paper), origin="analyst")
    second = await save_search(db_session, investigation.id, search_result(paper, query="rag low resource"),
                               origin="skeptic", round=1)

    assert await count(db_session, RetrievalHit) == 2
    hit = second.hits[0]
    assert hit.source_rank == 1  # best rank over both searches
    assert len(hit.found_by_query_ids) == 2  # found by both searches' OpenAlex queries
    assert await count(db_session, SearchQuery) == 4
    assert first.hits[1].id == hit.id


async def test_another_scope_gets_its_own_hit(db_session):
    investigation = await new_investigation(db_session)
    evaluation = await make_evaluation(db_session, investigation.id)
    await save_search(db_session, investigation.id, search_result(record(openalex="W1")), origin="landscape")
    await save_search(db_session, investigation.id, search_result(record(openalex="W1")), origin="analyst",
                      evaluation_id=evaluation.id)
    assert await count(db_session, RetrievalHit) == 2
    assert await count(db_session, Paper) == 1


# --- DbResponseCache -------------------------------------------------------------------------------

T0 = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self):
        self.moment = T0

    def __call__(self):
        return self.moment


def cache_on(session, clock):
    @asynccontextmanager
    async def sessions():
        yield session
        await session.flush()

    return DbResponseCache(sessions, clock=clock)


async def test_db_cache_put_then_get(db_session):
    cache = cache_on(db_session, FakeClock())
    await cache.put("openalex", "/works", {"search": "rag"}, '{"results": []}')
    assert await cache.get("openalex", "/works", {"search": "rag"}) == '{"results": []}'
    assert await cache.get("openalex", "/works", {"search": "other"}) is None
    assert (cache.hits, cache.misses) == (1, 1)


async def test_db_cache_expires(db_session):
    clock = FakeClock()
    cache = cache_on(db_session, clock)
    await cache.put("arxiv", "/api/query", {"q": "x"}, "<feed/>")
    clock.moment = T0 + timedelta(days=7, minutes=1)
    assert await cache.get("arxiv", "/api/query", {"q": "x"}) is None


async def test_db_cache_overwrites_and_never_stores_secrets(db_session):
    clock = FakeClock()
    cache = cache_on(db_session, clock)
    params = {"search": "rag", "api_key": "SECRET-KEY", "mailto": "me@example.org"}
    await cache.put("openalex", "/works", params, "old")
    clock.moment = T0 + timedelta(hours=1)
    await cache.put("openalex", "/works", params, "new")

    rows = (await db_session.execute(select(ApiCache))).scalars().all()
    assert len(rows) == 1
    db_session.expire_all()
    row = (await db_session.execute(select(ApiCache))).scalar_one()
    assert row.response == "new" and row.params == {"search": "rag"}
    assert "SECRET" not in str(row.params) and "me@example.org" not in str(row.params)
