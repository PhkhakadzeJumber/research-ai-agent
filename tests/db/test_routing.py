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


async def at_triage(db_session, mode="discover", count=3, budget=12.0, cost=0, light_room=0):
    """light_room: light evaluations still allowed. 0 by default, so replenish (line 3) stays out of the way of
    the other lines; the replenish tests give it room."""
    investigation = await create_investigation(db_session, mode=mode, complexity="beginner",
                                               input={"field": "NLP", "count": count})
    investigation.stage, investigation.status = "triage", "running"
    investigation.budget, investigation.cost_usd = {"cost_usd": budget, "light_evaluations": light_room}, cost
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


async def test_too_few_survivors_replenish_from_the_reserve_first(db_session):
    investigation = await at_triage(db_session, count=3, light_room=2)
    await idea(db_session, investigation, "A", "advanced")
    await idea(db_session, investigation, "Waiting", "proposed")  # in reserve
    decision = await route(db_session, investigation)
    assert (decision.stage, decision.task) == ("generating_candidates", "replenish")
    assert decision.reason == "1 of 3 ideas survived: replenishing (1 in reserve)"


async def test_without_a_reserve_the_scout_is_asked_once(db_session):
    investigation = await at_triage(db_session, count=3, light_room=2)
    await idea(db_session, investigation, "A", "advanced")
    decision = await route(db_session, investigation)
    assert decision.reason == "1 of 3 ideas survived: replenishing (asking the Scout for more)"

    used = await at_triage(db_session, count=3, light_room=2)
    await idea(db_session, used, "A", "advanced")
    await idea(db_session, used, "From the Scout's round", "rejected", origin="replenish")
    assert (await route(db_session, used)).stage == "full_evaluation"  # the one round is used


async def test_no_replenish_without_room_for_light_evaluations(db_session):
    investigation = await at_triage(db_session, count=3, light_room=0)
    await idea(db_session, investigation, "A", "advanced")
    await idea(db_session, investigation, "Waiting", "proposed")
    assert (await route(db_session, investigation)).stage == "full_evaluation"


async def test_a_rejected_user_idea_gets_alternatives_once(db_session):
    mode1 = await at_triage(db_session, mode="evaluate")
    await idea(db_session, mode1, "Mine", "rejected", origin="user")
    decision = await route(db_session, mode1)
    assert (decision.stage, decision.task) == ("generating_candidates", "alternatives")

    after = await at_triage(db_session, mode="evaluate")
    mine = await idea(db_session, after, "Mine", "rejected", origin="user")
    await idea(db_session, after, "An alternative", "rejected", origin="alternative")
    assert (await route(db_session, after)).stage == "ranking"  # alternatives were generated already
    assert mine.status == "rejected"


async def test_a_rejected_refinement_of_the_user_idea_also_gets_alternatives(db_session):
    mode1 = await at_triage(db_session, mode="evaluate")
    v1 = await idea(db_session, mode1, "Mine", "needs_refinement", origin="user")
    await refined(db_session, v1, "Mine v2", "rejected")
    assert (await route(db_session, mode1)).task == "alternatives"


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
