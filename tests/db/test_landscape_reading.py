"""Tests for reading the landscape (landscape_reading_step in src/ai/workflows/landscape.py) and its
part of scripts/discover.py. Real test database (rolled back), fake Claude and fake sources."""

import json
from decimal import Decimal
import re
import uuid
from types import SimpleNamespace

import pytest
from google.adk.models.base_llm import BaseLlm
from pydantic import PrivateAttr
from sqlalchemy import select

from scripts.discover import read_landscape
from src.ai.workflows.landscape import choose_landscape_papers, is_survey, landscape_reading_step, landscape_step
from src.ai.workflows.transitions import StaleState
from src.db.models import Investigation, LlmCall, RetrievalHit
from tests.db.test_clarification_workflow import sessions_on
from tests.db.test_evaluation_workflow import READ_SETTINGS, SCREEN_SETTINGS
from tests.db.test_landscape import Sources, confirmed
from tests.fakes import model_response

pytestmark = [pytest.mark.anyio, pytest.mark.db]

_PAPER = re.compile(r'<paper label="([^"]+)"[^>]*><title>([^<]*)</title>')


class TitleScreener(BaseLlm):
    """A fake screener: scores each paper in the batch by its title ({title: score}, others 0)."""

    model: str = "claude-haiku-5-5"
    _scores: dict = PrivateAttr(default_factory=dict)
    _requests: list = PrivateAttr(default_factory=list)

    @classmethod
    def scoring(cls, scores):
        llm = cls()
        llm._scores = dict(scores)
        return llm

    @property
    def requests(self):
        return self._requests

    async def generate_content_async(self, llm_request, stream=False):
        text = "\n".join(p.text for c in llm_request.contents for p in c.parts if p.text)
        self._requests.append(text)
        judgements = [{"label": label, "score": self._scores.get(title, 0), "reason": "r"}
                      for label, title in _PAPER.findall(text)]
        yield model_response(json.dumps({"judgements": judgements}), model=self.model)


class SameReader(BaseLlm):
    """A fake Paper Reader: the same small analysis for every paper."""

    model: str = "claude-opus-5-5"
    _calls: int = PrivateAttr(default=0)

    @property
    def calls(self):
        return self._calls

    async def generate_content_async(self, llm_request, stream=False):
        self._calls += 1
        yield model_response(json.dumps({"contribution_type": "empirical_study"}))


async def searched(db_session, table):
    """A Mode 2 investigation at landscape_reading whose landscape found the given papers."""
    investigation = await confirmed(db_session)
    await landscape_step(sessions_on(db_session), investigation.id, Sources(table))
    return investigation


def run(db_session, investigation, screener, reader=None, **kwargs):
    return landscape_reading_step(sessions_on(db_session), investigation.id, screen_settings=SCREEN_SETTINGS,
                                  screen_model=screener, read_settings=READ_SETTINGS,
                                  read_model=reader or SameReader(), **kwargs)


# --- choosing (pure code) --------------------------------------------------------------------------------

def row(title, subtopics, score=2, found=1, rank=1):
    return (SimpleNamespace(id=uuid.uuid4(), title=title), list(subtopics), score, found, rank)


def test_surveys_are_recognised_by_title():
    assert is_survey("A Survey of LLM Agents") and is_survey("Tool use: a systematic review")
    assert not is_survey("Surveyor agents for code") and not is_survey("Agent bench")


def test_every_subtopic_gets_its_share_before_the_rest():
    rows = [row(f"A{n}", ["a"], score=3) for n in range(6)] + [row("B low", ["b"], score=2)]
    chosen = choose_landscape_papers(rows, ["a", "b"], limit=4, per_subtopic=3)
    titles = [r[0].title for r in chosen]
    assert "B low" in titles  # the weaker subtopic isn't crowded out by the stronger one
    assert len(titles) == 4


def test_surveys_first_then_score_then_how_often_found():
    rows = [row("Plain 3", ["a"], score=3), row("A review of a", ["a"], score=2),
            row("Plain 2 found twice", ["a"], score=2, found=2), row("Plain 2", ["a"], score=2)]
    chosen = choose_landscape_papers(rows, ["a"], limit=4, per_subtopic=4)
    assert [r[0].title for r in chosen] == ["A review of a", "Plain 3", "Plain 2 found twice", "Plain 2"]


def test_a_paper_of_two_subtopics_is_chosen_once():
    shared = row("Shared", ["a", "b"], score=3)
    chosen = choose_landscape_papers([shared, row("Only b", ["b"])], ["a", "b"], limit=10, per_subtopic=2)
    assert [r[0].title for r in chosen] == ["Shared", "Only b"]


# --- the step --------------------------------------------------------------------------------------------

TABLE = {"LLM agent evaluation a": ["Agent bench", "A survey of agent evaluation", "Off topic vision"],
         "LLM agent evaluation a survey": ["A survey of agent evaluation"],
         "multilingual tool use a": ["Tool use in Georgian", "Agent bench"],
         "educational QA a": ["Tutor QA"]}
SCORES = {"Agent bench": 3, "A survey of agent evaluation": 2, "Tool use in Georgian": 2, "Tutor QA": 1}


async def test_the_landscape_is_screened_chosen_and_read(db_session):
    investigation = await searched(db_session, TABLE)
    screener, reader = TitleScreener.scoring(SCORES), SameReader()
    outcome = await run(db_session, investigation, screener, reader)

    assert (outcome.screened, outcome.not_screened) == (5, 0)
    assert outcome.score_counts == {3: 1, 2: 2, 1: 1, 0: 1}
    assert "<field_brief>" in screener.requests[0] and "LLM agent evaluation" in screener.requests[0]
    chosen = {paper.title: (names, survey) for paper, names, survey in outcome.selected}
    assert set(chosen) == {"Agent bench", "A survey of agent evaluation", "Tool use in Georgian"}  # scores >= 2
    assert chosen["Agent bench"][0] == ["LLM agent evaluation", "multilingual tool use"]  # from the searches
    assert chosen["A survey of agent evaluation"] == (["LLM agent evaluation"], True)
    assert outcome.selected[0][0].title == "A survey of agent evaluation"  # surveys first
    assert (outcome.read_now, outcome.reused, outcome.failed, reader.calls) == (3, 0, 0, 3)

    hits = (await db_session.execute(select(RetrievalHit).where(RetrievalHit.investigation_id == investigation.id))
            ).scalars().all()
    assert sum(hit.selected_for_reading for hit in hits) == 3 and all(hit.screened for hit in hits)
    calls = (await db_session.execute(select(LlmCall).where(LlmCall.investigation_id == investigation.id,
                                                            LlmCall.role == "relevance_screening"))).scalars().all()
    assert calls and all("relevance_screening_landscape@1" in call.prompt_version for call in calls)
    await db_session.refresh(investigation)
    assert (investigation.stage, investigation.status) == ("opportunity_mining", "running")


async def test_the_reading_limit_is_kept(db_session):
    investigation = await searched(db_session, TABLE)
    outcome = await run(db_session, investigation, TitleScreener.scoring(SCORES), limit=2, per_subtopic=1)
    titles = [paper.title for paper, _, _ in outcome.selected]
    # one per subtopic first (the survey for agent evaluation, the best for tool use), then the limit
    assert titles == ["A survey of agent evaluation", "Agent bench"]


async def test_papers_already_in_the_library_are_not_read_again(db_session):
    investigation = await searched(db_session, TABLE)
    first = SameReader()
    await run(db_session, investigation, TitleScreener.scoring(SCORES), first)
    # a second investigation over the same papers reuses the stored analyses
    other = await searched(db_session, TABLE)
    again = SameReader()
    outcome = await run(db_session, other, TitleScreener.scoring(SCORES), again)
    assert (first.calls, again.calls, outcome.reused) == (3, 0, 3)


async def test_reading_needs_the_landscape_reading_stage(db_session):
    investigation = await confirmed(db_session)  # still at landscape_retrieval
    with pytest.raises(StaleState):
        await run(db_session, investigation, TitleScreener.scoring({}))


async def test_the_command_reads_and_stops_when_the_budget_is_used_up(db_session, capsys):
    investigation = await searched(db_session, TABLE)
    investigation.budget, investigation.cost_usd = {"cost_usd": 1.0}, Decimal("1")
    await db_session.flush()
    assert await read_landscape(sessions_on(db_session), investigation.id) == 3
    assert "Budget used up" in capsys.readouterr().out

    investigation.budget = {"cost_usd": 12.0}
    await db_session.flush()
    code = await read_landscape(sessions_on(db_session), investigation.id,
                                screen_model=TitleScreener.scoring(SCORES), read_model=SameReader(),
                                mine_model=SameReader())  # nothing to mine: no Claude call is made
    output = capsys.readouterr().out
    assert code == 0
    assert "Screened 5 papers" in output and "Chosen to read: 3 (1 surveys)" in output
    assert "Read now: 3" in output and "Mining the landscape" in output  # reading goes on to mining (5.2b)
    assert (await db_session.get(Investigation, investigation.id)).stage == "generating_candidates"
