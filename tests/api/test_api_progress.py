"""Tests for the progress screen's routes (step 9c): the activity list, the progress summary (stage rail, counters,
last run), and the live event stream (replay, Last-Event-ID, live events while a run works, the end). Real test
database (rolled back), fake Claude, runs drained by the test: no network, no cost."""

import asyncio
import json

import pytest

from src.api.routes.progress import event_stream
from tests.ai.test_structured_idea_schema import VALID as READING
from tests.db.test_concluding import at_concluding
from tests.fakes import ScriptedLlm, answer

pytestmark = [pytest.mark.anyio, pytest.mark.db]


def parse(stream: str) -> list[dict]:
    """The events in an SSE text (comments skipped)."""
    events = []
    for block in stream.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        if lines:
            events.append({"id": int(lines["id"]), "event": lines["event"], **json.loads(lines["data"])})
    return events


async def finished_run(client, runs, db_session):
    """The user's idea at its last step, resumed through the API and run to the results."""
    investigation_id, _ = await at_concluding(db_session)
    await client.post(f"/api/v1/investigations/{investigation_id}/resume")
    await runs.drain()
    return investigation_id


async def test_the_activity_list_and_the_last_run(client, runs, db_session):
    investigation_id = await finished_run(client, runs, db_session)
    activity = (await client.get(f"/api/v1/investigations/{investigation_id}/activity")).json()
    kinds = [e["kind"] for e in activity["items"]]
    assert kinds == ["step_started", "step_done", "candidate", "ended"]
    assert [e["seq"] for e in activity["items"]] == [1, 2, 3, 4] and activity["last_seq"] == 4
    assert activity["items"][-1]["message"] == "Done: the results are ready."
    assert activity["items"][1]["at"].endswith("+04:00") and activity["items"][1]["cost_usd"] > 0
    newer = (await client.get(f"/api/v1/investigations/{investigation_id}/activity?after=2")).json()
    assert [e["seq"] for e in newer["items"]] == [3, 4]

    detail = (await client.get(f"/api/v1/investigations/{investigation_id}")).json()
    assert detail["last_run"]["reason"] == "done" and detail["last_run"]["message"].startswith("Done")


async def test_the_progress_summary(client, runs, db_session):
    investigation_id, _ = await at_concluding(db_session)  # paused at its last step (nothing running)
    progress = (await client.get(f"/api/v1/investigations/{investigation_id}/progress")).json()
    rail = {stage["key"]: stage["status"] for stage in progress["rail"]}
    assert (progress["mode"], progress["step"], progress["run"]) == ("evaluate", "concluding", "idle")
    assert rail["searching"] == rail["skeptic"] == "done" and rail["verdicts"] == "paused"
    assert rail["loop"] == rail["results"] == "todo"
    counters = progress["counters"]
    assert counters["papers_found"] >= counters["relevant"] >= counters["read"] > 0
    assert counters["quotes_verified"] > 0 and counters["ideas"] == 1 and counters["finalists"] == 0
    assert progress["last_run"] is None and progress["last_seq"] == 0

    await client.post(f"/api/v1/investigations/{investigation_id}/resume")
    await runs.drain()
    progress = (await client.get(f"/api/v1/investigations/{investigation_id}/progress")).json()
    rail = {stage["key"]: stage["status"] for stage in progress["rail"]}
    assert rail["results"] == "done" and rail["loop"] == "skipped"  # no refinements or alternatives were needed
    assert progress["counters"]["finalists"] == 1 and progress["last_run"]["reason"] == "done"


async def test_a_new_idea_reports_its_reading(client, runs, db_session):
    runs.models.idea_model = ScriptedLlm.with_answers(answer(json.dumps(READING)))
    started = (await client.post("/api/v1/investigations", json={"mode": "evaluate", "idea": "RAG for Georgian QA"}))
    await runs.drain()
    investigation_id = started.json()["id"]
    messages = [e["message"] for e in (await client.get(
        f"/api/v1/investigations/{investigation_id}/activity")).json()["items"]]
    assert messages[0] == "Reading your idea"
    assert messages[1].startswith(f"Read “{READING['title']}”: 5 parts, 1 question for you")
    assert messages[2] == "Waiting for your answers to the clarification."
    progress = (await client.get(f"/api/v1/investigations/{investigation_id}/progress")).json()
    assert [s["status"] for s in progress["rail"][:3]] == ["done", "active", "todo"]


async def test_the_stream_replays_stored_events_and_ends_when_nothing_runs(client, runs, db_session):
    investigation_id = await finished_run(client, runs, db_session)
    url = f"/api/v1/investigations/{investigation_id}/events"
    response = await client.get(url)
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse(response.text)
    assert [(e["id"], e["event"]) for e in events] == [(1, "step_started"), (2, "step_done"), (3, "candidate"),
                                                        (4, "ended")]
    assert events[0]["seq"] == 1 and events[0]["message"].startswith("Concluding")

    reconnect = parse((await client.get(url, headers={"Last-Event-ID": "2"})).text)
    assert [e["id"] for e in reconnect] == [3, 4]
    assert [e["id"] for e in parse((await client.get(f"{url}?after=3")).text)] == [4]


async def test_the_stream_sends_live_events_until_the_run_ends(client, runs, db_session):
    investigation_id, _ = await at_concluding(db_session)
    await client.post(f"/api/v1/investigations/{investigation_id}/resume")  # queued: the run is "running"
    stream = event_stream(runs, investigation_id, after=0, heartbeat=0.05)

    async def read_all() -> list[str]:
        return [chunk async for chunk in stream]

    reader = asyncio.create_task(read_all())
    for _ in range(20):  # let the stream replay (nothing yet) and start waiting for live events
        await asyncio.sleep(0)
    await runs.drain()  # the run works now; its events reach the open stream as they are stored
    chunks = await asyncio.wait_for(reader, timeout=5)
    events = parse("".join(chunks))
    assert [e["event"] for e in events] == ["step_started", "step_done", "candidate", "ended"]
    assert events[-1]["data"]["reason"] == "done"
    assert runs._listeners == {}  # the stream unsubscribed when it ended


async def test_a_failed_run_explains_itself(client, runs, db_session):
    investigation_id, _ = await at_concluding(db_session)

    async def broken():
        raise RuntimeError("boom")

    runs.start(investigation_id, "testing", broken)
    await runs.drain()
    detail = (await client.get(f"/api/v1/investigations/{investigation_id}")).json()
    assert detail["last_run"]["reason"] == "failed"
    assert detail["last_run"]["message"] == "Something went wrong while testing. Nothing is lost: resume to try again."
    assert "boom" not in detail["last_run"]["message"]  # internal details stay in the server log
