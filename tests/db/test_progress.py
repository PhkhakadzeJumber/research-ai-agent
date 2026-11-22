"""Tests for progress events (step 9c): numbering and storage (src/db/repositories/progress.py), the driver's
events described in words (src/ai/progress.py), and the command line recording them too. Real test database
(rolled back), fake Claude: no network, no cost."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from scripts.candidate_loop import continue_at
from src.ai.progress import Recorder, describe, describe_ended
from src.ai.workflows.driver import Ended, Event, advance
from src.db.models import Investigation, ProgressEvent
from src.db.repositories.progress import append_event, events_after, last_event
from src.db.repositories.runs import create_investigation
from tests.db.test_clarification_workflow import sessions_on
from tests.db.test_concluding import at_concluding

pytestmark = [pytest.mark.anyio, pytest.mark.db]


async def test_events_are_numbered_per_investigation_with_the_cost_at_that_moment(db_session):
    one = await create_investigation(db_session, mode="evaluate", complexity="beginner", input={"idea": "x"})
    other = await create_investigation(db_session, mode="evaluate", complexity="beginner", input={"idea": "y"})
    first = await append_event(db_session, one.id, kind="stage", message="Started")
    one.cost_usd = Decimal("0.123456")
    await db_session.flush()
    second = await append_event(db_session, one.id, kind="step_done", message="Searched", data={"unique_papers": 5})
    elsewhere = await append_event(db_session, other.id, kind="stage", message="Started")
    assert (first.seq, second.seq, elsewhere.seq) == (1, 2, 1)
    assert (first.cost_usd, second.cost_usd) == (Decimal("0"), Decimal("0.123456"))
    assert [e.seq for e in await events_after(db_session, one.id, after=1)] == [2]
    assert (await last_event(db_session, one.id, kind="stage")).seq == 1


async def test_the_cost_keeps_six_decimals(db_session):
    # step 9c: at 4 decimals every addition was rounded (found in step 9a)
    investigation = await create_investigation(db_session, mode="evaluate", complexity="beginner", input={"idea": "x"})
    investigation.cost_usd = Decimal("0.01364") * 28
    await db_session.flush()
    await db_session.refresh(investigation)
    assert investigation.cost_usd == Decimal("0.381920")


def test_how_a_run_ended_in_words():
    assert describe_ended(Ended("done")).message == "Done: the results are ready."
    budget = describe_ended(Ended("budget", "before refining", Decimal("5.012"), Decimal("5")))
    assert budget.message.startswith("Budget used up: $5.01 of $5, stopped before refining.")
    assert budget.data == {"reason": "budget", "where": "before refining", "cost_usd": 5.012, "cap_usd": 5.0}
    assert describe_ended(Ended("stopped", "concluding")).message.startswith("Stopped at concluding.")
    assert describe_ended(Ended("stopped", "before full evaluation")).message.startswith("Stopped before full")
    assert "Anthropic refused" in describe_ended(Ended("account_refused", "screening")).message
    assert describe_ended(Ended("nothing", "awaiting_clarification")).message == (
        "Waiting for your answers to the clarification.")
    assert describe(Event("done", {"cost_usd": 1})) == []  # the end is recorded by describe_ended


async def test_a_run_records_what_it_did(db_session):
    investigation_id, _ = await at_concluding(db_session)
    recorder = Recorder(sessions_on(db_session), investigation_id)
    ended = await advance(sessions_on(db_session), investigation_id, emit=recorder)
    await recorder.ended(ended)
    rows = await events_after(db_session, investigation_id)
    assert [r.kind for r in rows] == ["step_started", "step_done", "candidate", "ended"]
    assert rows[0].message == "Concluding: freezing the coverage and the verdicts"
    assert rows[1].message.startswith("Verdicts: novelty Potential opportunity, significance Strong")
    assert rows[1].data["recommendation"] == "pursue"
    assert rows[2].message.startswith("Triage: rule ") and rows[2].message.endswith("Your idea is now finalist.")
    assert rows[3].data == {"reason": "done", "where": None} and rows[3].cost_usd > 0


async def test_the_command_line_records_events_too(db_session, capsys):
    investigation_id, _ = await at_concluding(db_session)
    assert await continue_at(sessions_on(db_session), investigation_id, command="evaluate") == 0
    assert "Done. Total cost" in capsys.readouterr().out  # printing is unchanged
    kinds = (await db_session.execute(select(ProgressEvent.kind).where(
        ProgressEvent.investigation_id == investigation_id).order_by(ProgressEvent.seq))).scalars().all()
    assert kinds[-1] == "ended" and "step_done" in kinds


async def test_a_failure_to_record_never_stops_the_run(db_session):
    investigation_id, _ = await at_concluding(db_session)

    def broken_sessions():
        raise RuntimeError("database gone")

    recorder = Recorder(broken_sessions, investigation_id)
    assert (await advance(sessions_on(db_session), investigation_id, emit=recorder)).reason == "done"
    investigation = await db_session.get(Investigation, investigation_id)
    await db_session.refresh(investigation)
    assert investigation.stage == "results_ready"
