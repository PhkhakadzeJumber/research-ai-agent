"""Tests for concluding, triage and finishing (src/ai/workflows/concluding.py), budgets
(src/ai/workflows/budget.py) and the report (scripts/report.py). Real test database (rolled back),
fake Claude and sources, on the real pipeline up to `concluding`."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from scripts.evaluate import evaluate
from scripts.report import build_report
from src.ai.workflows.budget import DEFAULT_COST_CAP_USD, budget_left, cost_cap, set_cost_cap
from src.ai.workflows.concluding import conclude_step, finish_investigation
from src.ai.workflows.transitions import StaleState
from src.ai.workflows.value_feasibility import value_step
from src.db.models import Candidate, Evaluation, Investigation, TriageDecision, Verdict
from tests.db.test_clarification_workflow import sessions_on
from tests.db.test_evaluation_workflow import VALUE_SETTINGS, value_answer
from tests.db.test_value_step import at_value
from tests.fakes import ScriptedLlm

pytestmark = [pytest.mark.anyio, pytest.mark.db]

PURSUE = value_answer(claims=[("matters", "P1", "We study Paper 0"), ("matters too", "P2", "We study Paper 1")],
                      timeline=[("all", 8)])


async def at_concluding(db_session, value=PURSUE, constraints=None):
    evaluation_id = await at_value(db_session, constraints=constraints or {"available_time": "10 weeks"})
    await value_step(sessions_on(db_session), evaluation_id, settings=VALUE_SETTINGS,
                     model=ScriptedLlm.with_answers(value))
    evaluation = await db_session.get(Evaluation, evaluation_id)
    candidate = await db_session.get(Candidate, evaluation.candidate_id)
    return candidate.investigation_id, evaluation_id


async def refreshed(db_session, model, key):
    row = await db_session.get(model, key)
    await db_session.refresh(row)
    return row


# --- concluding -------------------------------------------------------------------------------------------

async def test_concluding_freezes_coverage_and_verdicts_and_ends_the_evaluation(db_session):
    _, evaluation_id = await at_concluding(db_session)
    verdicts = await conclude_step(sessions_on(db_session), evaluation_id)

    evaluation = await refreshed(db_session, Evaluation, evaluation_id)
    assert evaluation.step == "done" and evaluation.status in ("done", "partial")
    assert evaluation.coverage["funnel"]["read"] == verdicts.coverage["funnel"]["read"]
    rows = {v.dimension: v for v in (await db_session.execute(
        select(Verdict).where(Verdict.evaluation_id == evaluation_id))).scalars()}
    assert set(rows) == {"novelty", "significance", "feasibility", "recommendation"}
    assert (rows["novelty"].category, rows["novelty"].rules_version) == ("potential_opportunity", "novelty-rules-v2")
    assert rows["significance"].category == "strong" and rows["recommendation"].category == "pursue"
    assert "sources: 2 (at least 3 needed)" in rows["novelty"].uncertainties


async def test_a_concluded_evaluation_cannot_conclude_again(db_session):
    _, evaluation_id = await at_concluding(db_session)
    await conclude_step(sessions_on(db_session), evaluation_id)
    with pytest.raises(StaleState):
        await conclude_step(sessions_on(db_session), evaluation_id)


# --- triage and finishing ---------------------------------------------------------------------------------

async def test_a_good_idea_is_finalized_and_the_investigation_completes(db_session):
    investigation_id, evaluation_id = await at_concluding(db_session)
    await conclude_step(sessions_on(db_session), evaluation_id)
    outcome = await finish_investigation(sessions_on(db_session), investigation_id)

    assert (outcome.decision.rule_number, outcome.decision.action, outcome.candidate_status) == (
        9, "finalize", "finalist")
    assert outcome.not_executed is None
    assert len(outcome.decision.evidence["verdict_ids"]) == 4
    investigation = await refreshed(db_session, Investigation, investigation_id)
    assert (investigation.stage, investigation.status) == ("results_ready", "completed")


async def test_an_infeasible_idea_is_rejected_with_its_reason(db_session):
    investigation_id, evaluation_id = await at_concluding(
        db_session, value=value_answer(timeline=[("all", 20)]), constraints={"available_time": "10 weeks"})
    await conclude_step(sessions_on(db_session), evaluation_id)
    outcome = await finish_investigation(sessions_on(db_session), investigation_id)

    assert (outcome.decision.rule_number, outcome.decision.action, outcome.candidate_status) == (1, "reject", "rejected")
    candidate = (await db_session.execute(select(Candidate).where(Candidate.investigation_id == investigation_id))
                 ).scalar_one()
    await db_session.refresh(candidate)
    assert candidate.rejection_reason == "Rule 1: infeasible under your constraints"


async def test_a_refinement_is_recorded_but_not_carried_out_yet(db_session):
    investigation_id, evaluation_id = await at_concluding(
        db_session, value=value_answer(timeline=[("all", 20)], simplifications=["use one 1B model"]))
    await conclude_step(sessions_on(db_session), evaluation_id)
    outcome = await finish_investigation(sessions_on(db_session), investigation_id)
    assert (outcome.decision.rule_number, outcome.decision.action, outcome.candidate_status) == (
        5, "refine", "needs_refinement")
    assert "needs the Scout (step 5)" in outcome.not_executed
    [stored] = (await db_session.execute(select(TriageDecision))).scalars().all()
    assert stored.id == outcome.decision.id


async def test_finishing_needs_a_concluded_evaluation(db_session):
    investigation_id, _ = await at_concluding(db_session)
    with pytest.raises(ValueError, match="hasn't been concluded"):
        await finish_investigation(sessions_on(db_session), investigation_id)


# --- budgets ----------------------------------------------------------------------------------------------

def test_cost_caps():
    investigation = Investigation(budget={}, cost_usd=Decimal("4.99"))
    assert cost_cap(investigation) == DEFAULT_COST_CAP_USD and budget_left(investigation)
    set_cost_cap(investigation, 3)
    assert (cost_cap(investigation), budget_left(investigation)) == (Decimal("3"), False)
    with pytest.raises(ValueError):
        set_cost_cap(investigation, 0)


async def test_the_command_stops_before_a_paid_step_when_the_budget_is_used_up(db_session, capsys):
    investigation_id, _ = await at_concluding(db_session)
    investigation = await db_session.get(Investigation, investigation_id)
    evaluation = (await db_session.execute(select(Evaluation))).scalars().first()
    evaluation.step = "assessing_value_feasibility"  # back to a paid step, for this test only
    set_cost_cap(investigation, Decimal("0.01"))
    await db_session.flush()

    code = await evaluate(sessions_on(db_session), investigation_id, value_settings=VALUE_SETTINGS,
                          value_model=ScriptedLlm.with_answers())
    assert code == 3 and "Budget used up" in capsys.readouterr().out


# --- the report -------------------------------------------------------------------------------------------

async def test_the_report_is_built_from_stored_data(db_session):
    investigation_id, evaluation_id = await at_concluding(db_session)
    preliminary = await build_report(db_session, investigation_id)
    assert "## Verdicts (preliminary: the evaluation hasn't concluded)" in preliminary

    await conclude_step(sessions_on(db_session), evaluation_id)
    await finish_investigation(sessions_on(db_session), investigation_id)
    report = await build_report(db_session, investigation_id)

    assert report.startswith("# Eureka report: ")
    for section in ("## Your idea", "## Verdicts\n", "## Claims", "## Skeptic log", "## Feasibility details",
                    "## Coverage\n", "## Cost"):
        assert section in report
    assert "**Recommendation: Pursue**" in report and "**Decision:** rule 9: finalize" in report
    assert "Your idea's status: finalist" in report
    assert "Eureka never claims an idea is original" in report


async def test_the_skeptic_loop_stops_at_the_budget_and_says_so(db_session):
    from src.ai.workflows.evaluation import novelty_verdict_for
    from src.ai.workflows.skeptic_round import revision_step
    from tests.db.test_evaluation_workflow import NOVELTY_SETTINGS
    from tests.db.test_revision import PRIOR_WORK, at_revision, revision

    # round 1 found new papers, so another round would follow; no override (a key facet is missing)
    evaluation_id = await at_revision(db_session, challenge={**PRIOR_WORK, "same_on_facets": [1, 2]})
    evaluation = await db_session.get(Evaluation, evaluation_id)
    investigation = await db_session.get(Investigation,
                                         (await db_session.get(Candidate, evaluation.candidate_id)).investigation_id)
    set_cost_cap(investigation, Decimal("0.01"))
    await db_session.flush()

    outcome = await revision_step(sessions_on(db_session), evaluation_id, settings=NOVELTY_SETTINGS,
                                  model=ScriptedLlm.with_answers(revision()))
    assert outcome.next_step == "assessing_value_feasibility"
    evaluation = await refreshed(db_session, Evaluation, evaluation_id)
    assert evaluation.counters["budgets_hit"] == ["cost cap $0.01 reached: the Skeptic stopped after 1 round(s)"]
    verdict, coverage = await novelty_verdict_for(db_session, evaluation_id)
    assert "the Skeptic was stopped by the budget before saturation" in verdict.weaknesses
    assert coverage["budgets_hit"] == evaluation.counters["budgets_hit"]
