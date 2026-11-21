"""Tests for the novelty step, the stored evidence, the coverage snapshot and the novelty verdict.
Real test database (rolled back), fake Claude and fake sources: the real pipeline up to assessing_novelty."""

import pytest
from sqlalchemy import select

from src.ai.evidence.coverage import compute_coverage
from src.ai.workflows.evaluation import novelty_step, novelty_verdict_for, read_step, screen_step, selected_papers
from src.ai.workflows.transitions import StaleState
from src.db.models import Claim, ClaimEvidence, Evaluation, FacetMatch, LlmCall, Quote
from tests.db.test_clarification_workflow import sessions_on
from tests.db.test_evaluation_workflow import (
    NOVELTY_SETTINGS,
    READ_SETTINGS,
    SCREEN_SETTINGS,
    novelty_answer,
    screening_answer,
)
from tests.db.test_screening_reading import CORE, READING, retrieved
from tests.fakes import PaperAwareLlm, ScriptedLlm, answer

pytestmark = [pytest.mark.anyio, pytest.mark.db]

KEY_FACETS = (1, 2, 3)  # the test idea's problem, method and data setting (tests/ai/test_structured_idea_schema.py)


async def at_novelty(db_session, n=21):
    """An evaluation at assessing_novelty with n relevant papers read ('Paper 0' ... first = P1)."""
    investigation, evaluation_id = await retrieved(db_session, {CORE: [f"Paper {i}" for i in range(n)]})
    sessions = sessions_on(db_session)
    await screen_step(sessions, evaluation_id, settings=SCREEN_SETTINGS,
                      model=ScriptedLlm.with_answers(screening_answer(*[3] * n)))
    chosen = await selected_papers(db_session, evaluation_id)
    await read_step(sessions, evaluation_id, settings=READ_SETTINGS,
                    model=PaperAwareLlm.with_answers({str(paper.id): READING for paper, _ in chosen}))
    return investigation, evaluation_id


async def run_novelty(db_session, evaluation_id, novelty):
    return await novelty_step(sessions_on(db_session), evaluation_id, settings=NOVELTY_SETTINGS,
                              model=ScriptedLlm.with_answers(novelty))


async def evaluation(db_session, evaluation_id):
    row = await db_session.get(Evaluation, evaluation_id)
    await db_session.refresh(row)
    return row


# --- the step and its stored evidence -------------------------------------------------------------------

async def test_a_paper_that_did_it_all_makes_the_idea_previously_investigated(db_session):
    _, evaluation_id = await at_novelty(db_session)
    cells = [("P1", facet, "same", "We study Paper 0") for facet in KEY_FACETS] + [("P2", 1, "different", None)]
    outcome = await run_novelty(db_session, evaluation_id, novelty_answer(cells, closest_works=[
        {"paper": "P1", "what_they_did": "the same study", "how_the_idea_differs": "it doesn't",
         "minor_difference_only": False}]))

    assert outcome.error is None and outcome.papers["P1"].title == "Paper 0"
    assert len(outcome.papers) == 12  # the closest 12 read papers, not all 21
    row = await evaluation(db_session, evaluation_id)
    assert row.step == "skeptic_review"
    assert row.novelty_assessment["closest_works"][0]["paper_id"] == str(outcome.papers["P1"].id)
    assert row.counters["novelty_verified_cells"] == 3

    matches = (await db_session.execute(select(FacetMatch).where(FacetMatch.evaluation_id == evaluation_id)))
    matches = matches.scalars().all()
    assert len(matches) == 4 and all(m.quote_id for m in matches if m.overlap == "same")
    assert {(await db_session.get(Quote, m.quote_id)).verified for m in matches if m.quote_id} == {True}

    verdict, _ = await novelty_verdict_for(db_session, evaluation_id)
    assert (verdict.category, verdict.rule, verdict.papers) == ("previously_investigated", "N1",
                                                               [str(outcome.papers["P1"].id)])
    [call] = (await db_session.execute(select(LlmCall).where(LlmCall.role == "novelty"))).scalars().all()
    assert call.evaluation_id == evaluation_id


async def test_an_invented_quote_does_not_make_a_same_count(db_session):
    _, evaluation_id = await at_novelty(db_session)
    cells = [("P1", facet, "same", "We study a completely different topic") for facet in KEY_FACETS]
    await run_novelty(db_session, evaluation_id, novelty_answer(cells))

    verdict, coverage = await novelty_verdict_for(db_session, evaluation_id)
    assert (verdict.category, verdict.rule) == ("potential_opportunity", "N4")  # the "same" cells don't count
    assert "sources: 2 (at least 3 needed)" in verdict.weaknesses
    assert "the Skeptic hasn't reviewed the idea yet" in verdict.weaknesses


async def test_claims_get_their_status_from_code_and_link_their_quotes(db_session):
    _, evaluation_id = await at_novelty(db_session)
    await run_novelty(db_session, evaluation_id, novelty_answer(claims=[
        {"kind": "gap", "text": "not found among the papers examined", "statement_type": "interpretation",
         "inferred": True, "evidence": [{"paper": "P2", "quote": "We study Paper 1", "stance": "supports"},
                                        {"paper": "P2", "quote": "We study Paper 1", "stance": "supports"}]},
        {"kind": "prior_work", "text": "t", "statement_type": "evidence", "inferred": False,
         "evidence": [{"paper": "P3", "quote": "an invented sentence", "stance": "supports"}]},
    ]))
    claims = (await db_session.execute(select(Claim).where(Claim.evaluation_id == evaluation_id)
                                       .order_by(Claim.created_at))).scalars().all()
    assert [(c.kind, c.status, c.author_agent) for c in claims] == [("gap", "verified", "novelty"),
                                                                    ("prior_work", "unverified_quote", "novelty")]
    links = (await db_session.execute(select(ClaimEvidence).where(ClaimEvidence.claim_id == claims[0].id)))
    assert len(links.scalars().all()) == 1  # the same quote twice is one link


async def test_a_failed_assessment_degrades_and_the_verdict_says_insufficient(db_session):
    _, evaluation_id = await at_novelty(db_session)
    broken = answer("not json")
    outcome = await novelty_step(sessions_on(db_session), evaluation_id, settings=NOVELTY_SETTINGS,
                                 model=ScriptedLlm.with_answers(broken, broken))

    assert outcome.error.startswith("the Novelty Investigator failed (LlmOutputInvalid")
    row = await evaluation(db_session, evaluation_id)
    assert row.step == "skeptic_review" and row.status == "running"
    assert row.counters["degraded_steps"][-1]["step"] == "assessing_novelty"
    verdict, _ = await novelty_verdict_for(db_session, evaluation_id)
    assert verdict.category == "insufficient_evidence" and "could not be made" in verdict.trace[0]


async def test_too_few_relevant_papers_is_insufficient_evidence(db_session):
    _, evaluation_id = await at_novelty(db_session, n=5)
    await run_novelty(db_session, evaluation_id, novelty_answer())
    verdict, _ = await novelty_verdict_for(db_session, evaluation_id)
    assert verdict.category == "insufficient_evidence" and "only 5 relevant papers" in verdict.trace[0]


# --- coverage -------------------------------------------------------------------------------------------

async def test_the_coverage_snapshot_counts_the_search_trail(db_session):
    _, evaluation_id = await at_novelty(db_session, n=21)
    coverage = await compute_coverage(db_session, evaluation_id)

    assert coverage["funnel"] == {"retrieved": 21, "unique": 21, "screened": 21, "relevant": 21, "read": 21,
                                  "read_failed": 0}
    assert coverage["sources_ok"] == 2 and coverage["queries_by_origin"] == {"analyst": 16}
    assert (coverage["read_full_text"], coverage["read_abstract_only"]) == (0, 21)
    assert coverage["tier_mix"] == {"peer_reviewed": 21, "preprint": 0}
    assert "semantic_scholar was not searched." in coverage["blind_spots"]
    assert "Every paper was read from its abstract only (no full text)." in coverage["blind_spots"]


async def test_novelty_refuses_to_run_out_of_order(db_session):
    _, evaluation_id = await retrieved(db_session, {CORE: ["Paper A"]})
    with pytest.raises(StaleState):
        await run_novelty(db_session, evaluation_id, novelty_answer())



async def test_the_tier_mix_counts_only_papers_actually_read(db_session):
    from sqlalchemy import delete

    from src.db.models import PaperAnalysis

    _, evaluation_id = await at_novelty(db_session, n=3)
    first, _ = (await selected_papers(db_session, evaluation_id))[0]
    await db_session.execute(delete(PaperAnalysis).where(PaperAnalysis.paper_id == first.id))  # as if its reading failed
    coverage = await compute_coverage(db_session, evaluation_id)
    assert coverage["tier_mix"] == {"peer_reviewed": 2, "preprint": 0}  # 3 selected, 2 read (step 4.6 live run: 25 vs 24)
