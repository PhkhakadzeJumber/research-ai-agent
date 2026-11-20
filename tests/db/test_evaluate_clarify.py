"""Tests for the interactive clarification in scripts/evaluate.py, with scripted keyboard input.
Real test database (rolled back), fake Claude."""

import json

import pytest

from scripts.evaluate import clarify
from src.db.models import Candidate
from tests.db.test_clarification_workflow import PROPOSED, REVISED, SETTINGS, sessions_on, waiting
from tests.fakes import ScriptedLlm, answer

pytestmark = [pytest.mark.anyio, pytest.mark.db]


def keyboard(*replies):
    """Replies in order; after the last one, input ends (like Ctrl+Z)."""
    replies = list(replies)

    def ask(prompt):
        if not replies:
            raise EOFError
        return replies.pop(0)

    return ask


async def candidate_count(db_session):
    from sqlalchemy import func, select

    return (await db_session.execute(select(func.count()).select_from(Candidate))).scalar()


async def test_answer_then_confirm(db_session, capsys):
    investigation = await waiting(db_session, PROPOSED)
    llm = ScriptedLlm.with_answers(answer(json.dumps(REVISED)))

    code = await clarify(sessions_on(db_session), investigation.id, yes=False,
                         ask=keyboard("Georgian and Armenian", "y"), settings=SETTINGS, model=llm)

    assert code == 0 and await candidate_count(db_session) == 1
    output = capsys.readouterr().out
    assert "Applying your answers (one Idea Analyst call)" in output
    assert "Georgian and Armenian QA" in output and "Confirmed: your idea is candidate" in output


async def test_skip_edit_and_confirm(db_session, capsys):
    investigation = await waiting(db_session, PROPOSED)
    llm = ScriptedLlm.with_answers()  # nothing answered: no call

    code = await clarify(sessions_on(db_session), investigation.id, yes=False,
                         ask=keyboard("", "e", "9", "x", "e", "2", "LoRA fine-tuning", "y"), model=llm)

    assert code == 0 and llm.requests == []
    output = capsys.readouterr().out
    assert "Not changed: There is no facet number 9." in output
    assert "  2. method       * LoRA fine-tuning\n" in output  # edited: no longer [proposed]


async def test_quitting_keeps_the_checkpoint(db_session, capsys):
    investigation = await waiting(db_session)
    code = await clarify(sessions_on(db_session), investigation.id, yes=False, ask=keyboard("", "q"),
                         model=ScriptedLlm.with_answers())
    assert code == 0 and await candidate_count(db_session) == 0
    assert f"--continue {investigation.id}" in capsys.readouterr().out


async def test_yes_is_refused_when_key_facets_were_proposed(db_session, capsys):
    investigation = await waiting(db_session, PROPOSED)
    code = await clarify(sessions_on(db_session), investigation.id, yes=True, ask=keyboard())
    assert code == 2 and await candidate_count(db_session) == 0
    assert "Eureka proposed these key facets (method, data_setting)" in capsys.readouterr().out


async def test_yes_confirms_a_clear_idea(db_session):
    investigation = await waiting(db_session)
    assert await clarify(sessions_on(db_session), investigation.id, yes=True, ask=keyboard()) == 0
    assert await candidate_count(db_session) == 1
