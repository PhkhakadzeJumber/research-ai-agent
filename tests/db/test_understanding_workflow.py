"""Tests for intake -> understanding -> awaiting_clarification (src/ai/workflows/understanding.py).
Real test database (rolled back), fake Claude: no network, no cost."""

import json
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from google.genai import types
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.ai.workflows.understanding import NotAnIdeaInvestigation, idea_request, understand_idea
from src.config import RoleLlmSettings
from src.db.models import Intake, Investigation, LlmCall
from src.db.repositories.intakes import find_intake, store_intake
from src.db.repositories.runs import create_investigation
from tests.ai.test_structured_idea_schema import VALID
from tests.fakes import ScriptedLlm, answer

pytestmark = [pytest.mark.anyio, pytest.mark.db]

SETTINGS = RoleLlmSettings(role="idea_analyst", model="claude-opus-5-5", effort="medium", max_tokens=16000)
BROKEN = json.dumps({**VALID, "facets": VALID["facets"][:2]})


def sessions_on(db_session):
    @asynccontextmanager
    async def sessions():
        yield db_session
        await db_session.flush()

    return sessions


async def idea_investigation(session, **input_overrides):
    return await create_investigation(
        session, mode="evaluate", complexity="beginner",
        input={"idea": "RAG for Georgian QA with small LMs", "field": "NLP", "seed_papers": ["2401.12345"],
               **input_overrides},
        constraints={"available_time": "8 weeks", "compute": "laptop only", "budget": None},
    )


async def test_the_reading_is_stored_and_the_run_waits_for_clarification(db_session):
    investigation = await idea_investigation(db_session)
    llm = ScriptedLlm.with_answers(answer(json.dumps(VALID)))

    outcome = await understand_idea(sessions_on(db_session), investigation.id, settings=SETTINGS, model=llm)

    assert outcome.error is None and outcome.reading.title == VALID["title"]
    await db_session.refresh(investigation)
    assert (investigation.stage, investigation.status) == ("awaiting_clarification", "clarifying")
    assert investigation.cost_usd > Decimal(0)

    intake = await find_intake(db_session, investigation.id)
    assert intake.id == outcome.intake_id
    assert intake.agent_reading == VALID  # stored unchanged
    assert (intake.mode, intake.schema_version) == ("evaluate", "structured-idea-v2")
    assert intake.prompt_version == "shared_preamble@1+idea_analyst@2"
    assert (intake.user_edits, intake.answers, intake.confirmed_at) == ({}, [], None)  # step 4.3

    [call] = (await db_session.execute(select(LlmCall))).scalars().all()
    assert (call.role, call.investigation_id) == ("idea_analyst", investigation.id)


async def test_the_analyst_sees_the_investigations_request(db_session):
    investigation = await idea_investigation(db_session)
    llm = ScriptedLlm.with_answers(answer(json.dumps(VALID)))
    await understand_idea(sessions_on(db_session), investigation.id, settings=SETTINGS, model=llm)

    packet = llm.requests[0].contents[-1].parts[0].text
    assert "RAG for Georgian QA with small LMs" in packet and "- 2401.12345" in packet
    assert "- available time: 8 weeks" in packet and "budget" not in packet  # empty constraints left out


async def test_a_failed_reading_fails_the_investigation_and_keeps_the_cost(db_session):
    investigation = await idea_investigation(db_session)
    llm = ScriptedLlm.with_answers(answer(BROKEN), answer(BROKEN))

    outcome = await understand_idea(sessions_on(db_session), investigation.id, settings=SETTINGS, model=llm)

    assert outcome.reading is None and outcome.error.startswith("LlmOutputInvalid")
    await db_session.refresh(investigation)
    assert (investigation.stage, investigation.status) == ("understanding", "failed")
    assert await find_intake(db_session, investigation.id) is None
    calls = (await db_session.execute(select(LlmCall))).scalars().all()
    assert [call.attempt for call in calls] == [1, 2] and investigation.cost_usd > Decimal(0)


async def test_a_refusal_fails_the_investigation(db_session):
    investigation = await idea_investigation(db_session)
    llm = ScriptedLlm.with_answers(answer("", types.FinishReason.SAFETY))

    outcome = await understand_idea(sessions_on(db_session), investigation.id, settings=SETTINGS, model=llm)

    assert outcome.error.startswith("LlmRefusal")
    await db_session.refresh(investigation)
    assert investigation.status == "failed"


async def test_only_new_mode_1_investigations_are_understood(db_session):
    discover = await create_investigation(db_session, mode="discover", complexity="beginner", input={"field": "NLP"})
    with pytest.raises(NotAnIdeaInvestigation):
        await understand_idea(sessions_on(db_session), discover.id, settings=SETTINGS, model=ScriptedLlm())
    with pytest.raises(NotAnIdeaInvestigation):
        idea_request(await idea_investigation(db_session, idea="  "))


async def test_one_intake_per_investigation(db_session):
    investigation = await idea_investigation(db_session)
    await store_intake(db_session, investigation_id=investigation.id, mode="evaluate", agent_reading=VALID,
                       schema_version="s", prompt_version="p")
    savepoint = await db_session.begin_nested()
    with pytest.raises(IntegrityError):
        await store_intake(db_session, investigation_id=investigation.id, mode="evaluate", agent_reading=VALID,
                           schema_version="s", prompt_version="p")
    await savepoint.rollback()


async def test_deleting_an_investigation_deletes_its_intake(db_session):
    investigation = await idea_investigation(db_session)
    await store_intake(db_session, investigation_id=investigation.id, mode="evaluate", agent_reading=VALID,
                       schema_version="s", prompt_version="p")
    await db_session.delete(investigation)
    await db_session.flush()
    assert (await db_session.execute(select(Intake))).scalars().all() == []
    assert await db_session.get(Investigation, investigation.id) is None
