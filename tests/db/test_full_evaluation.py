"""Tests for full evaluation of the best advanced candidates (src/ai/workflows/full_evaluation.py, step 5.6a) and its
part of the candidate loop. Real test database (rolled back), fake Claude and fake sources, on the real pipeline."""

import json
from dataclasses import replace

import pytest
from sqlalchemy import select

from scripts.candidate_loop import LoopModels, continue_at
from src.ai.workflows.full_evaluation import choose_for_full, full_evaluation_step
from src.ai.workflows.light_evaluation import light_evaluation_step
from src.ai.workflows.transitions import StaleState
from src.db.models import Candidate, Evaluation, Investigation, RetrievalHit, Verdict
from src.db.repositories.candidates import FacetToStore, create_root_candidate
from src.db.repositories.runs import create_investigation
from tests.ai.test_query_plan import q
from tests.db.test_clarification_workflow import sessions_on
from tests.db.test_landscape_reading import TitleScreener
from tests.db.test_light_evaluation import CORE, Always, fake_models, prescreened, search

pytestmark = [pytest.mark.anyio, pytest.mark.db]

FULL_PLAN = {"queries": [
    q(CORE, "core", 1, 2, 3),
    *(q(text, "terminology_variant", 1) for text in ("judge variant one", "judge variant two", "judge variant three")),
    q("judging without method one", "without_method", 1, 3), q("judging without method two", "without_method", 1, 3),
    *(q(f"judging metric only {n}", "facet_only", 4) for n in ("a", "b", "c")),
    *(q(f"judging insight only {n}", "facet_only", 5) for n in ("a", "b", "c")),
    *(q(f"judging pair {n}", "facet_pair", 1, 3) for n in ("a", "b", "c")),
]}  # 15 queries: the full-depth quotas


def full_models(screener=None):
    return replace(fake_models(), model=Always.answering(json.dumps(FULL_PLAN)),
                   screen_model=screener or TitleScreener.scoring({"Judges fail in Georgian": 3,
                                                                   "Judge drift in Swahili": 2}))


async def advanced_after_light(db_session):
    """A Mode 2 investigation at full_evaluation: two candidates light-evaluated; the first one is advanced."""
    investigation = await prescreened(db_session)
    await light_evaluation_step(sessions_on(db_session), investigation.id, models=fake_models(), search_factory=search)
    first = (await db_session.execute(select(Candidate).where(
        Candidate.investigation_id == investigation.id, Candidate.title == "Judge calibration Georgian"))).scalar_one()
    first.status = "advanced"  # the fake verdicts aren't the point here: advance it for the upgrade
    other = (await db_session.execute(select(Candidate).where(
        Candidate.investigation_id == investigation.id, Candidate.title == "Translated benchmark artifacts"))
             ).scalar_one()
    other.status = "rejected"
    investigation.stage = "full_evaluation"
    await db_session.flush()
    return investigation, first


async def test_the_full_evaluation_extends_the_light_one(db_session):
    investigation, candidate = await advanced_after_light(db_session)
    light = (await db_session.execute(select(Evaluation).where(Evaluation.candidate_id == candidate.id))).scalar_one()
    light_hits = (await db_session.execute(select(RetrievalHit).where(RetrievalHit.evaluation_id == light.id))
                  ).scalars().all()
    screener = TitleScreener.scoring({})
    steps = []

    async def report(step, phase, outcome):
        if phase == "done":
            steps.append((step, outcome))

    outcome = await full_evaluation_step(sessions_on(db_session), investigation.id, models=full_models(screener),
                                         search_factory=search, report=report)

    assert [c.title for c in outcome.chosen] == ["Judge calibration Georgian"] and outcome.stopped is None
    full = (await db_session.execute(select(Evaluation).where(Evaluation.candidate_id == candidate.id,
                                                              Evaluation.depth == "full"))).scalar_one()
    await db_session.refresh(full)
    assert full.extends_id == light.id and full.status in ("done", "partial")
    assert full.counters["upgraded_from"] == str(light.id) and full.counters["copied_hits"] == len(light_hits)
    # the copied papers keep their screening: nothing new to screen, and the papers read come from the library
    assert screener.requests == []
    reading = [o for step, o in steps if step == "reading"][0]
    assert reading.read_now == 0 and reading.reused == 2
    # the light round counts: a full evaluation runs at most 2 more Skeptic rounds
    assert len(full.counters["skeptic_rounds"]) in (2, 3)
    verdicts = (await db_session.execute(select(Verdict).where(Verdict.evaluation_id == full.id))).scalars().all()
    assert len(verdicts) == 4
    (triaged, decision), = outcome.triaged
    await db_session.refresh(triaged)
    expected = {"finalize": "finalist", "refine": "needs_refinement", "reject": "rejected"}.get(decision.action,
                                                                                                "full_evaluating")
    assert triaged.status == expected
    await db_session.refresh(investigation)
    assert investigation.stage == "triage"


async def light_evaluated(db_session, investigation, title, recommendation, novelty="potential_opportunity"):
    c = await create_root_candidate(db_session, investigation_id=investigation.id, origin="discover", title=title,
                                    research_question="Q?", facets=[FacetToStore("problem", "p", True, False)])
    c.status = "advanced"
    evaluation = Evaluation(candidate_id=c.id, depth="light", step="done", status="done")
    db_session.add(evaluation)
    await db_session.flush()
    for dimension, category in (("recommendation", recommendation), ("novelty", novelty),
                                ("feasibility", "feasible"), ("significance", "moderate")):
        db_session.add(Verdict(evaluation_id=evaluation.id, dimension=dimension, category=category, rule="x",
                               rules_version="v"))
    await db_session.flush()
    return c


async def test_the_best_advanced_candidates_are_chosen_within_the_limits(db_session):
    investigation = await create_investigation(db_session, mode="discover", complexity="beginner",
                                               input={"field": "NLP", "count": 3})
    await light_evaluated(db_session, investigation, "Refine me", "refine")
    await light_evaluated(db_session, investigation, "Pursue, potential", "pursue")
    await light_evaluated(db_session, investigation, "Pursue, supported", "pursue", "supported_opportunity")
    await light_evaluated(db_session, investigation, "Reconsider", "reconsider")
    chosen = await choose_for_full(db_session, investigation)
    assert [c.title for c in chosen] == ["Pursue, supported", "Pursue, potential", "Refine me"]  # Mode 2: 3
    statuses = {c.title: c.status for c in (await db_session.execute(
        select(Candidate).where(Candidate.investigation_id == investigation.id))).scalars()}
    assert statuses["Reconsider"] == "advanced"  # passed screening, not fully evaluated

    mode1 = await create_investigation(db_session, mode="evaluate", complexity="beginner", input={"idea": "x"})
    for n in range(3):
        await light_evaluated(db_session, mode1, f"Alternative {n}", "pursue")
    assert len(await choose_for_full(db_session, mode1)) == 2  # Mode 1: the best 2


async def test_no_more_than_three_full_evaluations_per_investigation(db_session):
    investigation = await create_investigation(db_session, mode="discover", complexity="beginner",
                                               input={"field": "NLP", "count": 3})
    done = await light_evaluated(db_session, investigation, "Done before", "pursue")
    for _ in range(2):
        db_session.add(Evaluation(candidate_id=done.id, depth="full", step="done", status="done"))
    await light_evaluated(db_session, investigation, "A", "pursue")
    await light_evaluated(db_session, investigation, "B", "pursue")
    await db_session.flush()
    assert len(await choose_for_full(db_session, investigation)) == 1  # 3 - 2 already done


async def test_full_evaluation_needs_its_stage(db_session):
    investigation = await prescreened(db_session)  # at light_evaluation
    with pytest.raises(StaleState):
        await full_evaluation_step(sessions_on(db_session), investigation.id, models=full_models())


async def test_the_loop_runs_the_full_evaluation_and_routes_on(db_session, capsys):
    investigation, _ = await advanced_after_light(db_session)
    investigation.budget = {**investigation.budget, "light_evaluations": 2}  # no room to replenish afterwards
    await db_session.flush()
    code = await continue_at(sessions_on(db_session), investigation.id,
                             LoopModels(full_models(), search_factory=search), command="discover")
    output = capsys.readouterr().out
    assert code == 0
    assert "Full evaluation 1/1: Judge calibration Georgian (extends its light evaluation" in output
    assert "plan: 15 searches" in output and "Triage after full evaluation:" in output
    # the fake verdicts end in rule 7 (expand search: recorded, not carried out yet), so nothing is left to evaluate
    assert "rule 7: expand_search" in output and "Next: ranking (nothing left to evaluate)" in output
    assert (await db_session.get(Investigation, investigation.id)).stage == "ranking"
