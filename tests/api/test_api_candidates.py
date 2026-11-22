"""Tests for the idea routes (step 9a): detail, facet matrix, claims, Skeptic log and research package.
Real test database (rolled back), data from the real pipeline with fake Claude: no network, no cost."""

import uuid

import pytest

from src.ai.workflows.proposal import write_proposal
from src.db.models import Candidate, Evaluation
from tests.api.test_api_investigations import with_package
from tests.db.test_clarification_workflow import sessions_on
from tests.db.test_concluding import at_concluding
from tests.db.test_proposal import PROPOSAL_SETTINGS, FakeDesigner

pytestmark = [pytest.mark.anyio, pytest.mark.db]


async def test_the_detail_of_a_concluded_idea(client, db_session):
    _, candidate_id = await with_package(db_session)
    detail = (await client.get(f"/api/v1/candidates/{candidate_id}")).json()

    assert detail["preliminary"] is False and detail["evaluation"]["depth"] == "full"
    verdicts = {v["dimension"]: v for v in detail["verdicts"]}
    assert list(verdicts) == ["novelty", "significance", "feasibility", "recommendation"]
    assert (verdicts["novelty"]["label"], verdicts["novelty"]["rule"]) == ("Potential opportunity", "N4")
    assert (verdicts["significance"]["label"], verdicts["significance"]["rule"]) == ("Strong", "S2")
    assert all(v["frozen"] for v in detail["verdicts"])
    assert "sources: 2 (at least 3 needed)" in verdicts["novelty"]["limits"]
    assert detail["decision"]["action"] == "finalize"
    assert [f["kind"] for f in detail["facets"]][:3] == ["problem", "method", "data_setting"]
    assert detail["coverage"]["frozen"] is True and detail["coverage"]["read"] > 0
    assert detail["feasibility"]["total_weeks"] == 8
    for work in detail["closest_works"]:
        assert work["paper"]["title"].startswith("Paper ")  # titles come from the database


async def test_verdicts_before_the_evaluation_concludes_are_preliminary(client, db_session):
    _, evaluation_id = await at_concluding(db_session)  # verdicts not frozen yet
    evaluation = await db_session.get(Evaluation, evaluation_id)
    detail = (await client.get(f"/api/v1/candidates/{evaluation.candidate_id}")).json()
    assert detail["preliminary"] is True and len(detail["verdicts"]) == 4
    assert not any(v["frozen"] for v in detail["verdicts"])
    assert detail["coverage"]["frozen"] is False and detail["decision"] is None


async def test_matrix_claims_and_skeptic_log(client, db_session):
    _, candidate_id = await with_package(db_session)

    matrix = (await client.get(f"/api/v1/candidates/{candidate_id}/matrix")).json()
    positions = {f["position"] for f in matrix["facets"]}
    paper_ids = {p["id"] for p in matrix["papers"]}
    assert all(c["facet_position"] in positions and c["paper_id"] in paper_ids for c in matrix["cells"])
    assert {c["overlap"] for c in matrix["cells"]} <= {"same", "similar", "different", "not_addressed", "unknown"}

    claims = (await client.get(f"/api/v1/candidates/{candidate_id}/claims")).json()
    assert claims and all(c["status"] for c in claims)
    quotes = [q for c in claims for q in c["evidence"]]
    assert quotes and all(q["verified"] and q["paper"]["title"] for q in quotes)

    log = (await client.get(f"/api/v1/candidates/{candidate_id}/challenges")).json()
    assert log["rounds"][0]["round"] == 1 and isinstance(log["challenges"], list)


async def test_the_research_package(client, db_session):
    _, candidate_id = await with_package(db_session)
    package = (await client.get(f"/api/v1/candidates/{candidate_id}/proposal")).json()

    assert package["revision"] == 1 and package["checks"] == {"retagged": 1, "dropped_labels": 1}
    sections = package["sections"]
    assert [s["number"] for s in sections] == list(range(1, 18))
    assert sections[0]["items"] == ["RAG for Georgian QA: a one-GPU study"]
    written = {s["number"] for s in sections if s["statements"] is not None}
    assert written == {2, 4, 5, 9, 10, 12, 13, 15}
    evidence, unverified = sections[1]["statements"]
    assert (evidence["type"], evidence["label"], evidence["retagged"]) == ("evidence", "evidence", False)
    assert evidence["sources"] and evidence["sources"][0]["label"]  # rendered from the database
    assert (unverified["type"], unverified["label"], unverified["retagged"]) == ("to_verify", "to verify", True)
    assert sections[9]["statements"][0]["type"] == "hypothesis"

    investigation_id = (await db_session.get(Candidate, candidate_id)).investigation_id
    second = await write_proposal(sessions_on(db_session), investigation_id, candidate_id=candidate_id,
                                  settings=PROPOSAL_SETTINGS, model=FakeDesigner())
    latest = (await client.get(f"/api/v1/candidates/{candidate_id}/proposal")).json()
    assert (latest["revision"], latest["id"]) == (2, str(second.package.id))


async def test_missing_ideas_and_packages(client, db_session):
    assert (await client.get(f"/api/v1/candidates/{uuid.uuid4()}")).json()["error"]["message"] == "Idea not found."
    _, evaluation_id = await at_concluding(db_session)
    evaluation = await db_session.get(Evaluation, evaluation_id)
    response = await client.get(f"/api/v1/candidates/{evaluation.candidate_id}/proposal")
    assert response.status_code == 404 and response.json()["error"]["message"] == "Research package not found."
