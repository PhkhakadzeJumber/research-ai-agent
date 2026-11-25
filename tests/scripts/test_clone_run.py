"""scripts.clone_run: copying a finished run to another account (for demos and the browser tests), free. Checked here:
references between copied rows are rewritten, also inside lists and JSON, the shared library stays shared, the
original is untouched, and the copy's step pages read exactly like the original's."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from scripts.clone_run import clone
from src.api.steps import step_page
from src.db.models import Candidate, Evaluation, Paper, ProgressEvent, RetrievalHit, SearchQuery
from tests.api.test_api_billing import new_user
from tests.api.test_api_steps import mode1, searches_and_papers
from tests.db.conftest import db_session, test_database_url  # noqa: F401  (the database fixtures)

pytestmark = [pytest.mark.anyio, pytest.mark.db]


async def count(session, model, **where):
    return (await session.execute(select(func.count()).select_from(model).filter_by(**where))).scalar_one()


async def test_a_copy_rewrites_every_reference_and_reads_like_the_original(db_session):
    owner = await new_user(db_session, "owner@example.com")
    demo = await new_user(db_session, "demo@example.com")
    investigation, idea, evaluation = await mode1(db_session, owner, stage="results_ready", step="done", status="done")
    await searches_and_papers(db_session, investigation, evaluation)
    db_session.add(ProgressEvent(investigation_id=investigation.id, seq=1, kind="stage", stage="full_evaluation",
                                 message="started", data={"evaluation_id": str(evaluation.id)}))
    await db_session.flush()
    papers_before = await count(db_session, Paper)

    copy_id, counts = await clone(db_session, investigation, demo.id)
    assert counts["candidates"] == 1 and counts["search_queries"] == 3 and counts["retrieval_hits"] == 5

    copy_idea = (await db_session.execute(select(Candidate).where(Candidate.investigation_id == copy_id))).scalar_one()
    copy_eval = (await db_session.execute(select(Evaluation).where(Evaluation.candidate_id == copy_idea.id))).scalar_one()
    assert copy_idea.id != idea.id and copy_idea.root_id == copy_idea.id  # a reference to itself, rewritten too
    copy_queries = {q.id for q in (await db_session.execute(select(SearchQuery).where(
        SearchQuery.investigation_id == copy_id))).scalars()}
    for hit in (await db_session.execute(select(RetrievalHit).where(RetrievalHit.investigation_id == copy_id))).scalars():
        assert hit.evaluation_id == copy_eval.id and set(hit.found_by_query_ids) <= copy_queries  # lists, too
    event = (await db_session.execute(select(ProgressEvent).where(ProgressEvent.investigation_id == copy_id))).scalar_one()
    assert event.data == {"evaluation_id": str(copy_eval.id)}  # ids inside JSON, too
    assert await count(db_session, Paper) == papers_before  # the library is shared, not copied
    assert await count(db_session, RetrievalHit, investigation_id=investigation.id) == 5  # the original is untouched

    from src.db.models import Investigation
    copy = await db_session.get(Investigation, copy_id)
    assert copy.user_id == demo.id
    for key in ("search", "screening", "reading", "verdict"):
        original_page = await step_page(db_session, investigation, "idle", key)
        copy_page = await step_page(db_session, copy, "idle", key)
        assert copy_page.did == original_page.did and copy_page.stats == original_page.stats
