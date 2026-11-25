"""The step pages (Figma pages 3 and 4): the stepper and each step's page, read from what a run stored. No AI call:
the runs here are built row by row, so every number on a page can be checked."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

from src.api.steps import _event_keys
from src.db.models import Evaluation, LlmCall, Paper, ProgressEvent, RetrievalHit, SearchQuery, Verdict
from src.db.repositories.candidates import FacetToStore, create_root_candidate
from src.db.repositories.runs import create_investigation
from src.timeutil import now
from tests.db.test_results import evaluated

pytestmark = [pytest.mark.anyio, pytest.mark.db]

FACETS = [FacetToStore("problem", "agents fail more in low-resource languages", True, True)]


def event(seq, kind, *, stage=None, step=None, message="", at=None, data=None):
    return ProgressEvent(investigation_id=uuid.uuid4(), seq=seq, kind=kind, stage=stage, step=step,
                         message=message or kind, data=data or {}, created_at=at or now())


# --- which step each event belongs to ---------------------------------------------------------------------------

def test_mode_1_events_follow_the_evaluation_then_the_verdict():
    events = [event(1, "step_started", stage="understanding"), event(2, "step_done", stage="understanding"),
              event(3, "ended"), event(4, "stage", stage="awaiting_clarification"),
              event(5, "stage", stage="full_evaluation"), event(6, "step_started", step="planning_queries"),
              event(7, "step_done", step="retrieving"), event(8, "step_done", step="screening"),
              event(9, "step_done", step="skeptic_review"), event(10, "step_done", step="revising_novelty"),
              event(11, "step_done", step="concluding"),
              event(12, "step_done", step="screening"),  # an alternative's light evaluation: part of the Verdict step
              event(13, "candidate", stage="triage"), event(14, "ended"),
              event(15, "step_started", stage="writing_proposal")]
    keys = [k for _, k in _event_keys("evaluate", events)]
    assert keys == ["idea", "idea", "idea", "idea", "search", "search", "search", "screening", "skeptic", "skeptic",
                    "verdict", "verdict", "verdict", "verdict", "package"]


def test_mode_2_events_follow_the_stages():
    events = [event(1, "stage", stage="understanding"), event(2, "stage", stage="landscape_retrieval"),
              event(3, "step_done", step="screening"), event(4, "stage", stage="opportunity_mining"),
              event(5, "stage", stage="generating_candidates"), event(6, "stage", stage="prescreening"),
              event(7, "stage", stage="light_evaluation"), event(8, "step_done", step="reading"),
              event(9, "candidate", stage="triage"), event(10, "stage", stage="full_evaluation"),
              event(11, "candidate", stage="triage"), event(12, "stage", stage="ranking")]
    keys = [k for _, k in _event_keys("discover", events)]
    assert keys == ["field", "landscape", "landscape", "problems", "ideas", "prescreen", "light", "light", "light",
                    "full", "full", "ranking"]


# --- a Mode 1 run, built row by row -----------------------------------------------------------------------------

async def mode1(db_session, user, *, stage="full_evaluation", step="screening", status="running"):
    investigation = await create_investigation(db_session, mode="evaluate", complexity="beginner",
                                               input={"idea": "Token tax"}, user_id=user.id)
    investigation.stage = stage
    idea = await create_root_candidate(db_session, investigation_id=investigation.id, origin="user",
                                       title="Token tax in agent trajectories", research_question="Does it?",
                                       facets=FACETS)
    evaluation = Evaluation(candidate_id=idea.id, depth="full", step=step, status=status)
    db_session.add(evaluation)
    await db_session.flush()
    return investigation, idea, evaluation


async def searches_and_papers(db_session, investigation, evaluation):
    """2 first searches (the second finds 1 new paper), a Skeptic search, 4 papers screened 3, 2, 0 and 1."""
    q1, q2 = (SearchQuery(investigation_id=investigation.id, evaluation_id=evaluation.id, origin="analyst",
                          source=source, query_text=text, result_count=n)
              for source, text, n in (("openalex", "tokenizer fertility agents", 3), ("arxiv", "tokenizer tax", 2)))
    skeptic = SearchQuery(investigation_id=investigation.id, evaluation_id=evaluation.id, origin="skeptic", round=1,
                          source="arxiv", query_text="prior work", result_count=1)
    db_session.add_all([q1, q2, skeptic])
    await db_session.flush()
    papers = [Paper(title=f"Paper {n}", tier=2, year=2025, openalex_id=f"W{uuid.uuid4().int % 10**9}") for n in range(5)]
    db_session.add_all(papers)
    await db_session.flush()
    rows = [(papers[0], [q1.id, q2.id], 3, True), (papers[1], [q1.id], 2, True), (papers[2], [q1.id], 0, False),
            (papers[3], [q2.id], 1, False), (papers[4], [skeptic.id], 3, True)]
    for paper, found_by, score, read in rows:
        db_session.add(RetrievalHit(investigation_id=investigation.id, evaluation_id=evaluation.id, paper_id=paper.id,
                                    found_by_query_ids=found_by, llm_score=score, llm_reason=f"score {score}",
                                    screened=True, selected_for_reading=read))
    await db_session.flush()
    return papers


async def test_the_stepper_follows_your_ideas_evaluation(client, db_session, user, runs):
    investigation, _, evaluation = await mode1(db_session, user, step="reading")
    page = (await client.get(f"/api/v1/investigations/{investigation.id}/steps")).json()
    assert page["mode"] == "evaluate" and page["current"] == "reading"
    states = {s["key"]: s["state"] for s in page["steps"]}
    assert states == {"idea": "done", "search": "done", "screening": "done", "reading": "paused",
                      "novelty": "upcoming", "skeptic": "upcoming", "value": "upcoming", "verdict": "upcoming",
                      "package": "upcoming"}
    assert [s["label"] for s in page["steps"]][-1] == "Research package" and page["steps"][-1]["optional"] is True

    investigation.stage = "awaiting_clarification"
    await db_session.flush()
    waiting = (await client.get(f"/api/v1/investigations/{investigation.id}/steps")).json()
    assert waiting["current"] == "idea" and waiting["steps"][0]["state"] == "waiting"

    investigation.stage, evaluation.step, evaluation.status = "results_ready", "done", "done"
    await db_session.flush()
    done = (await client.get(f"/api/v1/investigations/{investigation.id}/steps")).json()
    assert done["current"] is None and all(s["state"] == "done" for s in done["steps"][:8])


async def test_search_and_screening_count_only_the_first_searches(client, db_session, user):
    investigation, _, evaluation = await mode1(db_session, user)
    await searches_and_papers(db_session, investigation, evaluation)
    url = f"/api/v1/investigations/{investigation.id}/steps"
    search = (await client.get(f"{url}/search")).json()
    assert search["output"]["kind"] == "search" and search["output"]["papers"] == 4  # not the Skeptic's paper
    assert [(q["text"], q["found"], q["new"]) for q in search["output"]["queries"]] == [
        ("tokenizer fertility agents", 3, 3), ("tokenizer tax", 2, 1)]
    assert search["did"].startswith("Eureka searched 2 phrasings of the idea on arxiv and openalex (2 searches). "
                                    "They returned 5 results; after merging duplicates, 4 distinct papers remained.")
    assert (search["previous"], search["next"]) == ("idea", "screening")
    assert "WHAT THIS STEP" not in search["does"] and search["does"].startswith("Eureka turns the idea")

    screening = (await client.get(f"{url}/screening")).json()["output"]
    assert (screening["screened"], screening["relevant"], screening["selected"]) == (4, 2, 2)
    assert screening["scores"] == {"0": 1, "1": 1, "2": 1, "3": 1}
    assert [p["score"] for p in screening["kept"]] == [3, 2] and [p["score"] for p in screening["dropped"]] == [1, 0]


async def test_a_steps_time_and_credits_come_from_its_events_and_calls(client, db_session, user):
    investigation, _, evaluation = await mode1(db_session, user)
    start = now() - timedelta(minutes=30)
    for seq, kind, stage, step, minutes in [(1, "step_started", "understanding", None, 0),
                                           (2, "step_done", "understanding", None, 1),
                                           (3, "ended", None, None, 1),
                                           (4, "stage", "awaiting_clarification", None, 20),  # the user took 19 min
                                           (5, "step_started", None, "retrieving", 20),
                                           (6, "step_done", None, "retrieving", 22)]:
        db_session.add(ProgressEvent(investigation_id=investigation.id, seq=seq, kind=kind, stage=stage, step=step,
                                     message=kind, data={}, created_at=start + timedelta(minutes=minutes)))
    for role, minutes, usd in (("idea_analyst", 0.5, "0.05"), ("relevance_screening", 21, "0.03")):
        db_session.add(LlmCall(investigation_id=investigation.id, evaluation_id=evaluation.id if minutes > 1 else None,
                               role=role, model="m", effort="low", prompt_version="v", attempt=1, input_tokens=1,
                               output_tokens=1, thinking_tokens=0, cache_read_tokens=0, cache_creation_tokens=0,
                               latency_ms=1, stop_reason="end_turn", cost_usd=Decimal(usd),
                               created_at=start + timedelta(minutes=minutes)))
    await db_session.flush()
    steps = {s["key"]: s for s in (await client.get(f"/api/v1/investigations/{investigation.id}/steps")).json()["steps"]}
    assert steps["idea"]["seconds"] == 60  # the time Eureka worked, not the 19 minutes it waited for you
    assert steps["search"]["seconds"] == 120
    # by when they happened: the screening agent's call ran inside the search step's window
    assert (steps["idea"]["credits"], steps["search"]["credits"]) == (2, 1)
    page = (await client.get(f"/api/v1/investigations/{investigation.id}/steps/idea")).json()
    assert [line["message"] for line in page["log"]] == ["step_started", "step_done", "ended", "stage"]
    assert {"label": "Time", "value": "1 min 00 s"} in page["stats"]


async def test_novelty_skeptic_value_and_verdict_summarize_and_point_to_their_pages(client, db_session, user):
    investigation, idea, evaluation = await mode1(db_session, user, stage="results_ready", step="done", status="done")
    evaluation.novelty_assessment = {"closest_works": [{"paper_id": "a"}, {"paper_id": "b"}]}
    evaluation.coverage = {"skeptic_rounds": [{"round": 1, "challenges": 2, "new_relevant": 5},
                                              {"round": 2, "challenges": 1, "new_relevant": 0}]}
    for dimension, category in (("novelty", "potential_opportunity"), ("recommendation", "pursue"),
                                ("significance", "moderate"), ("feasibility", "feasible")):
        db_session.add(Verdict(evaluation_id=evaluation.id, dimension=dimension, category=category, rule="R2",
                               rules_version="v"))
    await db_session.flush()
    url = f"/api/v1/investigations/{investigation.id}/steps"
    novelty = (await client.get(f"{url}/novelty")).json()
    assert novelty["output"] == {"kind": "ref", "view": "matrix", "candidate_id": str(idea.id)}
    assert "kept 2 closest works" in novelty["did"] and "potential opportunity (rule R2)" in novelty["did"]
    skeptic = (await client.get(f"{url}/skeptic")).json()
    assert skeptic["did"].startswith("The Skeptic ran 2 rounds") and "raised 3 challenges" in skeptic["did"]
    assert "round 1: 5 new papers, 2 challenges" in skeptic["did"]
    verdict = (await client.get(f"{url}/verdict")).json()
    assert verdict["did"].startswith("The recommendation is pursue (rule R2).") and verdict["next"] == "package"
    package = (await client.get(f"{url}/package")).json()
    assert package["step"]["state"] == "upcoming" and "optional" in package["did"]


async def test_not_reached_steps_say_so(client, db_session, user):
    investigation, _, _ = await mode1(db_session, user, stage="awaiting_clarification")
    page = (await client.get(f"/api/v1/investigations/{investigation.id}/steps/reading")).json()
    assert page["step"]["state"] == "upcoming" and page["did"] == "Not reached yet."
    unknown = await client.get(f"/api/v1/investigations/{investigation.id}/steps/landscape")  # a Mode 2 step
    assert unknown.status_code == 404


# --- Mode 2 ------------------------------------------------------------------------------------------------------

async def test_mode_2_steps_list_the_ideas_and_open_each_finalists_evaluation(client, db_session, user):
    investigation = await create_investigation(db_session, mode="discover", complexity="beginner",
                                               input={"field": "NLP", "count": 3}, user_id=user.id)
    investigation.stage = "results_ready"
    finalist = await evaluated(db_session, investigation, "Token tax", "finalist", "full")
    finalist.prescreen = {"decision": "light_evaluation", "checks": {"time": {"needs_weeks": 8, "available_weeks": 10}}}
    dropped = await evaluated(db_session, investigation, "Needs a cluster", "screened_out", "light", recommendation="reconsider")
    dropped.prescreen, dropped.rejection_reason = {"decision": "screened_out"}, "Needs a cluster: over your compute"
    await db_session.flush()
    url = f"/api/v1/investigations/{investigation.id}/steps"
    overview = (await client.get(url)).json()
    assert [s["label"] for s in overview["steps"]] == ["Your field", "Landscape", "Open problems", "Ideas",
                                                       "Pre-screen", "Light check", "Full evaluation", "Ranking",
                                                       "Package"]
    prescreen = (await client.get(f"{url}/prescreen")).json()
    rows = {r["title"]: r for r in prescreen["output"]["ideas"]}
    assert rows["Needs a cluster"]["decision"] == "screened_out" and "over your compute" in rows["Needs a cluster"]["detail"]
    assert rows["Token tax"]["detail"] == "needs about 8 of your 10 weeks"
    assert "1 dropped, 1 chosen for the light evaluation" in prescreen["did"]
    full = (await client.get(f"{url}/full")).json()
    assert [r["title"] for r in full["output"]["ideas"]] == ["Token tax"] and full["did"] == "1 finalist had the full evaluation: 1 pursue."

    own = (await client.get(f"{url}/verdict", params={"candidate": str(finalist.id)})).json()
    assert own["candidate_id"] == str(finalist.id) and own["step"]["state"] == "done"
    assert own["did"].startswith("The recommendation is pursue") and (own["previous"], own["next"]) == ("value", None)
    other = await create_investigation(db_session, mode="discover", complexity="beginner", input={"field": "x"},
                                       user_id=user.id)
    stranger = await evaluated(db_session, other, "Elsewhere", "finalist", "full")
    assert (await client.get(f"{url}/verdict", params={"candidate": str(stranger.id)})).status_code == 404
    mode1_run, _, _ = await mode1(db_session, user)
    wrong = await client.get(f"/api/v1/investigations/{mode1_run.id}/steps/search", params={"candidate": str(finalist.id)})
    assert wrong.status_code == 422


async def test_nobody_opens_someone_elses_steps(client, db_session):
    from tests.api.test_api_billing import new_user
    other = await new_user(db_session, "steps-other@example.com")
    investigation, _, _ = await mode1(db_session, other)
    assert (await client.get(f"/api/v1/investigations/{investigation.id}/steps")).status_code == 404
    assert (await client.get(f"/api/v1/investigations/{investigation.id}/steps/search")).status_code == 404
