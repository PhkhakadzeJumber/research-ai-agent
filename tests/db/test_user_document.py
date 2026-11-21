"""Tests for understanding the user's document (src/ai/workflows/user_document.py, step 6a.1). Real test database
(rolled back), fake Claude."""

import json

import pytest
from sqlalchemy import func, select

from src.ai.workflows.user_document import DocumentError, attach_document, prepare_document, understand_document
from src.db.models import Intake, Investigation, LlmCall, Paper, PaperAnalysis, Quote, UserDocument
from src.db.repositories.runs import create_investigation
from src.timeutil import now
from tests.ai.test_structured_idea_schema import VALID
from tests.ai.test_user_document_parsing import DRAFT, document_output
from tests.db.test_clarification_workflow import sessions_on
from tests.fakes import ScriptedLlm, answer

pytestmark = [pytest.mark.anyio, pytest.mark.db]

READING = answer(json.dumps({
    "research_questions": [{"statement": "Does RAG help small models in Georgian?",
                            "quote": {"text": "We compare closed-book and RAG settings on three sub-3B models.",
                                      "chunk_id": "D3"}}],
    "future_work": [{"statement": "Nobody has tested Georgian.",
                     "quote": {"text": "a sentence that isn't in the draft", "chunk_id": "D2"}}],
    "methods": ["retrieval-augmented generation"], "contribution_type": "empirical_study",
}))


async def with_document(db_session, tmp_path, *, published=None, ids=None):
    path = tmp_path / "draft.md"
    path.write_text(DRAFT, encoding="utf-8")
    investigation = await create_investigation(db_session, mode="evaluate", complexity="beginner",
                                               input={"idea": "", "field": "NLP"},
                                               constraints={"available_time": "10 weeks"})
    document = await attach_document(db_session, investigation.id, prepare_document(path), consent_at=now(),
                                     published=published, external_ids=ids)
    return investigation, document


async def test_nothing_is_stored_without_consent(db_session, tmp_path):
    path = tmp_path / "draft.md"
    path.write_text(DRAFT, encoding="utf-8")
    investigation = await create_investigation(db_session, mode="evaluate", complexity="beginner", input={"idea": ""})
    with pytest.raises(DocumentError, match="hasn't agreed"):
        await attach_document(db_session, investigation.id, prepare_document(path), consent_at=None)
    assert (await db_session.execute(select(func.count()).select_from(UserDocument))).scalar_one() == 0


async def test_the_document_is_read_and_becomes_the_idea(db_session, tmp_path):
    investigation, document = await with_document(db_session, tmp_path, published=True, ids={"arxiv": "2405.01234"})
    papers_before = (await db_session.execute(select(func.count()).select_from(Paper))).scalar_one()
    reader = ScriptedLlm.with_answers(READING)
    analyst = ScriptedLlm.with_answers(answer(json.dumps(document_output())))

    outcome = await understand_document(sessions_on(db_session), investigation.id, read_model=reader, model=analyst)

    assert outcome.error is None and outcome.reader_quotes == (1, 2)  # one quote is in the draft, one isn't
    reader_input = "\n".join(p.text for c in reader.requests[0].contents for p in c.parts if p.text)
    assert '<chunk id="D2" section="Related work">' in reader_input
    analyst_input = "\n".join(p.text for c in analyst.requests[0].contents for p in c.parts if p.text)
    assert "research questions: (D3) Does RAG help small models in Georgian?" in analyst_input
    assert "future work: Nobody has tested Georgian." in analyst_input  # unverified: no section given
    assert outcome.facet_sources == {"retrieval-augmented generation": "D3"}
    assert outcome.novelty_claims == [{"claim": "no prior work evaluates RAG for Georgian", "section": "D2"}]

    await db_session.refresh(document)
    assert document.reading["verified_quotes"] == 1 and document.reading["analysis"]["methods"] == [
        "retrieval-augmented generation"]
    assert document.idea_extras["novelty_claims"][0]["section"] == "D2"
    assert (document.published, document.external_ids) == (True, {"arxiv": "2405.01234"})
    intake = (await db_session.execute(select(Intake).where(Intake.investigation_id == investigation.id))
              ).scalar_one()
    assert intake.agent_reading["facets"] == VALID["facets"]  # the plain reading: clarification works unchanged
    assert "idea_analyst_document@1" in intake.prompt_version
    # private: nothing went into the shared library
    assert (await db_session.execute(select(func.count()).select_from(Paper))).scalar_one() == papers_before
    assert (await db_session.execute(select(func.count()).select_from(PaperAnalysis).where(
        PaperAnalysis.paper_id == document.id))).scalar_one() == 0
    assert (await db_session.execute(select(func.count()).select_from(Quote).where(
        Quote.paper_id == document.id))).scalar_one() == 0
    roles = sorted((await db_session.execute(select(LlmCall.role).where(
        LlmCall.investigation_id == investigation.id))).scalars())
    assert roles == ["idea_analyst", "paper_reader"]
    await db_session.refresh(investigation)
    assert investigation.stage == "awaiting_clarification"


async def test_a_failed_call_fails_the_investigation_and_keeps_its_cost(db_session, tmp_path):
    investigation, _ = await with_document(db_session, tmp_path)
    reader = ScriptedLlm.with_answers(READING)
    broken = ScriptedLlm.with_answers(answer("not json"), answer("still not json"))
    outcome = await understand_document(sessions_on(db_session), investigation.id, read_model=reader, model=broken)
    assert outcome.error and "LlmOutputInvalid" in outcome.error
    await db_session.refresh(investigation)
    assert investigation.status == "failed"
    calls = (await db_session.execute(select(LlmCall).where(LlmCall.investigation_id == investigation.id))
             ).scalars().all()
    assert {c.role for c in calls} >= {"paper_reader"}  # the reading was paid for and is counted


async def test_the_document_is_deleted_with_its_investigation(db_session, tmp_path):
    investigation, document = await with_document(db_session, tmp_path)
    await db_session.delete(await db_session.get(Investigation, investigation.id))
    await db_session.flush()
    db_session.expunge_all()
    assert await db_session.get(UserDocument, document.id) is None


async def test_clarification_works_on_a_document_idea(db_session, tmp_path):
    # found by the step 6a.1 live check: clarification rebuilt the request from the (empty) idea text and failed
    from src.ai.workflows.clarification import load_pending

    investigation, _ = await with_document(db_session, tmp_path)
    assert investigation.input["document"] == "draft.md"
    await understand_document(sessions_on(db_session), investigation.id, read_model=ScriptedLlm.with_answers(READING),
                              model=ScriptedLlm.with_answers(answer(json.dumps(document_output()))))
    pending = await load_pending(sessions_on(db_session), investigation.id)
    assert "the user's own document draft.md" in pending.request.idea.lower()
    assert pending.reading.facets[1].text == "retrieval-augmented generation"
