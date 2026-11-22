"""Tests for the research package (A7, step 7): the output schema, code's checks on A7's statements, writing and
storing a package for a finalist, its 17 sections, and the command's Markdown. Real test database, fake Claude."""

import json
import re
from types import SimpleNamespace

import pytest
from google.adk.models.base_llm import BaseLlm
from pydantic import PrivateAttr, ValidationError
from sqlalchemy import select

from scripts.proposal import to_markdown
from src.ai.schemas.proposal import ProposalOutput
from src.ai.workflows.concluding import conclude_step, finish_investigation
from src.ai.workflows.proposal import NoCandidateForProposal, check_statements, package_sections, write_proposal
from src.ai.workflows.ranking import ranked_results
from src.config import RoleLlmSettings
from src.db.models import Candidate, Investigation, LlmCall, Paper, ResearchPackage
from tests.db.test_clarification_workflow import sessions_on
from tests.db.test_concluding import at_concluding
from tests.fakes import model_response

PROPOSAL_SETTINGS = RoleLlmSettings(role="proposal", model="claude-opus-5-5", effort="medium", max_tokens=20000)
_CLAIM = re.compile(r'<claim label="(C\d+)" kind="[^"]*" status="([^"]*)">')
_PAPER = re.compile(r'<paper label="(P\d+)"')


def statement(text, kind="interpretation", sources=()):
    return {"text": text, "type": kind, "sources": list(sources)}


def output(**sections):
    base = {"title": "A small study", "background": [], "motivation": [], "contribution": [], "methodology": [],
            "hypotheses": [], "tools": [], "experiment_design": [], "limitations": [],
            "recommendation_explanation": "Pursue: nothing blocks it."}
    return {**base, **sections}


# --- shape and code's checks (no database) -----------------------------------------------------------------

def test_the_output_shape():
    assert ProposalOutput.model_validate(output()).title == "A small study"
    with pytest.raises(ValidationError, match="pattern"):
        ProposalOutput.model_validate(output(background=[statement("x", sources=["paper 3"])]))
    with pytest.raises(ValidationError, match="type"):
        ProposalOutput.model_validate(output(background=[statement("x", kind="fact")]))


def test_unsupported_evidence_becomes_to_verify_and_unknown_labels_are_dropped():
    claims = {"C1": SimpleNamespace(id="c1", status="verified"), "C2": SimpleNamespace(id="c2", status="refuted")}
    papers = {"P1": SimpleNamespace(id="p1", title="Verified paper", year=2025),
              "P2": SimpleNamespace(id="p2", title="Unverified paper", year=2024)}
    supported = {"C1", "P1"}
    parsed = ProposalOutput.model_validate(output(background=[
        statement("Backed by a verified claim.", "evidence", ["C1"]),
        statement("Backed by a refuted claim only.", "evidence", ["C2"]),
        statement("P2 says so, but nothing verified backs it.", "evidence", ["P2", "P99"]),
        statement("A guess.", "hypothesis"),
    ]))
    sections, retagged, dropped = check_statements(parsed, claims, papers, supported)
    kinds = [item["type"] for item in sections["background"]]
    assert kinds == ["evidence", "to_verify", "to_verify", "hypothesis"]
    assert (retagged, dropped) == (2, 1)  # P99 isn't in the ledger
    assert sections["background"][2]["text"].startswith('"Unverified paper" (2024) says so')  # labels become titles
    assert sections["background"][0]["sources"] == [{"kind": "claim", "id": "c1"}]


# --- writing a package (database) --------------------------------------------------------------------------

class FakeDesigner(BaseLlm):
    """A fake A7: cites the first verified claim and first paper of the packet as evidence, plus one unknown label."""

    model: str = "claude-opus-5-5"
    _requests: list = PrivateAttr(default_factory=list)

    @property
    def requests(self):
        return self._requests

    async def generate_content_async(self, llm_request, stream=False):
        text = "\n".join(p.text for c in llm_request.contents for p in c.parts if p.text)
        self._requests.append(text)
        verified = [label for label, status in _CLAIM.findall(text) if status in ("verified", "defended")]
        papers = _PAPER.findall(text)
        evidence = statement("The problem is documented.", "evidence", verified[:1] or papers[:1])
        yield model_response(json.dumps(output(
            title="RAG for Georgian QA: a one-GPU study",
            background=[evidence, statement("Nobody tested it yet.", "evidence", ["C99"])],
            methodology=[statement(f"Reuse the retrieval setup of {papers[0] if papers else 'P1'}.", "interpretation",
                                   papers[:1])],
            hypotheses=[statement("RAG lowers the hallucination rate.", "hypothesis")],
            limitations=[statement("Translated questions may carry artifacts.", "interpretation")],
        )))


async def finalist_investigation(db_session):
    investigation_id, evaluation_id = await at_concluding(db_session)
    await conclude_step(sessions_on(db_session), evaluation_id)
    outcome = await finish_investigation(sessions_on(db_session), investigation_id)
    assert outcome.candidate_status == "finalist"
    return investigation_id


@pytest.mark.anyio
@pytest.mark.db
async def test_a_package_is_written_for_the_top_finalist(db_session):
    investigation_id = await finalist_investigation(db_session)
    designer = FakeDesigner()
    outcome = await write_proposal(sessions_on(db_session), investigation_id, settings=PROPOSAL_SETTINGS,
                                   model=designer)

    packet = designer.requests[0]
    assert "<verdicts>" in packet and "<claims>" in packet and "<feasibility>" in packet and "<user>" in packet
    package = await db_session.get(ResearchPackage, outcome.package.id)
    assert (package.revision, package.prompt_version.endswith("proposal@1")) == (1, True)
    assert package.sections["background"][1]["type"] == "to_verify"  # C99 isn't in the ledger: dropped, re-tagged
    assert outcome.dropped_labels == 1
    candidate = await db_session.get(Candidate, outcome.candidate.id)
    await db_session.refresh(candidate)
    assert candidate.status == "proposal_ready"
    investigation = await db_session.get(Investigation, investigation_id)
    await db_session.refresh(investigation)
    assert (investigation.stage, investigation.status) == ("results_ready", "completed")
    calls = (await db_session.execute(select(LlmCall).where(LlmCall.investigation_id == investigation_id,
                                                            LlmCall.role == "proposal"))).scalars().all()
    assert len(calls) == 1

    sections = await package_sections(db_session, package)
    assert [s["number"] for s in sections] == list(range(1, 18))
    assert sections[0]["items"] == ["RAG for Georgian QA: a one-GPU study"]
    assert sections[9]["items"][0].startswith("[hypothesis] RAG lowers")
    assert sections[16]["items"][0].startswith("Recommendation: ")
    titles = {p.title for p in (await db_session.execute(select(Paper))).scalars()}
    assert all(any(t in ref for t in titles) for ref in sections[15]["items"])  # references only from the database

    markdown = to_markdown(candidate, package, sections)
    assert markdown.count("\n## ") == 16 and "## 13. Experiment design" in markdown
    retagged = package.sections["checks"]["retagged"]
    assert retagged >= 1 and f"{retagged} statement(s) re-tagged 'to verify'" in markdown


@pytest.mark.anyio
@pytest.mark.db
async def test_a_second_package_is_a_new_revision_and_finalists_are_required(db_session):
    investigation_id = await finalist_investigation(db_session)
    first = await write_proposal(sessions_on(db_session), investigation_id, settings=PROPOSAL_SETTINGS,
                                 model=FakeDesigner())
    second = await write_proposal(sessions_on(db_session), investigation_id, candidate_id=first.candidate.id,
                                  settings=PROPOSAL_SETTINGS, model=FakeDesigner())
    assert (first.package.revision, second.package.revision) == (1, 2)

    candidate = await db_session.get(Candidate, first.candidate.id)
    candidate.status = "rejected"
    await db_session.flush()
    with pytest.raises(NoCandidateForProposal, match="rejected"):
        await write_proposal(sessions_on(db_session), investigation_id, candidate_id=candidate.id,
                             settings=PROPOSAL_SETTINGS, model=FakeDesigner())


def test_the_explanation_may_run_to_2000_characters():
    # raised from 800 after the step 7 live run, where a longer paragraph forced a paid rewrite
    assert len(ProposalOutput.model_validate(output(recommendation_explanation="x" * 2000)).recommendation_explanation) == 2000
    with pytest.raises(ValidationError, match="at most 2000"):
        ProposalOutput.model_validate(output(recommendation_explanation="x" * 2001))


@pytest.mark.anyio
@pytest.mark.db
async def test_the_idea_with_a_package_is_still_a_ranked_finalist(db_session):
    # found in step 9a: `proposal_ready` wasn't counted as a finalist, so the idea dropped out of the results
    investigation_id = await finalist_investigation(db_session)
    outcome = await write_proposal(sessions_on(db_session), investigation_id, settings=PROPOSAL_SETTINGS,
                                   model=FakeDesigner())
    results = await ranked_results(db_session, investigation_id)
    assert [r.candidate.id for r in results.finalists] == [outcome.candidate.id]
    assert results.finalists[0].candidate.status == "proposal_ready"
