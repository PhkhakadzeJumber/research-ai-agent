"""Tests for the screening and reading steps (src/ai/workflows/evaluation.py).
Real test database (rolled back), fake Claude and fake sources."""

import json

import pytest
from google.genai import types
from sqlalchemy import select

from src.ai.workflows.evaluation import read_step, retrieve_step, screen_step, selected_papers
from src.ai.workflows.transitions import StaleState
from src.db.models import Evaluation, LlmCall, RetrievalHit
from tests.db.test_clarification_workflow import sessions_on
from tests.db.test_evaluation_workflow import (
    READ_SETTINGS,
    SCREEN_SETTINGS,
    FakeSearch,
    planned,
    screening_answer,
)
from tests.fakes import PaperAwareLlm, ScriptedLlm, answer

pytestmark = [pytest.mark.anyio, pytest.mark.db]

CORE = "rag hallucination georgian question answering"
RAG = "retrieval augmented generation"
READING = answer(json.dumps({"contribution_type": "empirical_study"}))


async def retrieved(db_session, papers_by_query):
    """An evaluation at `screening` whose search found the given papers."""
    investigation, evaluation_id = await planned(db_session)
    await retrieve_step(sessions_on(db_session), evaluation_id, FakeSearch(papers_by_query))
    return investigation, evaluation_id


async def evaluation(db_session, evaluation_id):
    row = await db_session.get(Evaluation, evaluation_id)
    await db_session.refresh(row)
    return row


async def hits(db_session, evaluation_id):
    return (await db_session.execute(
        select(RetrievalHit).where(RetrievalHit.evaluation_id == evaluation_id)
    )).scalars().all()


# --- screening ------------------------------------------------------------------------------------------

async def test_papers_are_scored_and_the_most_relevant_selected(db_session):
    # A found by two queries, B and C once: order A, B, C -> labels P1, P2, P3
    _, evaluation_id = await retrieved(db_session, {CORE: ["Paper A", "Paper B", "Paper C"], RAG: ["Paper A"]})
    screener = ScriptedLlm.with_answers(screening_answer(2, 3, 0))

    outcome = await screen_step(sessions_on(db_session), evaluation_id, max_read=5, settings=SCREEN_SETTINGS,
                                model=screener)

    assert outcome.score_counts == {3: 1, 2: 1, 1: 0, 0: 1} and outcome.selected == 2
    rows = {hit.llm_reason: hit for hit in await hits(db_session, evaluation_id)}
    assert all(hit.screened for hit in rows.values())
    assert (rows["reason 2"].llm_score, rows["reason 2"].selected_for_reading) == (3, True)
    assert rows["reason 3"].selected_for_reading is False  # score 0 is never read
    chosen = [paper.title for paper, _ in await selected_papers(db_session, evaluation_id)]
    assert chosen == ["Paper B", "Paper A"]  # score 3 before score 2

    row = await evaluation(db_session, evaluation_id)
    assert row.step == "reading"
    assert (row.counters["screened"], row.counters["selected_for_reading"], row.counters["max_read"]) == (3, 2, 5)
    [call] = (await db_session.execute(select(LlmCall).where(LlmCall.role == "relevance_screening"))).scalars().all()
    assert call.evaluation_id == evaluation_id


async def test_the_reading_limit_and_what_it_leaves_out(db_session):
    _, evaluation_id = await retrieved(db_session, {CORE: ["Paper A", "Paper B", "Paper C"]})
    outcome = await screen_step(sessions_on(db_session), evaluation_id, max_read=1, settings=SCREEN_SETTINGS,
                                model=ScriptedLlm.with_answers(screening_answer(2, 3, 2)))
    assert (outcome.selected, outcome.relevant_not_selected) == (1, 2)
    assert [paper.title for paper, _ in await selected_papers(db_session, evaluation_id)] == ["Paper B"]


async def test_papers_are_screened_in_batches(db_session):
    _, evaluation_id = await retrieved(db_session, {CORE: ["Paper A", "Paper B", "Paper C"]})
    screener = ScriptedLlm.with_answers(screening_answer(3, 2), screening_answer(1))
    outcome = await screen_step(sessions_on(db_session), evaluation_id, batch_size=2, concurrency=1,
                                settings=SCREEN_SETTINGS, model=screener)
    assert outcome.screened == 3 and len(screener.requests) == 2
    assert 'label="P1"' in screener.requests[1].contents[-1].parts[0].text  # labels restart in each batch


async def test_a_failed_batch_leaves_its_papers_unscreened_and_degrades_the_step(db_session):
    _, evaluation_id = await retrieved(db_session, {CORE: ["Paper A", "Paper B", "Paper C"]})
    broken = answer("not json at all")
    screener = ScriptedLlm.with_answers(screening_answer(3, 2), broken, broken)  # second batch fails twice
    outcome = await screen_step(sessions_on(db_session), evaluation_id, batch_size=2, concurrency=1,
                                settings=SCREEN_SETTINGS, model=screener)

    assert (outcome.screened, outcome.not_screened, outcome.selected) == (2, 1, 2)
    row = await evaluation(db_session, evaluation_id)
    assert row.step == "reading" and row.counters["degraded_steps"][0]["step"] == "screening"
    assert len((await db_session.execute(select(LlmCall).where(LlmCall.role == "relevance_screening")))
               .scalars().all()) == 3  # the failed attempts still cost money


async def test_screening_refuses_to_run_out_of_order(db_session):
    _, evaluation_id = await planned(db_session)  # still at retrieving
    with pytest.raises(StaleState):
        await screen_step(sessions_on(db_session), evaluation_id, model=ScriptedLlm.with_answers())


# --- reading --------------------------------------------------------------------------------------------

async def screened(db_session, scores, papers=("Paper A", "Paper B")):
    investigation, evaluation_id = await retrieved(db_session, {CORE: list(papers)})
    await screen_step(sessions_on(db_session), evaluation_id, settings=SCREEN_SETTINGS,
                      model=ScriptedLlm.with_answers(screening_answer(*scores)))
    return investigation, evaluation_id


async def test_selected_papers_are_read_into_the_library(db_session):
    investigation, evaluation_id = await screened(db_session, (3, 2))
    chosen = await selected_papers(db_session, evaluation_id)
    reader = PaperAwareLlm.with_answers({str(paper.id): READING for paper, _ in chosen})

    outcome = await read_step(sessions_on(db_session), evaluation_id, settings=READ_SETTINGS, model=reader)

    assert (outcome.read_now, outcome.reused, outcome.failed) == (2, 0, 0)
    assert sorted(reader.seen_paper_ids) == sorted(str(paper.id) for paper, _ in chosen)
    row = await evaluation(db_session, evaluation_id)
    assert row.step == "assessing_novelty" and row.counters["read_now"] == 2
    reads = (await db_session.execute(select(LlmCall).where(LlmCall.role == "paper_reader"))).scalars().all()
    assert {call.evaluation_id for call in reads} == {evaluation_id}
    assert {call.investigation_id for call in reads} == {investigation.id}


async def test_papers_already_in_the_library_are_not_read_again(db_session):
    _, first = await screened(db_session, (3, 2))
    chosen = await selected_papers(db_session, first)
    await read_step(sessions_on(db_session), first, settings=READ_SETTINGS,
                    model=PaperAwareLlm.with_answers({str(p.id): READING for p, _ in chosen}))

    _, second = await screened(db_session, (3, 2))  # a second evaluation finding the same papers
    reader = PaperAwareLlm.with_answers({})
    outcome = await read_step(sessions_on(db_session), second, settings=READ_SETTINGS, model=reader)
    assert (outcome.read_now, outcome.reused) == (0, 2) and reader.seen_paper_ids == []


async def test_a_failed_read_degrades_the_step_but_the_evaluation_goes_on(db_session):
    _, evaluation_id = await screened(db_session, (3, 2))
    chosen = await selected_papers(db_session, evaluation_id)
    first, second = chosen[0][0], chosen[1][0]
    reader = PaperAwareLlm.with_answers({str(first.id): READING,
                                         str(second.id): answer("", types.FinishReason.SAFETY)})  # a refusal
    outcome = await read_step(sessions_on(db_session), evaluation_id, settings=READ_SETTINGS, model=reader)

    assert (outcome.read_now, outcome.failed) == (1, 1)
    row = await evaluation(db_session, evaluation_id)
    assert row.step == "assessing_novelty" and row.status == "running"
    assert row.counters["degraded_steps"][-1] == {"step": "reading", "reason": "1 selected papers failed to be read"}


async def test_nothing_relevant_means_nothing_to_read(db_session):
    _, evaluation_id = await screened(db_session, (1, 0))
    outcome = await read_step(sessions_on(db_session), evaluation_id, settings=READ_SETTINGS,
                              model=PaperAwareLlm.with_answers({}))
    assert outcome.papers == []
    assert (await evaluation(db_session, evaluation_id)).step == "assessing_novelty"
