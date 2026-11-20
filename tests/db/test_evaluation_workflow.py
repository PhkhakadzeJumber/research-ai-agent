"""Tests for the first evaluation steps (src/ai/workflows/evaluation.py), evaluation state changes,
and the command's evaluation flow. Real test database (rolled back), fake Claude and fake sources."""

import json
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from scripts.evaluate import evaluate
from src.ai.workflows.clarification import confirm_idea, load_pending
from src.ai.workflows.evaluation import begin_evaluation, plan_step, retrieve_step
from src.ai.workflows.states import IllegalTransition
from src.ai.workflows.transitions import (
    StaleState,
    advance_evaluation,
    end_evaluation,
    note_degraded,
    start_evaluation,
)
from src.db.models import Candidate, Evaluation, Investigation, LlmCall, RetrievalHit, SearchQuery
from src.db.repositories.candidates import FacetToStore, create_root_candidate
from src.db.repositories.runs import create_investigation
from src.retrieval.records import ExternalIds, PaperRecord, SearchResult, SourceReport
from tests.ai.test_query_plan import SETTINGS, full_plan
from tests.db.test_clarification_workflow import sessions_on, waiting
from tests.fakes import ScriptedLlm, answer

pytestmark = [pytest.mark.anyio, pytest.mark.db]


class FakeSearch:
    """Answers every query from a table of {query text: [paper titles]}; others find nothing.
    `fail` lists sources that fail on every query."""

    def __init__(self, papers_by_query=None, *, fail=(), sources=("openalex", "arxiv")):
        self.papers_by_query = papers_by_query or {}
        self.fail, self.sources, self.requests = set(fail), sources, []

    async def search(self, request):
        self.requests.append(request)
        titles = self.papers_by_query.get(request.query, [])
        records = tuple(PaperRecord(ids=ExternalIds(openalex_id=f"W{abs(hash(t)) % 10**8}"), title=t,
                                    sources=("openalex",), tier=1) for t in titles)
        reports = tuple(SourceReport(source=s, ok=False, error="timed out") if s in self.fail
                        else SourceReport(source=s, ok=True, result_count=len(records) if s == "openalex" else 0)
                        for s in self.sources)
        found = () if "openalex" in self.fail else records
        return SearchResult(request=request, papers=found, reports=reports, retrieved_count=len(found))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


async def confirmed(db_session):
    """An investigation whose idea was confirmed: Candidate v1 is full_evaluating."""
    investigation = await waiting(db_session)
    sessions = sessions_on(db_session)
    pending = await load_pending(sessions, investigation.id)
    candidate_id = await confirm_idea(sessions, investigation.id, final=pending.reading, explicit=True)
    return investigation, candidate_id


async def planned(db_session):
    investigation, candidate_id = await confirmed(db_session)
    sessions = sessions_on(db_session)
    evaluation_id = await begin_evaluation(sessions, candidate_id, depth="full")
    llm = ScriptedLlm.with_answers(answer(json.dumps(full_plan())))
    await plan_step(sessions, evaluation_id, settings=SETTINGS, model=llm)
    return investigation, evaluation_id


async def rows(db_session, model, **where):
    statement = select(model)
    for name, value in where.items():
        statement = statement.where(getattr(model, name) == value)
    return (await db_session.execute(statement)).scalars().all()


# --- evaluation state changes ---------------------------------------------------------------------------

async def a_candidate(db_session):
    investigation = await create_investigation(db_session, mode="evaluate", complexity="beginner", input={})
    return await create_root_candidate(db_session, investigation_id=investigation.id, origin="user", title="T",
                                       research_question="Q?", facets=[FacetToStore("problem", "p", True, True)])


async def test_an_evaluation_walks_its_steps_and_ends_done(db_session):
    candidate = await a_candidate(db_session)
    evaluation = await start_evaluation(db_session, candidate.id, depth="light")
    assert (evaluation.step, evaluation.status) == ("normalizing", "running")
    path = ["normalizing", "planning_queries", "retrieving", "screening", "reading", "assessing_novelty",
            "skeptic_review", "assessing_value_feasibility", "concluding", "done"]
    for current, target in zip(path, path[1:]):
        await advance_evaluation(db_session, evaluation.id, to=target, expected_from=current)
    await db_session.refresh(evaluation)
    assert (evaluation.step, evaluation.status) == ("done", "done") and evaluation.finished_at is not None
    with pytest.raises(IllegalTransition, match="has ended"):
        await advance_evaluation(db_session, evaluation.id, to="concluding", expected_from="done")


async def test_illegal_and_stale_steps_are_refused(db_session):
    candidate = await a_candidate(db_session)
    evaluation = await start_evaluation(db_session, candidate.id, depth="full")
    with pytest.raises(IllegalTransition, match="normalizing -> reading"):
        await advance_evaluation(db_session, evaluation.id, to="reading", expected_from="normalizing")
    with pytest.raises(StaleState, match="at normalizing, not retrieving"):
        await advance_evaluation(db_session, evaluation.id, to="screening", expected_from="retrieving")


async def test_a_degraded_step_ends_the_evaluation_partial(db_session):
    candidate = await a_candidate(db_session)
    evaluation = await start_evaluation(db_session, candidate.id, depth="light")
    await note_degraded(db_session, evaluation.id, "retrieving", "arXiv was down")
    await advance_evaluation(db_session, evaluation.id, to="planning_queries", expected_from="normalizing")
    for current, target in [("planning_queries", "retrieving"), ("retrieving", "screening"),
                            ("screening", "reading"), ("reading", "assessing_novelty"),
                            ("assessing_novelty", "skeptic_review"),
                            ("skeptic_review", "assessing_value_feasibility"),
                            ("assessing_value_feasibility", "concluding"), ("concluding", "done")]:
        await advance_evaluation(db_session, evaluation.id, to=target, expected_from=current)
    await db_session.refresh(evaluation)
    assert evaluation.status == "partial"
    assert evaluation.counters["degraded_steps"] == [{"step": "retrieving", "reason": "arXiv was down"}]


async def test_extending_a_light_evaluation_starts_at_planning(db_session):
    candidate = await a_candidate(db_session)
    light = await start_evaluation(db_session, candidate.id, depth="light")
    await end_evaluation(db_session, light.id, status="done")
    full = await start_evaluation(db_session, candidate.id, depth="full", extends_id=light.id)
    assert (full.step, full.extends_id) == ("planning_queries", light.id)
    with pytest.raises(IllegalTransition):
        await end_evaluation(db_session, full.id, status="running")


async def test_search_rows_need_a_real_evaluation(db_session):
    investigation = await create_investigation(db_session, mode="evaluate", complexity="beginner", input={})
    savepoint = await db_session.begin_nested()
    db_session.add(SearchQuery(investigation_id=investigation.id, evaluation_id=uuid.uuid4(), origin="analyst",
                               source="arxiv", query_text="x"))
    with pytest.raises(IntegrityError, match="fk_search_queries_evaluation_id_evaluations"):
        await db_session.flush()
    await savepoint.rollback()


# --- normalizing + planning -----------------------------------------------------------------------------

async def test_the_users_idea_skips_normalizing(db_session):
    _, candidate_id = await confirmed(db_session)
    evaluation_id = await begin_evaluation(sessions_on(db_session), candidate_id, depth="full")
    evaluation = await db_session.get(Evaluation, evaluation_id)
    assert (evaluation.step, evaluation.depth) == ("planning_queries", "full")
    assert "skipped" in evaluation.counters["normalizing"]


async def test_scout_candidates_need_normalizing_which_comes_later(db_session):
    investigation = await create_investigation(db_session, mode="discover", complexity="beginner", input={})
    scout = await create_root_candidate(db_session, investigation_id=investigation.id, origin="discover",
                                        title="T", research_question="Q?",
                                        facets=[FacetToStore("problem", "p", True, True)])
    with pytest.raises(NotImplementedError, match="step 5"):
        await begin_evaluation(sessions_on(db_session), scout.id, depth="light")


async def test_the_plan_is_stored_with_its_cost_and_the_step_moves_on(db_session):
    investigation, evaluation_id = await planned(db_session)
    evaluation = await db_session.get(Evaluation, evaluation_id)
    await db_session.refresh(evaluation)
    assert evaluation.step == "retrieving"
    assert evaluation.query_plan == full_plan() and evaluation.counters["queries_planned"] == 16
    [call] = await rows(db_session, LlmCall, evaluation_id=evaluation_id)
    assert (call.investigation_id, call.prompt_version) == (investigation.id,
                                                            "shared_preamble@1+idea_analyst_query_plan@1")


async def test_a_failed_plan_falls_back_and_degrades_the_evaluation(db_session):
    _, candidate_id = await confirmed(db_session)
    sessions = sessions_on(db_session)
    evaluation_id = await begin_evaluation(sessions, candidate_id, depth="full")
    broken = json.dumps({"queries": []})
    outcome = await plan_step(sessions, evaluation_id, settings=SETTINGS,
                              model=ScriptedLlm.with_answers(answer(broken), answer(broken)))

    assert outcome.fallback_reason.startswith("LlmOutputInvalid") and outcome.plan.queries
    evaluation = await db_session.get(Evaluation, evaluation_id)
    await db_session.refresh(evaluation)
    assert evaluation.step == "retrieving" and evaluation.status == "running"  # the investigation goes on
    assert evaluation.counters["degraded_steps"][0]["step"] == "planning_queries"
    assert len(await rows(db_session, LlmCall, evaluation_id=evaluation_id)) == 2  # the failed calls still cost


async def test_steps_refuse_to_run_out_of_order(db_session):
    _, evaluation_id = await planned(db_session)
    with pytest.raises(StaleState, match="not planning_queries"):
        await plan_step(sessions_on(db_session), evaluation_id, model=ScriptedLlm.with_answers())


# --- retrieving -----------------------------------------------------------------------------------------

async def test_searches_are_stored_with_their_kind_and_papers_deduplicated(db_session):
    investigation, evaluation_id = await planned(db_session)
    search = FakeSearch({
        "rag hallucination georgian question answering": ["Paper A", "Paper B"],
        "retrieval augmented generation": ["Paper B", "Paper C"],  # B again: one paper, one hit
    })

    outcome = await retrieve_step(sessions_on(db_session), evaluation_id, search)

    assert (outcome.searches, outcome.retrieved, outcome.unique_papers) == (16, 4, 3)
    assert {request.limit for request in search.requests} == {20}  # full depth
    queries = await rows(db_session, SearchQuery, evaluation_id=evaluation_id)
    assert len(queries) == 32  # 16 searches x 2 sources
    assert {(q.origin, q.query_type) for q in queries if q.query_text == "retrieval augmented generation"} == {
        ("analyst", "terminology_variant")}
    hits = await rows(db_session, RetrievalHit, evaluation_id=evaluation_id)
    assert len(hits) == 3 and all(hit.investigation_id == investigation.id for hit in hits)

    evaluation = await db_session.get(Evaluation, evaluation_id)
    await db_session.refresh(evaluation)
    assert evaluation.step == "screening"
    counters = evaluation.counters
    assert (counters["searches"], counters["retrieved"], counters["unique_papers"],
            counters["failed_source_searches"]) == (16, 4, 3, 0)


async def test_a_failing_source_is_recorded_not_fatal(db_session):
    _, evaluation_id = await planned(db_session)
    search = FakeSearch({"rag hallucination georgian question answering": ["Paper A"]}, fail=("arxiv",))
    outcome = await retrieve_step(sessions_on(db_session), evaluation_id, search)
    assert outcome.failed_source_searches == 16 and outcome.unique_papers == 1 and not outcome.failed
    failed = [q for q in await rows(db_session, SearchQuery, evaluation_id=evaluation_id) if q.error]
    assert {q.source for q in failed} == {"arxiv"}


async def test_if_nothing_could_be_searched_the_investigation_fails(db_session):
    investigation, evaluation_id = await planned(db_session)
    outcome = await retrieve_step(sessions_on(db_session), evaluation_id, FakeSearch(fail=("openalex", "arxiv")))
    assert outcome.failed
    evaluation = await db_session.get(Evaluation, evaluation_id)
    await db_session.refresh(evaluation)
    await db_session.refresh(investigation)
    assert (evaluation.status, evaluation.step) == ("failed", "retrieving")
    assert investigation.status == "failed"


# --- the command ----------------------------------------------------------------------------------------

async def test_the_command_runs_planning_and_retrieval(db_session, capsys):
    investigation, _ = await confirmed(db_session)
    llm = ScriptedLlm.with_answers(answer(json.dumps(full_plan())))
    search = FakeSearch({"rag hallucination georgian question answering": ["Paper A", "Paper B"]})

    code = await evaluate(sessions_on(db_session), investigation.id, search_factory=lambda: search,
                          settings=SETTINGS, model=llm)

    assert code == 0
    output = capsys.readouterr().out
    assert "Search plan: 16 queries" in output and "terminology_variant" in output
    assert "16 searches -> 2 results -> 2 unique papers" in output
    assert "1. Paper A" in output and "screening and reading" in output


async def test_the_command_resumes_where_the_evaluation_stopped(db_session, capsys):
    investigation, evaluation_id = await planned(db_session)  # already planned
    llm = ScriptedLlm.with_answers()  # planning must not run again
    code = await evaluate(sessions_on(db_session), investigation.id, search_factory=lambda: FakeSearch(), model=llm)
    assert code == 0 and llm.requests == []
    assert "Planning the searches" not in capsys.readouterr().out
    assert (await db_session.get(Candidate, (await db_session.get(Evaluation, evaluation_id)).candidate_id))
    assert (await db_session.get(Investigation, investigation.id)).stage == "full_evaluation"
