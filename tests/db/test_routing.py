"""Tests for the router after triage (src/ai/workflows/routing.py). Real test database (rolled back); pure code."""

import pytest

from src.ai.workflows.routing import route_after_triage
from src.ai.workflows.transitions import StaleState
from src.db.models import Investigation
from src.db.repositories.candidates import FacetToStore, create_refined_candidate, create_root_candidate
from src.db.repositories.runs import create_investigation
from tests.db.test_clarification_workflow import sessions_on

pytestmark = [pytest.mark.anyio, pytest.mark.db]

FACETS = [FacetToStore("problem", "p", True, False)]


async def at_triage(db_session, mode="discover", count=3, budget=12.0, cost=0):
    investigation = await create_investigation(db_session, mode=mode, complexity="beginner",
                                               input={"field": "NLP", "count": count})
    investigation.stage, investigation.status = "triage", "running"
    investigation.budget, investigation.cost_usd = {"cost_usd": budget}, cost
    await db_session.flush()
    return investigation


async def idea(db_session, investigation, title, status, origin="discover"):
    candidate = await create_root_candidate(db_session, investigation_id=investigation.id, origin=origin,
                                            title=title, research_question="Q?", facets=FACETS)
    candidate.status = status
    await db_session.flush()
    return candidate


async def refined(db_session, parent, title, status):
    child = await create_refined_candidate(db_session, parent, title=title, research_question="Q?", facets=FACETS)
    parent.status, child.status = "refined", status
    await db_session.flush()
    return child


async def route(db_session, investigation):
    decision = await route_after_triage(sessions_on(db_session), investigation.id)
    await db_session.refresh(investigation)
    assert investigation.stage == decision.stage
    return decision


async def test_a_refinement_comes_first(db_session):
    investigation = await at_triage(db_session)
    await idea(db_session, investigation, "A", "needs_refinement")
    await idea(db_session, investigation, "B", "advanced")
    decision = await route(db_session, investigation)
    assert (decision.stage, decision.reason) == ("refining", "1 candidate(s) to refine")


async def test_a_lineage_refined_twice_is_rejected(db_session):
    investigation = await at_triage(db_session)
    v1 = await idea(db_session, investigation, "A", "needs_refinement")
    v2 = await refined(db_session, v1, "A v2", "needs_refinement")
    v3 = await refined(db_session, v2, "A v3", "needs_refinement")
    await idea(db_session, investigation, "B", "advanced")
    decision = await route(db_session, investigation)

    assert [c.id for c in decision.out_of_refinements] == [v3.id]
    await db_session.refresh(v3)
    assert v3.status == "rejected"
    assert v3.rejection_reason == "Refinement limit reached: this idea was refined 2 times already."
    assert decision.stage == "full_evaluation"  # then B, advanced


async def test_the_investigation_allows_four_refinements(db_session):
    investigation = await at_triage(db_session)
    for name in ("A", "B", "C", "D"):
        root = await idea(db_session, investigation, name, "needs_refinement")
        await refined(db_session, root, f"{name} v2", "rejected")  # 4 refinements used
    last = await idea(db_session, investigation, "E", "needs_refinement")
    decision = await route(db_session, investigation)
    await db_session.refresh(last)
    assert last.status == "rejected" and "4 refinements in this investigation" in last.rejection_reason
    assert decision.stage == "ranking"


async def test_advanced_candidates_go_to_full_evaluation_otherwise_ranking(db_session):
    investigation = await at_triage(db_session)
    await idea(db_session, investigation, "A", "advanced")
    assert (await route(db_session, investigation)).stage == "full_evaluation"

    other = await at_triage(db_session)
    await idea(db_session, other, "B", "rejected")
    decision = await route(db_session, other)
    assert (decision.stage, decision.reason) == ("ranking", "nothing left to evaluate")


async def test_replenish_and_alternatives_wait_for_their_scout_tasks(db_session):
    # too few survivors (Mode 2) and a rejected user idea (Mode 1) would go to generating_candidates once
    # step 5.5b adds those tasks; until then the router goes on to the next line
    investigation = await at_triage(db_session, count=3)
    await idea(db_session, investigation, "A", "advanced")
    assert (await route(db_session, investigation)).stage == "full_evaluation"

    mode1 = await at_triage(db_session, mode="evaluate")
    await idea(db_session, mode1, "Mine", "rejected", origin="user")
    assert (await route(db_session, mode1)).stage == "ranking"


async def test_a_used_up_budget_goes_to_ranking(db_session):
    investigation = await at_triage(db_session, budget=1.0, cost=1)
    await idea(db_session, investigation, "A", "needs_refinement")
    decision = await route(db_session, investigation)
    assert (decision.stage, decision.reason) == ("ranking", "the budget is used up: finishing with what exists")


async def test_routing_needs_the_triage_stage(db_session):
    investigation = await at_triage(db_session)
    investigation.stage = "light_evaluation"
    await db_session.flush()
    with pytest.raises(StaleState):
        await route_after_triage(sessions_on(db_session), investigation.id)
    assert (await db_session.get(Investigation, investigation.id)).stage == "light_evaluation"
