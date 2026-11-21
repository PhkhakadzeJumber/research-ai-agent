"""Tests for the value and feasibility step and verdicts_for (src/ai/workflows/value_feasibility.py).
Real test database (rolled back), fake Claude and sources, on the real pipeline up to
assessing_value_feasibility (with one saturated Skeptic round)."""

import pytest
from sqlalchemy import select

from src.ai.workflows.evaluation import selected_papers
from src.ai.workflows.value_feasibility import value_step, verdicts_for
from src.db.models import Candidate, Claim, Evaluation, Investigation, PaperAnalysis
from tests.db.test_clarification_workflow import sessions_on
from tests.db.test_evaluation_workflow import VALUE_SETTINGS, FakeSearch, skeptic_queries_answer, value_answer
from tests.db.test_skeptic_round import at_skeptic, run_round
from tests.fakes import ScriptedLlm, answer

pytestmark = [pytest.mark.anyio, pytest.mark.db]


async def at_value(db_session, *, constraints=None, datasets=()):
    """assessing_value_feasibility; 'datasets' are added to the first read paper's analysis."""
    investigation, evaluation_id = await at_skeptic(db_session)
    await run_round(db_session, evaluation_id, FakeSearch(), ScriptedLlm.with_answers(skeptic_queries_answer()))
    if constraints is not None:
        stored = await db_session.get(Investigation, investigation.id)
        stored.constraints_snapshot = constraints
    if datasets:
        paper, _ = (await selected_papers(db_session, evaluation_id))[0]
        analysis = (await db_session.execute(select(PaperAnalysis).where(PaperAnalysis.paper_id == paper.id))
                    ).scalar_one()
        analysis.content = {**analysis.content, "datasets": list(datasets)}
    await db_session.flush()
    return evaluation_id


async def run_value(db_session, evaluation_id, *answers):
    return await value_step(sessions_on(db_session), evaluation_id, settings=VALUE_SETTINGS,
                            model=ScriptedLlm.with_answers(*answers))


async def stored(db_session, evaluation_id):
    row = await db_session.get(Evaluation, evaluation_id)
    await db_session.refresh(row)
    return row


async def test_the_assessment_is_stored_with_code_checks(db_session):
    evaluation_id = await at_value(db_session, constraints={"available_time": "10 weeks"}, datasets=["GeoQA"])
    outcome = await run_value(db_session, evaluation_id, value_answer(
        claims=[("RAG for small models matters", "P1", "We study Paper 0")],
        data=[("GeoQA", "public"), ("Georgian Wikipedia dump", "public"), ("annotations", "collect")],
        timeline=[("setup", 3), ("experiments", 6), ("writing", 3)]))

    assert outcome.error is None
    assert [c["status"] for c in outcome.data_checks] == ["confirmed", "unconfirmed", "to_collect"]
    assert outcome.timeline == {"total_weeks": 12, "available_weeks": 10.0, "over": True}
    assert outcome.code_blocking == ["the timeline needs 12 weeks; 10 are available"]

    row = await stored(db_session, evaluation_id)
    assert row.step == "concluding"
    assert row.value_feasibility["code_blocking"] == outcome.code_blocking
    [claim] = (await db_session.execute(select(Claim).where(Claim.author_agent == "value_feasibility"))
               ).scalars().all()
    assert (claim.kind, claim.status) == ("significance", "verified")


async def test_all_verdicts_come_from_stored_evidence(db_session):
    evaluation_id = await at_value(db_session, constraints={"available_time": "10 weeks"})
    await run_value(db_session, evaluation_id, value_answer(
        claims=[("matters", "P1", "We study Paper 0"), ("matters too", "P2", "We study Paper 1")],
        timeline=[("all", 12)], simplifications=["use one 1B model"]))

    verdicts = await verdicts_for(db_session, evaluation_id)
    assert verdicts.novelty.category == "potential_opportunity"
    assert (verdicts.significance.category, verdicts.significance.rule) == ("strong", "S2")  # 2 papers
    assert (verdicts.feasibility.category, verdicts.feasibility.rule) == ("feasible_with_adjustments", "F2")
    assert "the timeline needs 12 weeks; 10 are available" in verdicts.feasibility.weaknesses
    assert (verdicts.recommendation.category, verdicts.recommendation.needs_further_investigation) == ("pursue", True)
    assert (verdicts.triage.number, verdicts.triage.action) == (5, "refine")  # simplify first


async def test_too_long_without_a_simplification_is_not_feasible(db_session):
    evaluation_id = await at_value(db_session, constraints={"available_time": "4 weeks"})
    await run_value(db_session, evaluation_id, value_answer(timeline=[("all", 12)]))
    verdicts = await verdicts_for(db_session, evaluation_id)
    assert (verdicts.feasibility.category, verdicts.recommendation.category, verdicts.triage.number) == (
        "not_feasible", "reconsider", 1)


async def test_unconfirmed_data_makes_feasibility_unclear(db_session):
    evaluation_id = await at_value(db_session)
    await run_value(db_session, evaluation_id, value_answer(data=[("Georgian Wikipedia dump", "public")]))
    verdicts = await verdicts_for(db_session, evaluation_id)
    assert (verdicts.feasibility.category, verdicts.feasibility.rule) == ("unclear", "F3")
    assert verdicts.significance.category == "unclear"  # no verified significance claim


async def test_a_failed_assessment_degrades_and_both_verdicts_are_unclear(db_session):
    evaluation_id = await at_value(db_session)
    broken = answer("not json")
    outcome = await run_value(db_session, evaluation_id, broken, broken)
    assert outcome.error.startswith("the Value & Feasibility Evaluator failed")
    row = await stored(db_session, evaluation_id)
    assert row.step == "concluding" and row.counters["degraded_steps"][-1]["step"] == "assessing_value_feasibility"
    verdicts = await verdicts_for(db_session, evaluation_id)
    assert (verdicts.significance.category, verdicts.feasibility.category) == ("unclear", "unclear")
    assert verdicts.recommendation.needs_further_investigation


async def test_the_evaluator_sees_the_users_level_and_constraints(db_session):
    evaluation_id = await at_value(db_session, constraints={"compute": "laptop only"})
    llm = ScriptedLlm.with_answers(value_answer())
    await value_step(sessions_on(db_session), evaluation_id, settings=VALUE_SETTINGS, model=llm)
    packet = llm.requests[0].contents[-1].parts[0].text
    assert "<complexity>beginner" in packet and "- compute: laptop only" in packet
    assert '<claim label="C1" kind="gap"' in packet
    assert (await db_session.get(Candidate, (await stored(db_session, evaluation_id)).candidate_id))
