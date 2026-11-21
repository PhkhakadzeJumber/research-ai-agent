"""Tests for opportunity mining (src/ai/workflows/mining.py, src/ai/agents/opportunity_miner.py) and its
part of scripts/discover.py. Real test database (rolled back), fake Claude and fake sources."""

import json
import re
import uuid

import pytest
from google.adk.models.base_llm import BaseLlm
from pydantic import PrivateAttr
from sqlalchemy import select

from scripts.discover import mine
from src.ai.workflows.landscape import landscape_reading_step
from src.ai.workflows.mining import author_groups, mining_step
from src.ai.workflows.transitions import StaleState
from src.config import RoleLlmSettings
from src.db.models import Investigation, LlmCall, OpportunityItem, Paper
from tests.db.test_clarification_workflow import sessions_on
from tests.db.test_evaluation_workflow import READ_SETTINGS, SCREEN_SETTINGS
from tests.db.test_landscape_reading import TitleScreener, searched
from tests.fakes import model_response

pytestmark = [pytest.mark.anyio, pytest.mark.db]

MINE_SETTINGS = RoleLlmSettings(role="opportunity_mining", model="claude-haiku-5-5", effort="low", max_tokens=16000)
_TITLE = re.compile(r"<title>([^<]*)</title>")
_ITEM = re.compile(r'<item label="(M\d+)"[^>]*>.*?<statement>([^<]*)</statement>')
_PAIR = re.compile(r'<pair label="(X\d+)">')


def finding(claim, direction):
    return {"claim": claim, "direction": direction, "setting": "s", "quote": None}


# title -> (findings, future work, datasets, tasks). Every quote is the paper's whole abstract
# ("We study <title>."), so it verifies.
PAPERS = {
    "Judges fail in Georgian": ([finding("judges fail on low-resource languages", "negative")], [], [], []),
    "Judge drift in Swahili": ([finding("judges drift on low-resource languages", "negative")], [], [], []),
    "Judges and Finnish": ([], ["calibrate judges for low-resource languages"], [], []),
    "Judges work fine": ([finding("judges agree with humans everywhere", "positive")], [], [], []),
    "Tool use on XNLI": ([finding("tools break in other languages", "negative")], [], ["XNLI"], ["tool use"]),
    "Tools in Arabic": ([], ["make tools robust across languages"], ["MadeUpSet"], []),  # name not in the text
    "A survey of agent evaluation": ([finding("surveys a field", "not_applicable")], [], [], []),
}
TABLE = {"LLM agent evaluation a": [t for t in PAPERS if t != "Tools in Arabic"],
         "multilingual tool use a": ["Tool use on XNLI", "Tools in Arabic"]}


class TitleReader(BaseLlm):
    """A fake Paper Reader answering from PAPERS by the paper's title."""

    model: str = "claude-opus-5-5"

    async def generate_content_async(self, llm_request, stream=False):
        text = "\n".join(p.text for c in llm_request.contents for p in c.parts if p.text)
        title = _TITLE.search(text).group(1)
        findings, future, datasets, tasks = PAPERS[title]
        quote = {"text": f"We study {title}.", "chunk_id": None}
        yield model_response(json.dumps({
            "findings": [{**f, "quote": quote} for f in findings],
            "future_work": [{"statement": s, "quote": quote} for s in future],
            "datasets": datasets, "tasks": tasks, "contribution_type": "empirical_study"}))


class KeywordMiner(BaseLlm):
    """A fake opportunity miner: groups statements by keyword; confirms the first `confirm` pairs."""

    model: str = "claude-haiku-5-5"
    confirm: int = 1
    _requests: list = PrivateAttr(default_factory=list)

    @property
    def requests(self):
        return self._requests

    async def generate_content_async(self, llm_request, stream=False):
        text = "\n".join(p.text for c in llm_request.contents for p in c.parts if p.text)
        self._requests.append(text)
        if "<items>" in text:
            items = _ITEM.findall(text)
            groups = [{"label": f"{word} problem", "items": [label for label, s in items if word in s]}
                      for word in ("judges", "tools")]
            yield model_response(json.dumps({"groups": [g for g in groups if g["items"]]}), model=self.model)
        else:
            pairs = _PAIR.findall(text)
            yield model_response(json.dumps({"checks": [
                {"pair": p, "contradiction": n < self.confirm, "shared_question": "do judges work?", "reason": "r"}
                for n, p in enumerate(pairs)]}), model=self.model)


async def read_landscape(db_session, authors=None):
    """A Mode 2 investigation at opportunity_mining whose landscape papers were all read.
    authors: {title: [names]}."""
    investigation = await searched(db_session, TABLE)
    await landscape_reading_step(sessions_on(db_session), investigation.id, screen_settings=SCREEN_SETTINGS,
                                 screen_model=TitleScreener.scoring({t: 2 for t in PAPERS}),
                                 read_settings=READ_SETTINGS, read_model=TitleReader())
    for title, names in (authors or {}).items():
        paper = (await db_session.execute(select(Paper).where(Paper.title == title))).scalars().first()
        paper.authors = names
    await db_session.flush()
    return investigation


def run(db_session, investigation, miner=None):
    return mining_step(sessions_on(db_session), investigation.id, settings=MINE_SETTINGS,
                       model=miner or KeywordMiner())


# --- author groups (pure code) ---------------------------------------------------------------------------

def test_papers_sharing_an_author_are_one_group():
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    authors = {a: ["Ana Beridze", "Bo Li"], b: ["bo  li"], c: ["Bo Li", "Cy Doe"], d: ["Dee Ng"]}
    assert author_groups([a, b, c, d], authors) == 2  # a-b-c through "Bo Li" (spacing and case ignored); d
    assert author_groups([a, d], {}) == 2  # no authors known: every paper is its own group
    assert author_groups([a, a], authors) == 1


# --- the step --------------------------------------------------------------------------------------------

async def test_recurring_problems_contradictions_subtopics_and_datasets(db_session):
    investigation = await read_landscape(db_session, {"Judges fail in Georgian": ["Ana Beridze"],
                                                      "Judge drift in Swahili": ["Ana Beridze", "Bo Li"],
                                                      "Judges and Finnish": ["Cy Doe"]})
    miner = KeywordMiner()
    outcome = await run(db_session, investigation, miner)

    # 6 statements: the not_applicable finding is no mining material
    assert (outcome.papers, outcome.statements, outcome.groups) == (7, 6, 2)
    assert "surveys a field" not in miner.requests[0] and "<field_brief>" in miner.requests[0]
    (cluster,) = outcome.clusters
    assert cluster.label == "judges problem"
    # problem statements from 3 papers in 2 author groups; the positive finding is kept as context
    assert cluster.counts == {"papers": 3, "author_groups": 2, "future_work": 1, "findings": 2}
    assert len(cluster.quote_ids) == 4 and len(cluster.paper_ids) == 4
    assert cluster.prompt_version.endswith("opportunity_mining_grouping@1")
    assert outcome.dropped == [("tools problem", 2, 2)]  # only 2 papers: not recurring

    # the positive judge finding against both negative ones: 2 pairs, the miner confirms 1
    assert outcome.pairs_checked == 2
    (contradiction,) = outcome.contradictions
    assert contradiction.label == "do judges work?" and len(set(contradiction.paper_ids)) == 2
    assert contradiction.content["directions"][0] == "positive"

    subtopics = {item.label: item for item in outcome.subtopics}
    assert subtopics["LLM agent evaluation"].counts["landscape_papers"] == 6
    assert subtopics["multilingual tool use"].counts["read"] == 2
    assert len(subtopics["LLM agent evaluation"].content["surveys"]) == 1
    assert [d.label for d in outcome.datasets] == ["XNLI"]  # MadeUpSet isn't in the paper's text
    assert outcome.datasets[0].content["tasks"] == ["tool use"]

    stored = (await db_session.execute(select(OpportunityItem).where(
        OpportunityItem.investigation_id == investigation.id))).scalars().all()
    assert sorted({item.type for item in stored}) == ["contradiction", "dataset", "limitation_cluster", "subtopic"]
    calls = (await db_session.execute(select(LlmCall).where(LlmCall.investigation_id == investigation.id,
                                                            LlmCall.role == "opportunity_mining"))).scalars().all()
    assert len(calls) == 2
    await db_session.refresh(investigation)
    assert investigation.stage == "generating_candidates"


async def test_one_author_group_is_not_recurring(db_session):
    shared = ["Ana Beridze"]
    investigation = await read_landscape(db_session, {"Judges fail in Georgian": shared,
                                                      "Judge drift in Swahili": shared, "Judges and Finnish": shared})
    outcome = await run(db_session, investigation)
    assert outcome.clusters == []
    assert ("judges problem", 3, 1) in outcome.dropped


async def test_unknown_and_repeated_labels_are_ignored(db_session):
    investigation = await read_landscape(db_session)

    class Sloppy(KeywordMiner):
        async def generate_content_async(self, llm_request, stream=False):
            text = "\n".join(p.text for c in llm_request.contents for p in c.parts if p.text)
            if "<items>" in text:
                labels = [label for label, _ in _ITEM.findall(text)]
                yield model_response(json.dumps({"groups": [
                    {"label": "everything", "items": [*labels, "M99"]},
                    {"label": "again", "items": labels[:2]},  # already used: dropped
                ]}), model=self.model)
            else:
                yield model_response(json.dumps({"checks": []}), model=self.model)

    outcome = await run(db_session, investigation, Sloppy())
    assert outcome.groups == 1
    assert [c.label for c in outcome.clusters] == ["everything"] and len(outcome.clusters[0].quote_ids) == 6
    assert outcome.contradictions == []  # the miner checked nothing: no pair is kept


async def test_mining_needs_the_opportunity_mining_stage(db_session):
    investigation = await searched(db_session, TABLE)  # at landscape_reading
    with pytest.raises(StaleState):
        await run(db_session, investigation)


async def test_a_failed_call_stores_nothing(db_session, capsys):
    investigation = await read_landscape(db_session)

    class Broken(KeywordMiner):
        async def generate_content_async(self, llm_request, stream=False):
            yield model_response("not json", model=self.model)

    assert await mine(sessions_on(db_session), investigation.id, model=Broken()) == 1
    assert "Nothing was stored" in capsys.readouterr().out
    stored = (await db_session.execute(select(OpportunityItem).where(
        OpportunityItem.investigation_id == investigation.id))).scalars().all()
    assert stored == []
    assert (await db_session.get(Investigation, investigation.id)).stage == "opportunity_mining"


async def test_the_command_shows_what_was_mined(db_session, capsys):
    investigation = await read_landscape(db_session, {"Judges fail in Georgian": ["A"], "Judge drift in Swahili": ["B"],
                                                      "Judges and Finnish": ["C"]})
    from tests.db.test_scouting import FakeScout, eight  # imported here: test_scouting imports this module

    assert await mine(sessions_on(db_session), investigation.id, model=KeywordMiner(),
                      scout_model=FakeScout.proposing(eight)) == 0
    output = capsys.readouterr().out
    assert "6 verified statements from 7 papers -> 2 groups" in output
    assert "Recurring open problems: 1" in output and " 3 papers, 3 author groups  judges problem" in output
    assert "Contradictions: 1 of 2 suspected pairs" in output and "Datasets named: 1" in output
    assert "Generating candidate ideas" in output  # mining goes on to the Scout (5.3, test_scouting)
