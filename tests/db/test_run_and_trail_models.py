"""Tests for the run (users, investigations) and search-trail tables."""

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from src.db.models import ApiCache, Investigation, Paper, RetrievalHit, SearchQuery, User
from tests.db.factories import make_evaluation
from src.db.models.run import INVESTIGATION_STAGES, INVESTIGATION_STATUSES

pytestmark = [pytest.mark.anyio, pytest.mark.db]

DOC_05 = Path(__file__).resolve().parents[2] / "docs" / "05-workflows-and-orchestration.md"


def make_investigation(**overrides):
    values = dict(mode="evaluate", complexity="beginner", input={"idea": "RAG for low-resource QA"})
    values.update(overrides)
    return Investigation(**values)


async def add_and_flush(session, *objects):
    session.add_all(objects)
    await session.flush()


async def assert_rejected(session, *objects, constraint):
    with pytest.raises(IntegrityError, match=constraint):
        async with session.begin_nested():
            await add_and_flush(session, *objects)


# --- the database's state names must equal the formal state graph (doc 05 §11.1) ------------

def test_stage_names_match_the_state_graph_in_doc_05():
    section = DOC_05.read_text(encoding="utf-8").split("### 11.1 Investigation stages", 1)[1].split("###", 1)[0]
    documented = tuple(re.findall(r"^\| `([a-z_]+)` \|", section, flags=re.MULTILINE))
    assert documented == INVESTIGATION_STAGES


def test_status_names_match_the_state_graph_in_doc_05():
    line = DOC_05.read_text(encoding="utf-8").split("**Investigation status values:**", 1)[1].split("\n", 1)[0]
    line = re.sub(r"\([^)]*\)", "", line)  # drop notes like "(before `intake`)"
    assert tuple(re.findall(r"`([a-z_]+)`", line)) == INVESTIGATION_STATUSES


# --- users and investigations -----------------------------------------------------------------

async def test_investigation_defaults(db_session):
    investigation = make_investigation()
    await add_and_flush(db_session, investigation)
    await db_session.refresh(investigation)

    assert (investigation.status, investigation.stage) == ("created", "intake")
    assert investigation.user_id is None  # command-line runs have no user
    assert investigation.budget == {} and investigation.assumptions == []
    assert investigation.cost_usd == 0


async def test_investigation_belongs_to_a_user(db_session):
    user = User(email="student@example.org")
    await add_and_flush(db_session, user)
    await add_and_flush(db_session, make_investigation(user_id=user.id))


@pytest.mark.parametrize(
    "overrides, constraint",
    [
        ({"mode": "brainstorm"}, "ck_investigations_mode_valid"),
        ({"complexity": "expert"}, "ck_investigations_complexity_valid"),
        ({"status": "paused"}, "ck_investigations_status_valid"),
        ({"stage": "skeptic_round_2"}, "ck_investigations_stage_valid"),  # not a stage in the graph
    ],
)
async def test_invalid_investigation_values_are_rejected(db_session, overrides, constraint):
    await assert_rejected(db_session, make_investigation(**overrides), constraint=constraint)


async def test_user_emails_are_unique_and_lowercase(db_session):
    await add_and_flush(db_session, User(email="a@example.org"))
    await assert_rejected(db_session, User(email="a@example.org"), constraint="uq_users_email")
    await assert_rejected(db_session, User(email="B@Example.org"), constraint="ck_users_email_lowercase")


# --- search trail ---------------------------------------------------------------------------------

async def make_trail(session):
    """An investigation with one paper, one query and one landscape hit."""
    investigation = make_investigation()
    paper = Paper(title="A paper with a distinct title", tier=2, arxiv_id="2405.01234")
    await add_and_flush(session, investigation, paper)
    query = SearchQuery(investigation_id=investigation.id, origin="analyst", source="arxiv",
                        query_text="rag hallucination", result_count=1)
    await add_and_flush(session, query)
    hit = RetrievalHit(investigation_id=investigation.id, paper_id=paper.id, found_by_query_ids=[query.id])
    await add_and_flush(session, hit)
    return investigation, paper, query, hit


async def test_search_trail_round_trip(db_session):
    _, _, query, hit = await make_trail(db_session)
    await db_session.refresh(hit)
    assert hit.found_by_query_ids == [query.id]
    assert hit.screened is False and hit.selected_for_reading is False


async def test_one_hit_per_paper_per_scope(db_session):
    investigation, paper, _, _ = await make_trail(db_session)
    # Same paper, landscape scope again (evaluation_id empty): rejected thanks to NULLS NOT DISTINCT.
    await assert_rejected(
        db_session, RetrievalHit(investigation_id=investigation.id, paper_id=paper.id),
        constraint="uq_retrieval_hits_investigation_id_evaluation_id_paper_id",
    )
    # Same paper in an evaluation's scope: a different scope, so allowed.
    evaluation = await make_evaluation(db_session, investigation.id)
    await add_and_flush(db_session, RetrievalHit(investigation_id=investigation.id, paper_id=paper.id,
                                                 evaluation_id=evaluation.id))


@pytest.mark.parametrize(
    "make, constraint",
    [
        (lambda inv: SearchQuery(investigation_id=inv.id, origin="google", source="x", query_text="q"),
         "ck_search_queries_origin_valid"),
        (lambda inv: SearchQuery(investigation_id=inv.id, origin="skeptic", source="x", query_text="q", round=0),
         "ck_search_queries_round_positive"),
    ],
)
async def test_invalid_search_queries_are_rejected(db_session, make, constraint):
    investigation = make_investigation()
    await add_and_flush(db_session, investigation)
    await assert_rejected(db_session, make(investigation), constraint=constraint)


async def test_llm_score_range(db_session):
    investigation, paper, _, hit = await make_trail(db_session)
    hit.llm_score = 4
    with pytest.raises(IntegrityError, match="ck_retrieval_hits_llm_score_range"):
        async with db_session.begin_nested():
            await db_session.flush()


async def test_deleting_an_investigation_keeps_papers(db_session):
    investigation, paper, _, _ = await make_trail(db_session)
    await db_session.execute(delete(Investigation).where(Investigation.id == investigation.id))

    remaining_queries = (await db_session.execute(select(func.count()).select_from(SearchQuery))).scalar()
    remaining_hits = (await db_session.execute(select(func.count()).select_from(RetrievalHit))).scalar()
    paper_still_there = (await db_session.execute(select(Paper.id).where(Paper.id == paper.id))).scalar()
    assert (remaining_queries, remaining_hits) == (0, 0)
    assert paper_still_there == paper.id


async def test_api_cache_round_trip(db_session):
    moment = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    await add_and_flush(db_session, ApiCache(key="k" * 64, source="arxiv", path="/api/query",
                                             params={"search_query": "all:rag"}, response="<feed/>", fetched_at=moment))
    stored = (await db_session.execute(select(ApiCache).where(ApiCache.key == "k" * 64))).scalar_one()
    assert (stored.response, stored.fetched_at) == ("<feed/>", moment)
