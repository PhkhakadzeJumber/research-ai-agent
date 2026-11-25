"""Replaying a finished run live without AI calls (local only, EUREKA_DEV_REPLAY): the copy starts where its
pipeline started and moves exactly as the real run did, then ends finished; nothing is charged."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from src.api import replay
from src.api.routes import dev
from src.config import ApiSettings
from src.db.models import Candidate, CreditHold, Evaluation, Investigation, ProgressEvent
from src.timeutil import now
from tests.api.test_api_billing import new_user
from tests.api.test_api_steps import mode1

pytestmark = [pytest.mark.anyio, pytest.mark.db]

STEPS = ["planning_queries", "retrieving", "screening", "reading", "assessing_novelty", "skeptic_review",
         "assessing_value_feasibility", "concluding"]


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(dev, "get_api_settings", lambda: ApiSettings(frontend_origins=("http://localhost:3000",),
                                                                     dev_replay=True))

    async def no_wait(seconds):
        return None

    monkeypatch.setattr(replay.asyncio, "sleep", no_wait)


async def finished_run(db_session, user):
    investigation, _, evaluation = await mode1(db_session, user, stage="results_ready", step="done", status="done")
    investigation.status = "completed"
    start, seq = now() - timedelta(hours=1), 0
    entries = [("step_started", "understanding", None), ("step_done", "understanding", None), ("ended", None, None),
               ("stage", "awaiting_clarification", None), ("stage", "full_evaluation", None)]
    entries += [(kind, None, step) for step in STEPS for kind in ("step_started", "step_done")]
    entries += [("candidate", "triage", None), ("ended", None, None)]
    for kind, stage, step in entries:
        seq += 1
        db_session.add(ProgressEvent(investigation_id=investigation.id, seq=seq, kind=kind, stage=stage, step=step,
                                     message=f"{kind} {stage or step}", data={},
                                     created_at=start + timedelta(seconds=30 * seq)))
    await db_session.flush()
    return investigation


async def test_replay_is_off_unless_turned_on(client, db_session, user):
    investigation = await finished_run(db_session, user)
    assert (await client.get("/api/v1/dev/replay")).json() == {"enabled": False}
    assert (await client.post(f"/api/v1/dev/replay/{investigation.id}")).status_code == 404


async def test_a_replay_plays_the_run_again_and_ends_finished(client, db_session, user, runs, enabled):
    investigation = await finished_run(db_session, user)
    started = await client.post(f"/api/v1/dev/replay/{investigation.id}")
    assert started.status_code == 202
    copy_id = started.json()["investigation_id"]
    copy = await db_session.get(Investigation, copy_id)
    own = (await db_session.execute(select(Evaluation).join(Candidate, Candidate.id == Evaluation.candidate_id)
                                    .where(Candidate.investigation_id == copy.id))).scalar_one()
    assert (copy.stage, copy.status, own.step, own.status) == ("full_evaluation", "running", "planning_queries", "running")
    kept = (await db_session.execute(select(ProgressEvent.kind).where(ProgressEvent.investigation_id == copy.id)
                                     .order_by(ProgressEvent.seq))).scalars().all()
    assert kept == ["step_started", "step_done", "ended", "stage"]  # up to the clarification; the rest is replayed
    steps = (await client.get(f"/api/v1/investigations/{copy_id}/steps")).json()
    assert steps["run"] == "running" and steps["current"] == "search"

    await runs.drain()
    await db_session.refresh(copy)
    await db_session.refresh(own)
    assert (copy.stage, copy.status, own.step, own.status) == ("results_ready", "completed", "done", "done")
    replayed = (await db_session.execute(select(ProgressEvent).where(ProgressEvent.investigation_id == copy.id)
                                         .order_by(ProgressEvent.seq))).scalars().all()
    assert [e.step for e in replayed if e.kind == "step_done" and e.step] == STEPS
    assert replayed[-1].kind == "ended" and replayed[-1].data["reason"] == "done"
    assert (await db_session.execute(select(CreditHold).where(CreditHold.investigation_id == copy.id))).first() is None
    original = await db_session.get(Investigation, investigation.id)
    assert original.stage == "results_ready"  # the original is untouched


async def test_only_your_own_finished_runs_can_be_replayed(client, db_session, user, runs, enabled):
    running, _, _ = await mode1(db_session, user)
    refused = await client.post(f"/api/v1/dev/replay/{running.id}")
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "not_finished")
    other = await new_user(db_session, "replay-other@example.com")
    theirs = await finished_run(db_session, other)
    assert (await client.post(f"/api/v1/dev/replay/{theirs.id}")).status_code == 404
