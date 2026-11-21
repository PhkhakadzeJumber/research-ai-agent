"""Tests for the results of an investigation (step 5.6b): the ranking (src/ai/workflows/ranking.py) and the reports
(scripts/report.py, both workflows). Real test database (rolled back), fake Claude and fake sources."""

import pytest
from sqlalchemy import select

from scripts.report import build_report
from src.ai.workflows.full_evaluation import full_evaluation_step
from src.ai.workflows.ranking import complete_results, ranked_results
from src.ai.workflows.refining import refine_step
from src.db.models import Candidate, Evaluation, Investigation, Verdict
from src.db.repositories.candidates import FacetToStore, create_refined_candidate, create_root_candidate
from src.db.repositories.runs import create_investigation
from tests.db.test_clarification_workflow import sessions_on
from tests.db.test_full_evaluation import advanced_after_light, full_models
from tests.db.test_light_evaluation import search
from tests.db.test_refining import REFINE_SETTINGS, FakeRefiner, mode1_needs_refinement, refined

pytestmark = [pytest.mark.anyio, pytest.mark.db]

FACETS = [FacetToStore("problem", "p", True, False)]


async def evaluated(db_session, investigation, title, status, depth, recommendation="pursue",
                    novelty="potential_opportunity", feasibility="feasible"):
    c = await create_root_candidate(db_session, investigation_id=investigation.id, origin="discover", title=title,
                                    research_question="Q?", facets=FACETS)
    c.status = status
    evaluation = Evaluation(candidate_id=c.id, depth=depth, step="done", status="done")
    db_session.add(evaluation)
    await db_session.flush()
    for dimension, category in (("recommendation", recommendation), ("novelty", novelty),
                                ("feasibility", feasibility), ("significance", "moderate")):
        db_session.add(Verdict(evaluation_id=evaluation.id, dimension=dimension, category=category, rule="x",
                               rules_version="v"))
    await db_session.flush()
    return c


async def test_finalists_are_ranked_with_reasons_and_the_rest_explained(db_session):
    investigation = await create_investigation(db_session, mode="discover", complexity="beginner",
                                               input={"field": "NLP", "count": 3})
    await evaluated(db_session, investigation, "Feasible with work", "finalist", "full",
                    feasibility="feasible_with_adjustments")
    await evaluated(db_session, investigation, "Refine it", "finalist", "full", recommendation="refine")
    await evaluated(db_session, investigation, "Best", "finalist", "full", novelty="supported_opportunity")
    await evaluated(db_session, investigation, "Waiting for full", "advanced", "light")
    old = await create_root_candidate(db_session, investigation_id=investigation.id, origin="discover",
                                      title="Too big", research_question="Q?", facets=FACETS)
    newer = await create_refined_candidate(db_session, old, title="Smaller", research_question="Q?", facets=FACETS)
    old.status, newer.status, newer.change_note = "refined", "rejected", "One model instead of three."
    newer.rejection_reason = "Rule 3: previously investigated"
    reserve = await create_root_candidate(db_session, investigation_id=investigation.id, origin="discover",
                                          title="In reserve", research_question="Q?", facets=FACETS)
    reserve.status = "proposed"
    await db_session.flush()

    results = await ranked_results(db_session, investigation.id)
    assert [r.candidate.title for r in results.finalists] == ["Best", "Feasible with work", "Refine it"]
    # Best differs from the second in novelty and feasibility; novelty is the earlier rule, so it is the reason
    assert results.finalists[0].ahead_because == "novelty: supported opportunity before potential opportunity"
    assert results.finalists[1].ahead_because == "recommendation: pursue before refine"
    assert results.finalists[2].ahead_because is None
    assert [r.candidate.title for r in results.advanced] == ["Waiting for full"]
    others = {c.title: why for c, why in results.others}
    assert others == {"Too big": "refined into v2: One model instead of three.",
                      "Smaller": "Rule 3: previously investigated",
                      "In reserve": "waiting in reserve (never evaluated)"}

    investigation.stage, investigation.status = "ranking", "running"  # as a real run would be at this point
    await db_session.flush()
    await complete_results(sessions_on(db_session), investigation.id)
    await db_session.refresh(investigation)
    assert (investigation.stage, investigation.status) == ("results_ready", "completed")


async def test_the_mode2_report(db_session):
    investigation, candidate = await advanced_after_light(db_session)
    await full_evaluation_step(sessions_on(db_session), investigation.id, models=full_models(), search_factory=search)
    candidate = await db_session.get(Candidate, candidate.id)
    candidate.status = "finalist"  # the fake verdicts end in rule 7; make it the finalist for the report
    await db_session.flush()

    report = await build_report(db_session, investigation.id)
    for heading in ("# Eureka report: research ideas in NLP", "## Your request", "## Field brief and landscape",
                    "## Open problems found in the literature", "## Ranked ideas",
                    "### 1. Judge calibration Georgian", "#### Verdicts", "#### Coverage",
                    "## Other ideas and why they stopped", "## Cost"):
        assert heading in report, heading
    assert "- Exclusions: medical" in report and "**LLM agent evaluation**" in report
    assert "judges problem (3 papers, 3 independent author groups)" in report
    assert "Why #1: recommendation" in report and "The idea's status: finalist" in report
    assert "Translated benchmark artifacts (rejected)" in report  # set aside, with its reason
    assert "#### Verdicts (light evaluation)" not in report  # the finalist's full evaluation is shown


async def test_the_mode1_report_lists_refined_versions(db_session):
    investigation_id, idea = await mode1_needs_refinement(db_session)
    await refine_step(sessions_on(db_session), investigation_id, settings=REFINE_SETTINGS,
                      model=FakeRefiner.answering(refined()))
    report = await build_report(db_session, investigation_id)
    assert report.startswith(f"# Eureka report: {idea.title}")
    assert "## Your idea" in report and "## Verdicts" in report and "your idea differs" not in report.split(
        "## Refined versions")[1]
    assert "## Refined versions and alternatives" in report
    assert "- Smaller version (v2, refine; light evaluating): its light evaluation didn't finish" in report
    assert (await db_session.get(Investigation, investigation_id)).mode == "evaluate"
