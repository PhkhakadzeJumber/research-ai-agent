"""Tests for the clarification checkpoint (src/ai/workflows/clarification.py) and candidates.
Real test database (rolled back), fake Claude: no network, no cost."""

import json
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.ai.workflows.clarification import (
    ConfirmationRequired,
    NotWaitingForClarification,
    answers_for,
    apply_answers,
    apply_edits,
    confirm_idea,
    load_pending,
)
from src.ai.workflows.states import IllegalTransition
from src.ai.workflows.transitions import StaleState, set_candidate_status
from src.ai.workflows.understanding import understand_idea
from src.config import RoleLlmSettings
from src.db.models import Candidate, Investigation, LlmCall
from src.db.repositories.candidates import FacetToStore, candidate_facets, create_root_candidate
from src.db.repositories.intakes import find_intake
from src.db.repositories.runs import create_investigation
from tests.ai.test_structured_idea_schema import VALID
from tests.fakes import ScriptedLlm, answer

pytestmark = [pytest.mark.anyio, pytest.mark.db]

SETTINGS = RoleLlmSettings(role="idea_analyst", model="claude-opus-5-5", effort="medium", max_tokens=16000)
# A vague idea's reading: the method and data setting are Eureka's proposals.
PROPOSED = {**VALID, "facets": [{**f, "stated_by_user": f["kind"] == "problem"} for f in VALID["facets"]]}
REVISED = {**VALID, "research_question": "Does RAG reduce hallucination of sub-3B LMs in Georgian and Armenian QA?",
           "facets": [{**f, "stated_by_user": True} for f in VALID["facets"]]}


def sessions_on(db_session):
    @asynccontextmanager
    async def sessions():
        yield db_session
        await db_session.flush()

    return sessions


async def waiting(db_session, reading=VALID):
    """An investigation at the clarification checkpoint with the given reading."""
    investigation = await create_investigation(db_session, mode="evaluate", complexity="beginner",
                                               input={"idea": "RAG for Georgian QA with small LMs"})
    llm = ScriptedLlm.with_answers(answer(json.dumps(reading)))
    await understand_idea(sessions_on(db_session), investigation.id, settings=SETTINGS, model=llm)
    return investigation


async def calls(db_session):
    return (await db_session.execute(select(LlmCall).order_by(LlmCall.created_at))).scalars().all()


# --- skipping and confirming ----------------------------------------------------------------------------

async def test_skipping_creates_candidate_v1_and_starts_the_full_evaluation(db_session):
    investigation = await waiting(db_session)
    sessions = sessions_on(db_session)
    pending = await load_pending(sessions, investigation.id)
    answers = answers_for(pending.reading, [None])

    candidate_id = await confirm_idea(sessions, investigation.id, final=pending.reading, answers=answers,
                                      explicit=False)

    await db_session.refresh(investigation)
    assert (investigation.stage, investigation.status) == ("full_evaluation", "running")
    assert investigation.assumptions == ["small means under 3B parameters", "an automatic measure"]

    candidate = await db_session.get(Candidate, candidate_id)
    assert (candidate.version, candidate.root_id, candidate.parent_id) == (1, candidate.id, None)
    assert (candidate.origin, candidate.status, candidate.title) == ("user", "full_evaluating", VALID["title"])
    facets = await candidate_facets(db_session, candidate_id)
    assert [(f.kind, f.text, f.is_key, f.stated_by_user) for f in facets] == [
        (f["kind"], f["text"], f["is_key"], f["stated_by_user"]) for f in VALID["facets"]
    ]

    intake = await find_intake(db_session, investigation.id)
    assert intake.agent_reading == VALID  # A1's original reading is never changed
    assert intake.answers == [{"question": "Which hallucination measure?", "if_skipped": "an automatic measure",
                               "answer": None}]
    assert intake.user_edits["confirmed_explicitly"] is False and intake.confirmed_at is not None


async def test_proposed_key_facets_need_an_explicit_confirmation(db_session):
    investigation = await waiting(db_session, PROPOSED)
    sessions = sessions_on(db_session)
    pending = await load_pending(sessions, investigation.id)

    with pytest.raises(ConfirmationRequired, match="method, data_setting"):
        await confirm_idea(sessions, investigation.id, final=pending.reading, explicit=False)
    await db_session.refresh(investigation)
    assert investigation.stage == "awaiting_clarification"  # nothing changed
    assert (await db_session.execute(select(Candidate))).scalars().all() == []

    candidate_id = await confirm_idea(sessions, investigation.id, final=pending.reading, explicit=True)
    facets = await candidate_facets(db_session, candidate_id)
    assert [f.stated_by_user for f in facets if f.is_key] == [True, False, False]  # stays honest


async def test_a_reading_is_confirmed_only_once(db_session):
    investigation = await waiting(db_session)
    sessions = sessions_on(db_session)
    pending = await load_pending(sessions, investigation.id)
    await confirm_idea(sessions, investigation.id, final=pending.reading, explicit=True)

    with pytest.raises(StaleState):
        await confirm_idea(sessions, investigation.id, final=pending.reading, explicit=True)
    with pytest.raises(NotWaitingForClarification):
        await load_pending(sessions, investigation.id)


async def test_only_investigations_at_the_checkpoint_can_be_clarified(db_session):
    fresh = await create_investigation(db_session, mode="evaluate", complexity="beginner", input={"idea": "x"})
    with pytest.raises(NotWaitingForClarification):
        await load_pending(sessions_on(db_session), fresh.id)
    with pytest.raises(NotWaitingForClarification):
        await load_pending(sessions_on(db_session), uuid.uuid4())


# --- answers and edits ----------------------------------------------------------------------------------

async def test_answers_are_applied_by_one_revise_call(db_session):
    investigation = await waiting(db_session, PROPOSED)
    sessions = sessions_on(db_session)
    pending = await load_pending(sessions, investigation.id)
    llm = ScriptedLlm.with_answers(answer(json.dumps(REVISED)))

    outcome = await apply_answers(sessions, pending, answers_for(pending.reading, ["Georgian and Armenian"]),
                                  settings=SETTINGS, model=llm)

    assert outcome.revised and outcome.error is None
    assert outcome.reading.research_question == REVISED["research_question"]
    assert outcome.reading.clarifying_questions == []  # never asks again
    packet = llm.requests[0].contents[-1].parts[0].text
    assert '<answer question="Which hallucination measure?">Georgian and Armenian</answer>' in packet
    assert "<reading>" in packet and "RAG for Georgian QA" in packet
    revise_call = (await calls(db_session))[-1]
    assert revise_call.prompt_version == "shared_preamble@1+idea_analyst_revise@1"
    assert revise_call.investigation_id == investigation.id


async def test_no_answers_means_no_claude_call(db_session):
    investigation = await waiting(db_session)
    sessions = sessions_on(db_session)
    pending = await load_pending(sessions, investigation.id)
    llm = ScriptedLlm.with_answers()  # would fail if called

    outcome = await apply_answers(sessions, pending, answers_for(pending.reading, ["   "]), model=llm)

    assert not outcome.revised and outcome.reading == pending.reading and llm.requests == []


async def test_a_failed_revision_keeps_the_checkpoint_and_records_the_cost(db_session):
    investigation = await waiting(db_session)
    sessions = sessions_on(db_session)
    pending = await load_pending(sessions, investigation.id)
    broken = json.dumps({**VALID, "facets": VALID["facets"][:2]})
    llm = ScriptedLlm.with_answers(answer(broken), answer(broken))

    outcome = await apply_answers(sessions, pending, answers_for(pending.reading, ["human judgement"]),
                                  settings=SETTINGS, model=llm)

    assert outcome.reading is None and outcome.error.startswith("LlmOutputInvalid")
    assert len(await calls(db_session)) == 3  # the first reading + two revise attempts
    await db_session.refresh(investigation)
    assert investigation.stage == "awaiting_clarification"  # the user can try again


def test_edits_change_facets_and_count_as_stated_by_the_user():
    from src.ai.schemas.structured_idea import StructuredIdeaOutput

    reading = StructuredIdeaOutput.model_validate(PROPOSED)
    edited = apply_edits(reading, {1: "LoRA fine-tuning"})
    assert (edited.facets[1].text, edited.facets[1].stated_by_user) == ("LoRA fine-tuning", True)
    assert [f.kind for f in edited.proposed_key_facets()] == ["data_setting"]
    with pytest.raises(ValueError, match="no facet number 9"):
        apply_edits(reading, {8: "x"})
    with pytest.raises(ValueError, match="each facet must be different"):
        apply_edits(reading, {1: VALID["facets"][0]["text"]})


async def test_edits_are_stored_with_the_final_reading(db_session):
    investigation = await waiting(db_session)
    sessions = sessions_on(db_session)
    pending = await load_pending(sessions, investigation.id)
    final = apply_edits(pending.reading, {3: "human judgement of 200 answers"})

    await confirm_idea(sessions, investigation.id, final=final, edits={3: "human judgement of 200 answers"},
                       explicit=True)

    intake = await find_intake(db_session, investigation.id)
    assert intake.user_edits["facet_edits"] == {"4": "human judgement of 200 answers"}  # shown 1-based
    assert intake.user_edits["final_reading"]["facets"][3]["text"] == "human judgement of 200 answers"


# --- candidates -----------------------------------------------------------------------------------------

async def a_candidate(db_session):
    investigation = await create_investigation(db_session, mode="discover", complexity="beginner", input={})
    return await create_root_candidate(
        db_session, investigation_id=investigation.id, origin="discover", title="T", research_question="Q?",
        facets=[FacetToStore("problem", "p", True, True)],
    )


async def test_candidate_status_changes_follow_the_graph(db_session):
    candidate = await a_candidate(db_session)
    await set_candidate_status(db_session, candidate.id, to="light_evaluating", expected_from="proposed")
    with pytest.raises(StaleState, match="is light_evaluating, not proposed"):
        await set_candidate_status(db_session, candidate.id, to="light_evaluating", expected_from="proposed")
    with pytest.raises(IllegalTransition):
        await set_candidate_status(db_session, candidate.id, to="finalist", expected_from="light_evaluating")

    await set_candidate_status(db_session, candidate.id, to="rejected", expected_from="light_evaluating",
                               reason="Rule 3: previously investigated")
    await db_session.refresh(candidate)
    assert (candidate.status, candidate.rejection_reason) == ("rejected", "Rule 3: previously investigated")
    with pytest.raises(IllegalTransition, match="a final status"):
        await set_candidate_status(db_session, candidate.id, to="refined", expected_from="rejected")


@pytest.mark.parametrize(("changes", "constraint"), [
    ({"origin": "invented"}, "origin_valid"),
    ({"status": "thinking"}, "status_valid"),
    ({"version": 2}, "parent_iff_refined"),  # a refinement needs its parent
])
async def test_invalid_candidates_are_rejected(db_session, changes, constraint):
    candidate = await a_candidate(db_session)
    savepoint = await db_session.begin_nested()
    for name, value in changes.items():
        setattr(candidate, name, value)
    with pytest.raises(IntegrityError, match=constraint):
        await db_session.flush()
    await savepoint.rollback()


async def test_deleting_an_investigation_deletes_its_candidates_and_facets(db_session):
    candidate = await a_candidate(db_session)
    investigation = await db_session.get(Investigation, candidate.investigation_id)
    await db_session.delete(investigation)
    await db_session.flush()
    assert (await db_session.execute(select(Candidate))).scalars().all() == []
