"""Tests for Workflow B's first stages (src/ai/workflows/field.py) and the interactive part of
scripts/discover.py. Real test database (rolled back), fake Claude."""

import json

import pytest
from sqlalchemy import select

from scripts.discover import clarify_brief, format_brief
from src.ai.schemas.field_brief import FieldBriefOutput
from src.ai.workflows.clarification import answers_for
from src.ai.workflows.field import (
    NotADiscoveryInvestigation,
    apply_brief_answers,
    confirm_brief,
    load_pending_brief,
    remove_subtopics,
    understand_field,
)
from src.db.models import Investigation, LlmCall
from src.db.repositories.intakes import find_intake
from src.db.repositories.runs import create_investigation
from tests.ai.test_field_brief import SETTINGS, VALID, brief, subtopic
from tests.db.test_clarification_workflow import sessions_on
from tests.fakes import ScriptedLlm, answer

pytestmark = [pytest.mark.anyio, pytest.mark.db]

INPUT = {"field": "NLP", "interests": "LLM agents", "exclusions": ["medical"], "count": 3}


async def understood(db_session, reading=VALID, *, attempts=1):
    """attempts=2 when the brief touches an exclusion: A1 is asked once more (and here answers the same)."""
    investigation = await create_investigation(db_session, mode="discover", complexity="beginner", input=INPUT,
                                               constraints={"available_time": "10 weeks"})
    await understand_field(sessions_on(db_session), investigation.id, settings=SETTINGS,
                           model=ScriptedLlm.with_answers(*[answer(json.dumps(reading))] * attempts))
    return investigation


async def stage(db_session, investigation):
    await db_session.refresh(investigation)
    return investigation.stage, investigation.status


async def test_the_brief_is_stored_and_the_run_waits_for_clarification(db_session):
    investigation = await understood(db_session)
    assert await stage(db_session, investigation) == ("awaiting_clarification", "clarifying")
    intake = await find_intake(db_session, investigation.id)
    assert (intake.mode, intake.schema_version, intake.agent_reading) == ("discover", "field-brief-v1", VALID)
    [call] = (await db_session.execute(select(LlmCall))).scalars().all()
    assert call.prompt_version == "shared_preamble@1+idea_analyst_field_brief@1"


async def test_a_failed_brief_fails_the_investigation(db_session):
    investigation = await create_investigation(db_session, mode="discover", complexity="beginner", input=INPUT)
    broken = answer("not json")
    outcome = await understand_field(sessions_on(db_session), investigation.id, settings=SETTINGS,
                                     model=ScriptedLlm.with_answers(broken, broken))
    assert outcome.error.startswith("LlmOutputInvalid")
    assert await stage(db_session, investigation) == ("understanding", "failed")


async def test_only_mode_2_requests_get_a_field_brief(db_session):
    idea = await create_investigation(db_session, mode="evaluate", complexity="beginner", input={"idea": "x"})
    with pytest.raises(NotADiscoveryInvestigation):
        await understand_field(sessions_on(db_session), idea.id, settings=SETTINGS, model=ScriptedLlm())


async def test_confirming_moves_to_the_landscape_search(db_session):
    investigation = await understood(db_session)
    sessions = sessions_on(db_session)
    pending = await load_pending_brief(sessions, investigation.id)
    answers = answers_for(pending.brief, [None])
    final = await confirm_brief(sessions, investigation.id, final=pending.brief, answers=answers)

    assert len(final.subtopics) == 4
    assert await stage(db_session, investigation) == ("landscape_retrieval", "running")
    assert investigation.assumptions == ["empirical work is preferred", "English only"]
    intake = await find_intake(db_session, investigation.id)
    assert intake.confirmed_at is not None and intake.user_edits["final_brief"]["subtopics"][0]["name"] == (
        "LLM agent evaluation")


async def test_subtopics_touching_an_exclusion_are_removed_at_confirmation(db_session):
    reading = brief(subtopics=[*VALID["subtopics"], subtopic("medical dialogue")])
    investigation = await understood(db_session, reading, attempts=2)  # still touching it after the rewrite
    sessions = sessions_on(db_session)
    pending = await load_pending_brief(sessions, investigation.id)
    final = await confirm_brief(sessions, investigation.id, final=pending.brief)
    assert [s.name for s in final.subtopics] == [s["name"] for s in VALID["subtopics"]]
    intake = await find_intake(db_session, investigation.id)
    assert intake.user_edits["removed_for_exclusions"] == ["medical dialogue"]


async def test_answers_are_applied_by_one_revise_call(db_session):
    investigation = await understood(db_session)
    sessions = sessions_on(db_session)
    pending = await load_pending_brief(sessions, investigation.id)
    revised = brief(subtopics=[*VALID["subtopics"][:3], subtopic("Georgian LLM evaluation")])
    llm = ScriptedLlm.with_answers(answer(json.dumps(revised)))
    outcome = await apply_brief_answers(sessions, pending, answers_for(pending.brief, ["Georgian"]),
                                        settings=SETTINGS, model=llm)
    assert outcome.revised and outcome.brief.subtopics[3].name == "Georgian LLM evaluation"
    assert outcome.brief.clarifying_questions == []
    none = await apply_brief_answers(sessions, pending, answers_for(pending.brief, [""]), model=ScriptedLlm())
    assert not none.revised


def test_removing_subtopics():
    reading = FieldBriefOutput.model_validate(VALID)
    assert [s.name for s in remove_subtopics(reading, [2, 4]).subtopics] == ["LLM agent evaluation",
                                                                            "small model reasoning"]
    with pytest.raises(ValueError, match="no subtopic number 9"):
        remove_subtopics(reading, [9])
    with pytest.raises(ValueError, match="At least one"):
        remove_subtopics(reading, [1, 2, 3, 4])


# --- the command ------------------------------------------------------------------------------------------

def keyboard(*replies):
    replies = list(replies)

    def ask(prompt):
        if not replies:
            raise EOFError
        return replies.pop(0)

    return ask


async def test_skip_remove_and_confirm(db_session, capsys):
    investigation = await understood(db_session)
    code = await clarify_brief(sessions_on(db_session), investigation.id, yes=False,
                               ask=keyboard("", "r", "2", "y"), model=ScriptedLlm())
    assert code == 0
    output = capsys.readouterr().out
    assert "Confirmed: 3 subtopics to explore." in output and "comes with step 5.1b" in output
    intake = await find_intake(db_session, investigation.id)
    assert intake.user_edits["removed_subtopics"] == ["multilingual tool use"]


async def test_quitting_keeps_the_checkpoint(db_session, capsys):
    investigation = await understood(db_session)
    code = await clarify_brief(sessions_on(db_session), investigation.id, yes=False, ask=keyboard("", "q"),
                               model=ScriptedLlm())
    assert code == 0 and f"--continue {investigation.id}" in capsys.readouterr().out
    assert await stage(db_session, investigation) == ("awaiting_clarification", "clarifying")


async def test_yes_confirms_without_questions(db_session):
    investigation = await understood(db_session)
    assert await clarify_brief(sessions_on(db_session), investigation.id, yes=True, ask=keyboard()) == 0
    assert (await db_session.get(Investigation, investigation.id)).stage == "landscape_retrieval"


def test_format_brief():
    text = format_brief(FieldBriefOutput.model_validate(VALID))
    assert "  1. LLM agent evaluation\n     why it fits you: fits\n     searches: LLM agent evaluation a |" in text
    assert "Assumed:\n  - empirical work is preferred" in text and "→ English only" in text
