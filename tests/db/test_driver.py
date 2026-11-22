"""Tests for the driver (src/ai/workflows/driver.py, step 9b.1): stopping between steps and resuming, events
without any printing, and the `only` option. The commands' output on top of it is tested by the script tests
(test_evaluation_workflow.py, test_refining.py, test_landscape.py, ...). Real test database (rolled back), fake
Claude: no network, no cost."""

import pytest

from src.ai.workflows.driver import Ended, Event, advance
from src.ai.workflows.states import Stage
from src.db.models import Evaluation, Investigation
from tests.db.test_clarification_workflow import sessions_on
from tests.db.test_concluding import at_concluding, refreshed

pytestmark = [pytest.mark.anyio, pytest.mark.db]


def stop_after(checks: int):
    """should_stop: False for the first `checks` questions, then True."""
    asked = []

    def should_stop() -> bool:
        asked.append(1)
        return len(asked) > checks

    return should_stop


async def test_a_run_stops_between_steps_and_resumes(db_session):
    investigation_id, evaluation_id = await at_concluding(db_session)  # the user's idea, at its last step

    ended = await advance(sessions_on(db_session), investigation_id, should_stop=lambda: True)
    assert ended == Ended("stopped", "before full evaluation")
    # one boundary later: the evaluation was about to run its `concluding` step, and stops before it
    ended = await advance(sessions_on(db_session), investigation_id, should_stop=stop_after(1))
    assert ended == Ended("stopped", "concluding")
    evaluation = await refreshed(db_session, Evaluation, evaluation_id)
    assert (evaluation.step, evaluation.status) == ("concluding", "running")  # untouched: resumable

    events: list[Event] = []

    async def collect(event: Event) -> None:
        events.append(event)

    ended = await advance(sessions_on(db_session), investigation_id, emit=collect)
    assert ended == Ended("done") and ended.exit_code == 0
    kinds = [e.kind for e in events]
    assert kinds == ["idea_step", "idea_step", "idea_triaged", "done"]
    assert [e.data["phase"] for e in events[:2]] == ["start", "done"]
    assert events[2].data["outcome"].candidate_status == "finalist"
    investigation = await refreshed(db_session, Investigation, investigation_id)
    assert (investigation.stage, investigation.status) == ("results_ready", "completed")


async def test_without_a_listener_nothing_is_printed(db_session, capsys):
    investigation_id, _ = await at_concluding(db_session)
    assert (await advance(sessions_on(db_session), investigation_id)).reason == "done"
    assert capsys.readouterr().out == ""


async def test_only_one_stage_and_stages_the_driver_does_not_run(db_session):
    investigation_id, _ = await at_concluding(db_session)
    ended = await advance(sessions_on(db_session), investigation_id, only=Stage.LANDSCAPE_RETRIEVAL)
    assert ended == Ended("nothing", "full_evaluation")  # not that stage: nothing is run

    await advance(sessions_on(db_session), investigation_id)  # to results_ready
    assert await advance(sessions_on(db_session), investigation_id) == Ended("nothing", "results_ready")


def test_exit_codes():
    assert [Ended(reason).exit_code for reason in ("done", "stopped", "nothing", "failed", "account_refused",
                                                   "budget")] == [0, 0, 0, 1, 1, 3]
