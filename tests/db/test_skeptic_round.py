"""Tests for one Skeptic round (src/ai/workflows/skeptic_round.py): real test database (rolled back),
fake Claude and fake sources, on top of the real pipeline up to the novelty assessment."""

import json

import pytest
from sqlalchemy import select

from src.ai.workflows.evaluation import novelty_step, novelty_verdict_for
from src.ai.workflows.skeptic_round import skeptic_round_step
from src.db.models import Challenge, Claim, Evaluation, Quote, SearchQuery
from tests.db.test_clarification_workflow import sessions_on
from tests.db.test_evaluation_workflow import (
    NOVELTY_SETTINGS,
    READ_SETTINGS,
    SCREEN_SETTINGS,
    SKEPTIC_SETTINGS,
    FakeSearch,
    novelty_answer,
    screening_answer,
    skeptic_queries_answer,
)
from tests.db.test_novelty_step import at_novelty
from tests.db.test_screening_reading import READING
from tests.fakes import PaperAwareLlm, ScriptedLlm, answer

pytestmark = [pytest.mark.anyio, pytest.mark.db]

GAP = {"kind": "gap", "text": "sub-3B models not found among the papers examined", "statement_type": "interpretation",
       "inferred": True, "evidence": [{"paper": "P1", "quote": "We study Paper 0", "stance": "supports"}]}
ATTACK = "open-book qa georgian"


async def at_skeptic(db_session):
    """An evaluation at skeptic_review, with 21 papers read and one verified gap claim (C1)."""
    investigation, evaluation_id = await at_novelty(db_session)
    await novelty_step(sessions_on(db_session), evaluation_id, settings=NOVELTY_SETTINGS,
                       model=ScriptedLlm.with_answers(novelty_answer(claims=[GAP])))
    return investigation, evaluation_id


async def run_round(db_session, evaluation_id, search, skeptic, *, screen=None, reader=None):
    return await skeptic_round_step(
        sessions_on(db_session), evaluation_id, search, settings=SKEPTIC_SETTINGS, model=skeptic,
        screen_settings=SCREEN_SETTINGS, screen_model=screen or ScriptedLlm.with_answers(),
        read_settings=READ_SETTINGS, read_model=reader or PaperAwareLlm.with_answers({}))


async def evaluation(db_session, evaluation_id):
    row = await db_session.get(Evaluation, evaluation_id)
    await db_session.refresh(row)
    return row


async def test_a_round_finds_new_prior_work_and_challenges_the_gap(db_session):
    _, evaluation_id = await at_skeptic(db_session)
    search = FakeSearch({ATTACK: ["New study A", "New study B", "Paper 0"]})  # Paper 0 is already known
    challenge = {"challenges": [{
        "target_claim": "C1", "type": "prior_work_found", "argument": "New study A already did it",
        "evidence": [{"paper": "P1", "quote": "We study New study A"}], "same_on_facets": [1, 2, 3]}]}
    skeptic = ScriptedLlm.with_answers(skeptic_queries_answer(ATTACK, "a b c", "d e f", "g h i"),
                                       answer(json.dumps(challenge)))
    screen = ScriptedLlm.with_answers(screening_answer(3, 1))  # New study A relevant, B tangential
    reader = ScriptedLlm.with_answers(READING)  # only New study A is read
    outcome = await run_round(db_session, evaluation_id, search, skeptic, screen=screen, reader=reader)

    assert (outcome.round, outcome.searches, outcome.new_papers, outcome.new_relevant) == (1, 4, 2, 1)
    assert [paper.title for paper, _ in outcome.read] == ["New study A"]
    assert outcome.next_step == "revising_novelty" and not outcome.saturated

    [stored] = (await db_session.execute(select(Challenge).where(Challenge.evaluation_id == evaluation_id))).scalars().all()
    assert (stored.round, stored.type, stored.statement_type, stored.same_on_facets) == (
        1, "prior_work_found", "evidence", [1, 2, 3])
    assert stored.new_paper_ids == [outcome.papers["P1"].id]
    assert (await db_session.get(Quote, stored.evidence_quote_ids[0])).verified is True
    target = await db_session.get(Claim, stored.target_claim_id)
    await db_session.refresh(target)
    assert (target.kind, target.status) == ("gap", "challenged")

    queries = (await db_session.execute(select(SearchQuery).where(SearchQuery.origin == "skeptic"))).scalars().all()
    assert {(q.round, q.query_type) for q in queries} == {(1, "other_terminology")}
    row = await evaluation(db_session, evaluation_id)
    assert row.step == "revising_novelty"
    assert row.counters["skeptic_rounds"] == [{"round": 1, "searches": 4, "new_papers": 2, "read": 1,
                                               "challenges": 1, "new_relevant": 1, "saturated": False}]


async def test_a_round_that_finds_nothing_new_is_saturated(db_session):
    _, evaluation_id = await at_skeptic(db_session)
    skeptic = ScriptedLlm.with_answers(skeptic_queries_answer())  # no challenge call: nothing new to quote
    outcome = await run_round(db_session, evaluation_id, FakeSearch({ATTACK: ["Paper 0"]}), skeptic)

    assert (outcome.new_papers, outcome.new_relevant, outcome.saturated) == (0, 0, True)
    assert outcome.next_step == "assessing_value_feasibility" and len(skeptic.requests) == 1
    verdict, coverage = await novelty_verdict_for(db_session, evaluation_id)
    assert "the Skeptic hasn't reviewed the idea yet" not in verdict.weaknesses
    assert "the Skeptic hasn't reached saturation" not in verdict.weaknesses  # saturated
    assert coverage["skeptic_rounds"] == [{"round": 1, "new_relevant": 0, "challenges": 0}]
    assert coverage["queries_by_origin"] == {"analyst": 16, "skeptic": 4}


async def test_the_skeptic_sees_claims_and_examined_titles_but_not_the_reasoning(db_session):
    _, evaluation_id = await at_skeptic(db_session)
    skeptic = ScriptedLlm.with_answers(skeptic_queries_answer())
    await run_round(db_session, evaluation_id, FakeSearch(), skeptic)
    packet = skeptic.requests[0].contents[-1].parts[0].text
    assert '<claim label="C1" kind="gap" status="verified">sub-3B models not found' in packet
    assert "- Paper 0 (n.d.)" in packet and "<verdict>Potential opportunity" in packet
    assert "rationale" not in packet and "<reading>" not in packet  # no A4 reasoning, no readings


async def test_a_failed_skeptic_degrades_and_the_verdict_says_not_saturated(db_session):
    _, evaluation_id = await at_skeptic(db_session)
    broken = answer("not json")
    outcome = await run_round(db_session, evaluation_id, FakeSearch(), ScriptedLlm.with_answers(broken, broken))

    assert outcome.error.startswith("the Skeptic failed to plan its searches") and not outcome.saturated
    row = await evaluation(db_session, evaluation_id)
    assert row.step == "assessing_value_feasibility" and row.counters["degraded_steps"][-1]["step"] == "skeptic_review"
    verdict, _ = await novelty_verdict_for(db_session, evaluation_id)
    assert "the Skeptic hasn't reached saturation" in verdict.weaknesses



async def test_coverage_counts_what_skeptic_rounds_add(db_session):
    from src.ai.evidence.coverage import compute_coverage

    _, evaluation_id = await at_skeptic(db_session)
    skeptic = ScriptedLlm.with_answers(skeptic_queries_answer(ATTACK, "a b c", "d e f", "g h i"),
                                       answer(json.dumps({"challenges": []})))
    await run_round(db_session, evaluation_id, FakeSearch({ATTACK: ["New study A", "New study B"]}), skeptic,
                    screen=ScriptedLlm.with_answers(screening_answer(3, 1)), reader=ScriptedLlm.with_answers(READING))

    funnel = (await compute_coverage(db_session, evaluation_id))["funnel"]
    # 21 papers from the main search + 2 from the Skeptic; 21 + 1 read (step 4.7a live run: stuck at the first 24)
    assert (funnel["unique"], funnel["screened"], funnel["relevant"], funnel["read"]) == (23, 23, 22, 22)
    assert funnel["retrieved"] == 23  # the fake source returns each paper once
