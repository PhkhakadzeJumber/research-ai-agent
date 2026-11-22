"""Tests for starting and controlling runs through the API (step 9b): new investigations (typed and from a document),
the clarification, stop and resume, the research package and the exports. Real test database (rolled back), fake
Claude, runs drained by the test (tests/api/conftest.py ManualRuns): no network, no cost."""

import json
import uuid

import pytest
from sqlalchemy import func, select

from src.api.runs import Runs
from src.db.models import Candidate, Evaluation, Investigation, LlmCall, UserDocument
from src.db.repositories.intakes import find_intake
from tests.ai.test_field_brief import VALID as BRIEF
from tests.ai.test_structured_idea_schema import VALID as READING
from tests.db.test_clarification_workflow import REVISED, sessions_on, waiting
from tests.db.test_concluding import at_concluding
from tests.db.test_proposal import PROPOSAL_SETTINGS, FakeDesigner, finalist_investigation
from tests.fakes import ScriptedLlm, answer

pytestmark = [pytest.mark.anyio, pytest.mark.db]

IDEA = {"mode": "evaluate", "idea": "RAG for Georgian QA with small LMs", "seed_papers": ["2401.12345"],
        "constraints": {"available time": "10 weeks"}}


async def get(client, investigation_id):
    return (await client.get(f"/api/v1/investigations/{investigation_id}")).json()


async def count(db_session, model) -> int:
    return (await db_session.execute(select(func.count()).select_from(model))).scalar_one()


# --- new investigations -----------------------------------------------------------------------------------

async def test_a_new_idea_is_read_in_the_background_then_waits_for_the_user(client, runs, db_session):
    runs.models.idea_model = ScriptedLlm.with_answers(answer(json.dumps(READING)))
    response = await client.post("/api/v1/investigations", json=IDEA)
    assert response.status_code == 202
    started = response.json()
    # the run's cost cap comes from the plan (step P2): 25 credits x $0.04 x 1.5 = $1.50
    assert (started["run"], started["waiting_for"], started["budget_usd"]) == ("running", None, 1.5)

    await runs.drain()
    now = await get(client, started["id"])
    assert (now["stage"], now["run"], now["waiting_for"]) == ("awaiting_clarification", "idle", "clarification")
    assert now["input"] == {"idea": IDEA["idea"], "seed_papers": ["2401.12345"]}
    assert now["constraints"] == {"available time": "10 weeks"} and now["cost_usd"] > 0
    clarification = (await client.get(f"/api/v1/investigations/{started['id']}/clarification")).json()
    assert clarification["idea"]["research_question"] == READING["research_question"]


async def test_bad_requests_store_nothing(client, runs, db_session):
    for body, code in [({**IDEA, "budget_usd": 500}, "budget_too_high"),
                       ({**IDEA, "idea": "x" * 2001}, "invalid_request"),
                       ({**IDEA, "unknown": 1}, "invalid_request"),
                       ({"mode": "discover", "field": "NLP", "count": 11}, "invalid_request"),
                       ({"mode": "something"}, "invalid_request")]:
        response = await client.post("/api/v1/investigations", json=body)
        assert (response.status_code, response.json()["error"]["code"]) == (422, code), body
    assert await count(db_session, Investigation) == 0 and runs.queued == []


async def test_a_new_discovery_and_its_clarification(client, runs, db_session):
    runs.models.idea_model = ScriptedLlm.with_answers(answer(json.dumps(BRIEF)))
    body = {"mode": "discover", "field": "NLP", "interests": "LLM agents", "exclusions": ["medical"], "count": 3,
            "max_light": 4}
    started = (await client.post("/api/v1/investigations", json=body)).json()
    assert started["budget_usd"] == 3.6  # 60 credits x $0.04 x 1.5 (step P2)
    await runs.drain()
    investigation = await db_session.get(Investigation, uuid.UUID(started["id"]))
    assert investigation.budget["light_evaluations"] == 4
    assert investigation.input == {"field": "NLP", "count": 3, "interests": "LLM agents", "exclusions": ["medical"]}

    url = f"/api/v1/investigations/{started['id']}/clarification"
    bad = await client.post(url, json={"edits": {"0": "x"}, "confirm": True})
    assert (bad.status_code, bad.json()["error"]["code"]) == (422, "invalid_request")  # edits are Mode 1's
    response = await client.post(url, json={"answers": [""], "remove_subtopics": [4], "confirm": True})
    assert response.status_code == 202
    runs.stop(investigation.id)  # stop before the landscape search: this test makes no searches
    await runs.drain()
    await db_session.refresh(investigation)
    assert investigation.stage == "landscape_retrieval"
    intake = await find_intake(db_session, investigation.id)
    assert intake.confirmed_at is not None
    assert [s["name"] for s in intake.user_edits["final_brief"]["subtopics"]] == [
        s["name"] for s in BRIEF["subtopics"][:3]]
    assert runs.last_ended[investigation.id].reason == "stopped"


async def test_a_document_needs_consent_and_a_supported_file(client, runs, db_session):
    files = {"file": ("draft.md", b"# Draft\n\n## Method\n\nRAG for Georgian QA with small models.", "text/markdown")}
    refused = await client.post("/api/v1/investigations/document", files=files, data={"consent": "false"})
    assert (refused.status_code, refused.json()["error"]["code"]) == (422, "consent_required")
    wrong = await client.post("/api/v1/investigations/document", data={"consent": "true"},
                              files={"file": ("draft.docx", b"PK...", "application/octet-stream")})
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (422, "invalid_document")
    big = await client.post("/api/v1/investigations/document", data={"consent": "true"},
                            files={"file": ("big.txt", b"a" * (10 * 1024 * 1024 + 1), "text/plain")})
    assert big.status_code == 413
    assert await count(db_session, Investigation) == 0

    response = await client.post("/api/v1/investigations/document", files=files, data={
        "consent": "true", "arxiv": "2401.00001", "constraints": json.dumps({"compute": "one GPU"})})
    assert response.status_code == 202
    started = response.json()
    assert started["summary"] == "Your document · draft.md" and started["run"] == "running"
    document = (await db_session.execute(select(UserDocument))).scalar_one()
    assert (document.file_name, document.published, document.external_ids) == ("draft.md", True,
                                                                                {"arxiv": "2401.00001"})
    assert document.consent_at is not None and len(runs.queued) == 1  # Eureka's reading of it is queued


# --- the clarification ------------------------------------------------------------------------------------

async def test_answering_and_editing_the_clarification(client, runs, db_session):
    investigation = await waiting(db_session)
    url = f"/api/v1/investigations/{investigation.id}/clarification"
    for body, status, code in [({"answers": ["a", "b"], "confirm": True}, 422, "invalid_request"),  # 1 question
                               ({"edits": {"9": "x"}, "confirm": True}, 422, "invalid_change"),
                               ({"remove_subtopics": [1], "confirm": True}, 422, "invalid_request"),
                               ({"answers": []}, 422, "invalid_request")]:  # confirm is required
        response = await client.post(url, json=body)
        assert (response.status_code, response.json()["error"]["code"]) == (status, code), body

    runs.models.idea_model = ScriptedLlm.with_answers(answer(json.dumps(REVISED)))
    response = await client.post(url, json={"answers": ["Georgian and Armenian"],
                                            "edits": {"1": "retrieval-augmented generation with BM25"},
                                            "confirm": True})
    assert response.status_code == 202
    again = await client.post(url, json={"confirm": True})
    assert (again.status_code, again.json()["error"]["code"]) == (409, "already_running")
    runs.stop(investigation.id)  # stop before the (paid) evaluation starts
    await runs.drain()

    await db_session.refresh(investigation)
    assert investigation.stage == "full_evaluation"
    [candidate] = (await db_session.execute(select(Candidate).where(
        Candidate.investigation_id == investigation.id))).scalars().all()
    assert candidate.research_question == REVISED["research_question"]  # the answers were applied
    intake = await find_intake(db_session, investigation.id)
    # stored numbered from 1, as the user sees facets (position 1 = facet 2); a confirmation in the API is explicit
    assert intake.user_edits["facet_edits"] == {"2": "retrieval-augmented generation with BM25"}
    assert intake.user_edits["confirmed_explicitly"] is True
    revise_calls = (await db_session.execute(select(LlmCall).where(
        LlmCall.investigation_id == investigation.id, LlmCall.prompt_version.like("%revise%")))).scalars().all()
    assert len(revise_calls) == 1
    detail = await get(client, investigation.id)
    assert (detail["run"], detail["waiting_for"]) == ("idle", None)  # stopped: resumable

    done = await client.post(url, json={"confirm": True})
    assert (done.status_code, done.json()["error"]["code"]) == (409, "not_waiting")


# --- stop and resume --------------------------------------------------------------------------------------

async def test_resume_runs_on_to_the_results(client, runs, db_session):
    investigation_id, _ = await at_concluding(db_session)  # the user's idea, at its last (free) step
    url = f"/api/v1/investigations/{investigation_id}"
    not_running = await client.post(f"{url}/stop")
    assert (not_running.status_code, not_running.json()["error"]["code"]) == (409, "not_running")
    too_much = await client.post(f"{url}/resume", json={"budget_usd": 21})
    assert too_much.json()["error"]["code"] == "budget_too_high"

    # a budget can only lower the cap the plan sets (step P2): asking for $8 leaves cost so far + 25 x $0.04 x 1.5
    response = await client.post(f"{url}/resume", json={"budget_usd": 8})
    resumed = response.json()
    assert response.status_code == 202 and resumed["budget_usd"] == pytest.approx(resumed["cost_usd"] + 1.5, abs=1e-6)
    assert (await client.post(f"{url}/resume")).json()["error"]["code"] == "already_running"
    stopping = (await client.post(f"{url}/stop")).json()
    assert stopping["run"] == "stopping"
    await runs.drain()
    assert (await get(client, investigation_id))["stage"] == "full_evaluation"  # stopped before anything ran

    await client.post(f"{url}/resume")
    await runs.drain()
    detail = await get(client, investigation_id)
    assert (detail["stage"], detail["status"], detail["run"]) == ("results_ready", "completed", "idle")
    nothing = await client.post(f"{url}/resume")
    assert (nothing.status_code, nothing.json()["error"]["code"]) == (409, "nothing_to_resume")


async def test_a_failing_run_is_recorded_and_never_takes_the_server_down(db_session):
    runs = Runs(sessions_on(db_session))

    async def broken():
        raise RuntimeError("boom")

    runs.start(uuid.uuid4(), "testing", broken)
    [task] = list(runs._tasks)
    await task
    [(investigation_id, ended)] = runs.last_ended.items()
    assert ended == "RuntimeError: boom" and runs.state(investigation_id) == "idle"


# --- the research package and the exports -----------------------------------------------------------------

async def test_writing_and_exporting_the_research_package(client, runs, db_session):
    investigation_id = await finalist_investigation(db_session)
    runs.models.proposal_model, runs.models.proposal_settings = FakeDesigner(), PROPOSAL_SETTINGS
    [candidate] = (await db_session.execute(select(Candidate).where(
        Candidate.investigation_id == investigation_id))).scalars().all()
    missing = await client.get(f"/api/v1/candidates/{candidate.id}/proposal/export")
    assert missing.status_code == 404

    response = await client.post(f"/api/v1/candidates/{candidate.id}/proposal")
    assert response.status_code == 202 and response.json()["run"] == "running"
    await runs.drain()
    package = (await client.get(f"/api/v1/candidates/{candidate.id}/proposal")).json()
    assert package["revision"] == 1

    exported = await client.get(f"/api/v1/candidates/{candidate.id}/proposal/export")
    assert exported.headers["content-type"].startswith("text/markdown")
    assert "attachment" in exported.headers["content-disposition"]
    assert "## 13. Experiment design" in exported.text
    report = await client.get(f"/api/v1/investigations/{investigation_id}/report?format=md")
    assert report.status_code == 200 and report.text.startswith("# Eureka report: ")
    assert (await client.get(f"/api/v1/investigations/{investigation_id}/report?format=pdf")).status_code == 422


async def test_a_package_needs_a_finalist_with_results(client, runs, db_session):
    _, evaluation_id = await at_concluding(db_session)
    candidate_id = (await db_session.get(Evaluation, evaluation_id)).candidate_id
    response = await client.post(f"/api/v1/candidates/{candidate_id}/proposal")
    assert (response.status_code, response.json()["error"]["code"]) == (409, "not_a_finalist")
