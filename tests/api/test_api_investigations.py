"""Tests for the investigation routes (step 9a): list, detail, clarification, results, landscape and cost.
Real test database (rolled back), data from the real pipeline with fake Claude: no network, no cost."""

import hashlib
import uuid

import pytest

from src.ai.workflows.proposal import write_proposal
from src.db.models import OpportunityItem, UserDocument
from src.db.repositories.runs import create_investigation
from src.timeutil import now
from tests.db.test_clarification_workflow import sessions_on, waiting
from tests.db.test_proposal import PROPOSAL_SETTINGS, FakeDesigner, finalist_investigation
from tests.db.test_results import evaluated

pytestmark = [pytest.mark.anyio, pytest.mark.db]


async def with_package(db_session):
    investigation_id = await finalist_investigation(db_session)
    outcome = await write_proposal(sessions_on(db_session), investigation_id, settings=PROPOSAL_SETTINGS,
                                   model=FakeDesigner())
    return investigation_id, outcome.candidate.id


async def test_the_list_and_one_investigation(client, db_session):
    investigation_id, _ = await with_package(db_session)
    discover = await create_investigation(db_session, mode="discover", complexity="beginner",
                                          input={"field": "NLP"})

    page = (await client.get("/api/v1/investigations")).json()
    assert page["total"] == 2 and {i["mode"] for i in page["items"]} == {"evaluate", "discover"}
    mode1 = next(i for i in page["items"] if i["mode"] == "evaluate")
    assert mode1["title"] == "RAG for hallucination reduction in Georgian QA with small LMs"
    assert (mode1["summary"], mode1["status"], mode1["stage"]) == ("Proposal ready", "completed", "results_ready")
    assert mode1["cost_usd"] > 0 and mode1["budget_usd"] == 5.0
    assert mode1["created_at"].endswith("+04:00")  # Tbilisi time
    mode2 = next(i for i in page["items"] if i["mode"] == "discover")
    assert (mode2["title"], mode2["summary"]) == ("Ideas in NLP", "Mapping the field")

    only = (await client.get("/api/v1/investigations?mode=discover")).json()
    assert [i["id"] for i in only["items"]] == [str(discover.id)] and only["total"] == 1
    first = (await client.get("/api/v1/investigations?limit=1")).json()
    assert len(first["items"]) == 1 and first["total"] == 2

    detail = (await client.get(f"/api/v1/investigations/{investigation_id}")).json()
    assert detail["input"] == {"idea": "RAG for Georgian QA with small LMs"}
    assert detail["constraints"] == {"available_time": "10 weeks"} and detail["document"] is None

    missing = await client.get(f"/api/v1/investigations/{uuid.uuid4()}")
    assert missing.status_code == 404 and missing.json()["error"]["message"] == "Investigation not found."


async def test_results_cost_and_landscape_of_a_mode1_investigation(client, db_session):
    investigation_id, candidate_id = await with_package(db_session)
    results = (await client.get(f"/api/v1/investigations/{investigation_id}/results")).json()
    [top] = results["finalists"]
    assert (top["rank"], top["candidate"]["id"], top["candidate"]["status"]) == (1, str(candidate_id), "proposal_ready")
    assert top["verdicts"]["recommendation"] == {"category": "pursue", "label": "Pursue"}
    assert top["verdicts"]["novelty"] == {"category": "potential_opportunity", "label": "Potential opportunity"}
    assert top["has_package"] is True and top["depth"] == "full" and top["supporting_quotes"] == 2

    cost = (await client.get(f"/api/v1/investigations/{investigation_id}/cost")).json()
    assert {line["role"] for line in cost["by_role"]} >= {"idea_analyst", "paper_reader", "proposal"}
    # the total is what budget checks use: investigations.cost_usd, kept to 4 decimals and rounded each time a
    # call is added (calls keep 6), so it may differ from the sum of the calls by up to $0.00005 per call
    calls = sum(line["calls"] for line in cost["by_role"])
    assert abs(sum(line["cost_usd"] for line in cost["by_role"]) - cost["total_usd"]) <= 0.00005 * calls

    landscape = await client.get(f"/api/v1/investigations/{investigation_id}/landscape")
    assert landscape.status_code == 409 and landscape.json()["error"]["code"] == "wrong_mode"


async def test_mode2_results_and_landscape(client, db_session):
    investigation = await create_investigation(db_session, mode="discover", complexity="beginner",
                                               input={"field": "NLP", "count": 3})
    await evaluated(db_session, investigation, "Second", "finalist", "full", feasibility="feasible_with_adjustments")
    await evaluated(db_session, investigation, "Best", "proposal_ready", "full")
    await evaluated(db_session, investigation, "Light only", "advanced", "light")
    rejected = await evaluated(db_session, investigation, "Old news", "rejected", "full",
                               novelty="previously_investigated")
    rejected.rejection_reason = "Rule 3: previously investigated"
    db_session.add_all([
        OpportunityItem(investigation_id=investigation.id, type="limitation_cluster",
                        label="Agents fail outside English", counts={"papers": 5, "author_groups": 4}),
        OpportunityItem(investigation_id=investigation.id, type="limitation_cluster", label="Judges disagree",
                        counts={"papers": 3, "author_groups": 3}),
        OpportunityItem(investigation_id=investigation.id, type="contradiction", label="RAG helps vs hurts"),
    ])
    await db_session.flush()

    results = (await client.get(f"/api/v1/investigations/{investigation.id}/results")).json()
    assert [r["candidate"]["title"] for r in results["finalists"]] == ["Best", "Second"]
    assert results["finalists"][0]["ahead_because"] == "feasibility: feasible before feasible with adjustments"
    assert [r["candidate"]["title"] for r in results["advanced"]] == ["Light only"]
    assert results["others"] == [{"candidate": results["others"][0]["candidate"],
                                  "why": "Rule 3: previously investigated"}]
    summary = (await client.get(f"/api/v1/investigations/{investigation.id}")).json()["summary"]
    assert summary == "4 ideas · 2 finalists · package ready"

    landscape = (await client.get(f"/api/v1/investigations/{investigation.id}/landscape")).json()
    assert landscape["field"] == "NLP" and landscape["subtopics"] == []  # no confirmed field brief in this setup
    assert [(p["label"], p["papers"]) for p in landscape["open_problems"]] == [
        ("Agents fail outside English", 5), ("Judges disagree", 3)]
    assert [c["label"] for c in landscape["contradictions"]] == ["RAG helps vs hurts"]


async def test_clarification_of_an_idea_read_from_a_document(client, db_session):
    investigation = await waiting(db_session)
    clarification = (await client.get(f"/api/v1/investigations/{investigation.id}/clarification")).json()
    assert clarification["confirmed"] is False and clarification["field_brief"] is None
    facets = clarification["idea"]["facets"]
    assert facets[0]["position"] == 0 and all(f["from_section"] is None for f in facets)
    assert clarification["questions"] and set(clarification["questions"][0]) == {"question", "if_skipped"}

    text = "# Draft\n\nOur contribution..."
    db_session.add(UserDocument(
        investigation_id=investigation.id, file_name="draft.pdf", media_type="application/pdf",
        size_bytes=len(text), sha256=hashlib.sha256(text.encode()).hexdigest(), text=text,
        sections=[{"label": "D1"}, {"label": "D2"}, {"label": "D3"}], consent_at=now(), published=False,
        reading={"quotes": 2, "verified_quotes": 2},
        idea_extras={"facet_sources": {facets[0]["text"]: "D2"},
                     "novelty_claims": [{"claim": "No one evaluated this for Georgian.", "section": "D3"}]}))
    await db_session.flush()

    clarification = (await client.get(f"/api/v1/investigations/{investigation.id}/clarification")).json()
    assert clarification["idea"]["facets"][0]["from_section"] == "D2"
    assert clarification["idea"]["novelty_claims"] == [{"claim": "No one evaluated this for Georgian.",
                                                        "section": "D3"}]
    detail = (await client.get(f"/api/v1/investigations/{investigation.id}")).json()
    assert detail["document"] == {"file_name": "draft.pdf", "media_type": "application/pdf", "sections": 3,
                                  "quotes": 2, "verified_quotes": 2, "published": False}
    assert detail["summary"] == "Your document · draft.pdf"
    assert "text" not in detail["document"]  # the document's text never leaves the server


async def test_clarification_before_eureka_read_anything(client, db_session):
    investigation = await create_investigation(db_session, mode="evaluate", complexity="beginner", input={"idea": "x"})
    response = await client.get(f"/api/v1/investigations/{investigation.id}/clarification")
    assert response.status_code == 409 and response.json()["error"]["code"] == "not_ready"
