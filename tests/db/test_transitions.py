"""Tests for applying investigation state changes (src/ai/workflows/transitions.py), real test database."""

import uuid

import pytest
from sqlalchemy import select

from src.ai.workflows.states import IllegalTransition
from src.ai.workflows.transitions import (
    InvestigationNotFound,
    StaleState,
    advance_investigation,
    cancel_investigation,
    fail_investigation,
    start_investigation,
)
from src.db.models import Investigation, SearchQuery
from src.db.repositories.runs import create_investigation

pytestmark = [pytest.mark.anyio, pytest.mark.db]

WORKFLOW_A_HAPPY_PATH = [
    ("understanding", "running"),
    ("awaiting_clarification", "clarifying"),
    ("full_evaluation", "running"),
    ("triage", "running"),
    ("ranking", "running"),
    ("reporting", "running"),
    ("results_ready", "completed"),
    ("writing_proposal", "running"),
    ("results_ready", "completed"),
]


async def started(session, mode="evaluate"):
    investigation = await create_investigation(session, mode=mode, complexity="beginner", input={"idea": "x"})
    await start_investigation(session, investigation.id)
    return investigation


async def stored(session, investigation_id):
    """The row as the database has it (not the object in the session)."""
    row = (await session.execute(
        select(Investigation.stage, Investigation.status).where(Investigation.id == investigation_id)
    )).one()
    return tuple(row)


async def test_workflow_a_runs_through_its_stages_with_matching_statuses(db_session):
    investigation = await started(db_session)
    assert await stored(db_session, investigation.id) == ("intake", "running")
    for stage, status in WORKFLOW_A_HAPPY_PATH:
        await advance_investigation(db_session, investigation.id, to=stage)
        assert await stored(db_session, investigation.id) == (stage, status)


async def test_an_illegal_move_changes_nothing(db_session):
    investigation = await started(db_session)
    with pytest.raises(IllegalTransition, match="intake -> reporting"):
        await advance_investigation(db_session, investigation.id, to="reporting")
    assert await stored(db_session, investigation.id) == ("intake", "running")


async def test_a_stage_is_not_entered_before_the_investigation_starts(db_session):
    investigation = await create_investigation(db_session, mode="evaluate", complexity="beginner", input={})
    with pytest.raises(IllegalTransition, match="hasn't been started"):
        await advance_investigation(db_session, investigation.id, to="understanding")
    await start_investigation(db_session, investigation.id)
    with pytest.raises(StaleState, match="already started"):
        await start_investigation(db_session, investigation.id)


async def test_expected_stage_protects_against_moving_twice(db_session):
    investigation = await started(db_session)
    await advance_investigation(db_session, investigation.id, to="understanding", expected_from="intake")
    with pytest.raises(StaleState, match="at understanding, not intake"):
        await advance_investigation(db_session, investigation.id, to="understanding", expected_from="intake")


async def test_budget_stop_finishes_through_ranking(db_session):
    investigation = await started(db_session)
    for stage in ("understanding", "awaiting_clarification", "full_evaluation"):
        await advance_investigation(db_session, investigation.id, to=stage)
    with pytest.raises(IllegalTransition):
        await advance_investigation(db_session, investigation.id, to="ranking")
    await advance_investigation(db_session, investigation.id, to="ranking", budget_stop=True)
    assert await stored(db_session, investigation.id) == ("ranking", "running")


async def test_cancel_and_fail_keep_the_stage_and_are_final(db_session):
    cancelled = await started(db_session)
    await advance_investigation(db_session, cancelled.id, to="understanding")
    await cancel_investigation(db_session, cancelled.id)
    assert await stored(db_session, cancelled.id) == ("understanding", "cancelled")
    with pytest.raises(IllegalTransition, match="is cancelled"):
        await advance_investigation(db_session, cancelled.id, to="awaiting_clarification")

    failed = await started(db_session, mode="discover")
    await fail_investigation(db_session, failed.id)
    assert await stored(db_session, failed.id) == ("intake", "failed")
    with pytest.raises(IllegalTransition, match="is failed"):
        await cancel_investigation(db_session, failed.id)


async def test_completed_results_cannot_be_cancelled_but_a_deep_dive_reopens_them(db_session):
    investigation = await started(db_session)
    for stage, _ in WORKFLOW_A_HAPPY_PATH[:7]:
        await advance_investigation(db_session, investigation.id, to=stage)
    with pytest.raises(IllegalTransition, match="completed results"):
        await cancel_investigation(db_session, investigation.id)
    await advance_investigation(db_session, investigation.id, to="full_evaluation")  # deep dive
    assert await stored(db_session, investigation.id) == ("full_evaluation", "running")


async def test_stage_and_outputs_are_saved_together_or_not_at_all(db_session):
    investigation_id = (await started(db_session)).id  # the object expires on rollback
    savepoint = await db_session.begin_nested()  # stands in for the caller's transaction
    await advance_investigation(db_session, investigation_id, to="understanding")
    db_session.add(SearchQuery(investigation_id=investigation_id, source="arxiv", query_text="rag", origin="landscape"))
    await db_session.flush()
    await savepoint.rollback()  # e.g. a crash before commit

    assert await stored(db_session, investigation_id) == ("intake", "running")
    queries = (await db_session.execute(select(SearchQuery).where(SearchQuery.investigation_id == investigation_id)))
    assert queries.scalars().all() == []


async def test_unknown_investigation(db_session):
    with pytest.raises(InvestigationNotFound):
        await advance_investigation(db_session, uuid.uuid4(), to="understanding")
