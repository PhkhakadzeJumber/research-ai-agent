"""Tests for the user's document in the evaluation (step 6a.2): the user's own work is set aside
(src/ai/workflows/own_work.py), the document's claims and plan reach the Skeptic and Value & Feasibility
(src/ai/workflows/evaluation.py candidate_for_planning, src/ai/context.py), and the report shows them
(scripts/report.py). Real test database (rolled back); no Claude."""

import uuid

import pytest
from sqlalchemy import select

from scripts.report import document_lines, document_vs_literature
from src.ai.context import render_skeptic_queries, render_value_feasibility
from src.ai.workflows.evaluation import candidate_for_planning
from src.ai.workflows.own_work import OWN_WORK_REASON, is_own_work, set_aside_own_work, title_matches
from src.ai.workflows.user_document import attach_document, prepare_document
from src.db.models import Evaluation, Paper, RetrievalHit
from src.db.repositories.candidates import FacetToStore, create_root_candidate
from src.db.repositories.runs import create_investigation
from src.timeutil import now
from tests.ai.test_user_document_parsing import DRAFT

pytestmark = [pytest.mark.anyio, pytest.mark.db]

PLAN_DRAFT = DRAFT + "\n## Plan\n\nEight weeks on one GPU: two for data, four for experiments, two for writing.\n"
CLAIMS = [{"claim": "no prior work evaluates RAG for Georgian", "section": "D2"}]


async def mode1_with_document(db_session, tmp_path, *, published=None, ids=None):
    path = tmp_path / "draft.md"
    path.write_text(PLAN_DRAFT, encoding="utf-8")
    investigation = await create_investigation(db_session, mode="evaluate", complexity="beginner", input={"idea": ""})
    document = await attach_document(db_session, investigation.id, prepare_document(path), consent_at=now(),
                                     published=published, external_ids=ids)
    document.idea_extras = {"novelty_claims": CLAIMS, "facet_sources": {"retrieval-augmented generation": "D3"}}
    idea = await create_root_candidate(db_session, investigation_id=investigation.id, origin="user",
                                       title="RAG for Georgian QA", research_question="Does RAG help?",
                                       facets=[FacetToStore("problem", "hallucination", True, True)])
    evaluation = Evaluation(candidate_id=idea.id, depth="full", step="screening", status="running")
    db_session.add(evaluation)
    await db_session.flush()
    return investigation, document, idea, evaluation


async def hit(db_session, investigation, evaluation, title, **ids):
    paper = Paper(title=title, tier=2, year=2024, **({"openalex_id": f"W{uuid.uuid4().int % 10**9}"} | ids))
    db_session.add(paper)
    await db_session.flush()
    row = RetrievalHit(investigation_id=investigation.id, evaluation_id=evaluation.id, paper_id=paper.id,
                       found_by_query_ids=[])
    db_session.add(row)
    await db_session.flush()
    return paper, row


# --- recognising the user's own work (pure) ----------------------------------------------------------------

def test_own_work_is_recognised_by_id_or_by_title_when_published():
    from types import SimpleNamespace

    def doc(published=None, **ids):
        return SimpleNamespace(external_ids=ids, published=published)

    def paper(title="Something else", doi=None, arxiv_id=None):
        return SimpleNamespace(title=title, doi=doi, arxiv_id=arxiv_id)

    titles = ["RAG for Georgian QA"]
    assert is_own_work(paper(doi="10.1/ABC"), doc(doi="https://doi.org/10.1/abc"), titles)
    assert is_own_work(paper(arxiv_id="2405.01234v3"), doc(arxiv="arXiv:2405.01234"), titles)  # versions ignored
    assert is_own_work(paper(title="RAG for Georgian Q.A."), doc(published=True), titles)
    assert not is_own_work(paper(title="RAG for Georgian Q.A."), doc(published=None), titles)  # unpublished: by ID only
    assert not is_own_work(paper(title="RAG for Kazakh QA"), doc(published=True), titles)
    assert title_matches("Retrieval-Augmented Generation for Georgian Question Answering",
                         ["Retrieval Augmented Generation for Georgian Question Answering"])


async def test_the_users_own_paper_is_set_aside_before_screening(db_session, tmp_path):
    investigation, _, _, evaluation = await mode1_with_document(db_session, tmp_path, published=True,
                                                                ids={"arxiv": "2405.01234"})
    mine, mine_hit = await hit(db_session, investigation, evaluation, "A different title", arxiv_id="2405.01234v2")
    same_title, same_hit = await hit(db_session, investigation, evaluation, "RAG for Georgian QA")
    other, other_hit = await hit(db_session, investigation, evaluation, "RAG for Kazakh QA")

    found = await set_aside_own_work(db_session, evaluation.id)
    assert set(found) == {mine.id, same_title.id}
    for row in (mine_hit, same_hit):
        assert (row.screened, row.llm_score, row.llm_reason) == (True, 0, OWN_WORK_REASON)
    assert (other_hit.screened, other_hit.llm_score) == (False, None)  # left for the screener
    assert set(evaluation.counters["own_work"]) == {str(mine.id), str(same_title.id)}
    assert await set_aside_own_work(db_session, evaluation.id) == []  # already screened: nothing new


async def test_without_a_document_nothing_is_set_aside(db_session):
    investigation = await create_investigation(db_session, mode="evaluate", complexity="beginner",
                                               input={"idea": "RAG for Georgian QA"})
    idea = await create_root_candidate(db_session, investigation_id=investigation.id, origin="user",
                                       title="RAG for Georgian QA", research_question="Q?",
                                       facets=[FacetToStore("problem", "p", True, True)])
    evaluation = Evaluation(candidate_id=idea.id, depth="full", step="screening", status="running")
    db_session.add(evaluation)
    await db_session.flush()
    await hit(db_session, investigation, evaluation, "RAG for Georgian QA")
    assert await set_aside_own_work(db_session, evaluation.id) == []


# --- the document reaches the Skeptic and Value & Feasibility ---------------------------------------------

async def test_the_documents_claims_and_plan_reach_the_skeptic_and_a6(db_session, tmp_path):
    investigation, _, idea, _ = await mode1_with_document(db_session, tmp_path)
    view = await candidate_for_planning(db_session, idea)
    assert view.document_claims == ("(D2) no prior work evaluates RAG for Georgian",)
    assert "(D3) Method: We compare closed-book and RAG settings" in view.document_plan
    assert "(D4) Plan: Eight weeks on one GPU" in view.document_plan
    assert "Related work" not in view.document_plan  # only method, data and plan sections

    skeptic = render_skeptic_queries(view, (), "Potential opportunity", ())
    assert "<document_claims>\n- (D2) no prior work evaluates RAG for Georgian\n</document_claims>" in skeptic
    value = render_value_feasibility(view, complexity="beginner", constraints=(), claims=(), challenges=(), papers=())
    assert "<user_plan>" in value and "Eight weeks on one GPU" in value

    # a Scout idea in the same investigation gets neither
    other = await create_root_candidate(db_session, investigation_id=investigation.id, origin="alternative",
                                        title="An alternative", research_question="Q?",
                                        facets=[FacetToStore("problem", "p", True, False)])
    plain = await candidate_for_planning(db_session, other)
    assert (plain.document_claims, plain.document_plan) == ((), "")
    assert "<document_claims>" not in render_skeptic_queries(plain, (), "x", ())


# --- the report -------------------------------------------------------------------------------------------

async def test_the_report_sections(db_session, tmp_path):
    investigation, document, _, evaluation = await mode1_with_document(db_session, tmp_path, published=True,
                                                                       ids={"arxiv": "2405.01234"})
    mine, _ = await hit(db_session, investigation, evaluation, "My own preprint", arxiv_id="2405.01234")
    await set_aside_own_work(db_session, evaluation.id)
    document.reading = {"verified_quotes": 3, "quotes": 4}

    text = "\n".join(document_lines(document))
    assert "## Your document" in text and "already published (arxiv 2405.01234)" in text
    assert "3 of 4 quotes checked against your text" in text and "retrieval-augmented generation *(from D3)*" in text
    vs = "\n".join(await document_vs_literature(db_session, document, evaluation))
    assert "## Your document's claims vs the literature" in vs
    assert '(D2) "no prior work evaluates RAG for Georgian"' in vs and "not concluded yet" in vs
    assert "Set aside as your own work" in vs and "*My own preprint*" in vs
    marked = (await db_session.execute(select(RetrievalHit).where(RetrievalHit.paper_id == mine.id))).scalar_one()
    assert marked.llm_reason == OWN_WORK_REASON  # never read, never in the novelty matrix
