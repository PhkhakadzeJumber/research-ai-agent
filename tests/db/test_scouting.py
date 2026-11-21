"""Tests for generating candidates (src/ai/workflows/scouting.py, src/ai/agents/scout.py) and its part of
scripts/discover.py. Real test database (rolled back), fake Claude and fake sources."""

import json
import re

import pytest
from google.adk.models.base_llm import BaseLlm
from pydantic import PrivateAttr
from sqlalchemy import select

from scripts.discover import scout
from src.ai.workflows.mining import mining_step
from src.ai.workflows.scouting import scouting_step
from src.ai.workflows.transitions import StaleState
from src.config import RoleLlmSettings
from src.db.models import Candidate, CandidateInspiration, Facet, Investigation, LlmCall, Quote
from tests.ai.test_scout_schema import candidate
from tests.db.test_clarification_workflow import sessions_on
from tests.db.test_mining import MINE_SETTINGS, KeywordMiner, read_landscape
from tests.fakes import model_response

pytestmark = [pytest.mark.anyio, pytest.mark.db]

SCOUT_SETTINGS = RoleLlmSettings(role="scout", model="claude-opus-5-5", effort="high", max_tokens=20000)
AUTHORS = {"Judges fail in Georgian": ["A"], "Judge drift in Swahili": ["B"], "Judges and Finnish": ["C"]}
_STATEMENT = re.compile(r'<statement label="(Q\d+)"[^>]*>([^<]*)<quote>')
_ITEM = re.compile(r'<item label="(O\d+)" type="([a-z_]+)"')


def more(statements, items):
    """The fake Scout's replenish answer (step 5.5b): three new grounded ideas."""
    first, second = list(statements.values())[:2]
    return [candidate(f"More idea {n}", strategy, (first, second))
            for n, strategy in enumerate(("limitation", "future_work", "practical_problem"), start=1)]


class FakeScout(BaseLlm):
    """A fake Scout: `build(statements, items)` returns the candidate list, where statements maps
    statement text -> Q label and items maps type -> [O labels]. A replenish request (it lists what was
    already tried, step 5.5b) is answered with `more` instead."""

    model: str = "claude-opus-5-5"
    _build: object = PrivateAttr()
    _more: object = PrivateAttr(default=None)
    _requests: list = PrivateAttr(default_factory=list)

    @classmethod
    def proposing(cls, build, replenish=None):
        llm = cls()
        llm._build, llm._more = build, replenish or more
        return llm

    @property
    def requests(self):
        return self._requests

    async def generate_content_async(self, llm_request, stream=False):
        text = "\n".join(p.text for c in llm_request.contents for p in c.parts if p.text)
        self._requests.append(text)
        statements = {statement: label for label, statement in _STATEMENT.findall(text)}
        items: dict[str, list[str]] = {}
        for label, kind in _ITEM.findall(text):
            items.setdefault(kind, []).append(label)
        build = self._more if "<already_tried>" in text else self._build
        yield model_response(json.dumps({"candidates": build(statements, items)}))


def eight(statements, items):
    """Eight candidates: #1 well grounded (two statements, the cluster, a paper label in the motivation),
    #2 with one statement and a dataset item only, #3 with an unknown label; the rest grounded."""
    q = statements
    fail, drift, calibrate = (q["judges fail on low-resource languages"], q["judges drift on low-resource languages"],
                              q["calibrate judges for low-resource languages"])
    cluster, dataset = items["limitation_cluster"][0], items["dataset"][0]
    ideas = [
        candidate("Calibrating judges", "limitation", (fail, drift, cluster), motivation="P1 shows judges fail."),
        candidate("XNLI tools", "combination", (q["tools break in other languages"], dataset)),
        candidate("Ghost", "future_work", (calibrate, "Q999")),
    ]
    strategies = ("practical_problem", "future_work", "limitation")
    ideas += [candidate(f"Idea {n}", strategies[n % 3], (fail, calibrate)) for n in range(5)]
    return ideas


async def mined(db_session):
    """A Mode 2 investigation at generating_candidates (one recurring problem, one contradiction)."""
    investigation = await read_landscape(db_session, AUTHORS)
    await mining_step(sessions_on(db_session), investigation.id, settings=MINE_SETTINGS, model=KeywordMiner())
    return investigation


def run(db_session, investigation, build=eight):
    return scouting_step(sessions_on(db_session), investigation.id, settings=SCOUT_SETTINGS,
                         model=FakeScout.proposing(build))


async def test_the_scout_sees_the_mined_material(db_session):
    investigation = await mined(db_session)
    fake = FakeScout.proposing(eight)
    outcome = await scouting_step(sessions_on(db_session), investigation.id, settings=SCOUT_SETTINGS, model=fake)
    packet = fake.requests[0]
    assert (outcome.items, outcome.statements) == (len(_ITEM.findall(packet)), 6)
    assert "<field_request>" in packet and "<count>3</count>" in packet
    # the recurring problem lists its statements by label; items come in a fixed order
    cluster = re.search(r'<item label="O1" type="limitation_cluster" papers="3" author_groups="3" '
                        r'statements="(Q\d+(?: Q\d+)*)">judges problem</item>', packet)
    assert cluster and len(cluster.group(1).split()) == 4
    assert re.search(r'type="subtopic" found="6" read="6" per_year="[^"]*">LLM agent evaluation: ', packet)
    assert re.search(r'type="dataset" papers="P\d+">XNLI \(tasks: tool use\)</item>', packet)
    assert '<paper label="P1"' in packet and "We study" in packet  # quotes and titles are shown


async def test_candidates_are_stored_grounded_or_screened_out(db_session):
    investigation = await mined(db_session)
    outcome = await run(db_session, investigation)

    assert len(outcome.candidates) == 8 and outcome.unknown_labels == 1
    screened = {candidate.title: candidate for candidate in outcome.screened_out}
    assert set(screened) == {"XNLI tools", "Ghost"}  # one verified quote each
    assert screened["XNLI tools"].status == "screened_out"
    assert screened["XNLI tools"].rejection_reason.startswith("Grounding: 1 verified quote(s)")

    first = outcome.candidates[0]
    assert (first.origin, first.origin_strategy, first.status, first.version) == ("discover", "limitation", "proposed", 1)
    assert first.motivation.startswith('"') and "P1" not in first.motivation  # the label became a title
    assert first.scout_notes["compute_tier"] == "T1" and first.scout_notes["risks"] == ["may exist"]
    facets = (await db_session.execute(select(Facet).where(Facet.candidate_id == first.id)
                                       .order_by(Facet.position))).scalars().all()
    assert [f.kind for f in facets] == ["problem", "method", "data_setting", "evaluation", "contribution"]
    assert not any(f.stated_by_user for f in facets) and facets[0].is_key
    links = (await db_session.execute(select(CandidateInspiration).where(
        CandidateInspiration.candidate_id == first.id))).scalars().all()
    assert sorted(link.kind for link in links) == ["opportunity_item", "quote", "quote"]
    for link in links:
        if link.kind == "quote":
            assert (await db_session.get(Quote, link.quote_id)).verified

    calls = (await db_session.execute(select(LlmCall).where(LlmCall.investigation_id == investigation.id,
                                                            LlmCall.role == "scout"))).scalars().all()
    assert len(calls) == 1 and "scout_discover@1" in calls[0].prompt_version
    await db_session.refresh(investigation)
    assert investigation.stage == "prescreening"


async def test_two_statements_of_one_quote_are_one_quote(db_session):
    investigation = await mined(db_session)

    def same_quote(statements, items):
        ideas = eight(statements, items)
        # "Tool use on XNLI" has one statement; citing its label twice still means one quote
        tools = statements["tools break in other languages"]
        ideas[1] = candidate("XNLI tools", "combination", (tools, tools))
        return ideas

    outcome = await run(db_session, investigation, same_quote)
    assert "XNLI tools" in {c.title for c in outcome.screened_out}


async def test_scouting_needs_the_generating_candidates_stage(db_session):
    investigation = await read_landscape(db_session)  # at opportunity_mining
    with pytest.raises(StaleState):
        await run(db_session, investigation)


async def test_the_command_lists_the_candidates(db_session, capsys):
    investigation = await mined(db_session)
    from tests.db.test_prescreening import QuickJudge  # imported here: test_prescreening imports this module

    # pre-screening follows (5.4); a quick check that rules everything out ends the run at triage
    assert await scout(sessions_on(db_session), investigation.id, model=FakeScout.proposing(eight),
                       quick_model=QuickJudge.judging(default="not_feasible")) == 0
    output = capsys.readouterr().out
    assert "8 candidates, 6 grounded" in output and "unknown labels ignored: 1" in output
    assert "1. Calibrating judges  [limitation; T1, ~8 weeks]" in output
    assert "SCREENED OUT: Grounding: 1 verified quote(s)" in output and "Pre-screening:" in output
    # everything ruled out, so nothing survived: one replenish round (5.5b) asks the Scout for more; those are
    # ruled out too, the round is used, and the router goes to ranking
    assert "Replenishing: too few ideas survived" in output and "More idea 1" in output
    stored = (await db_session.execute(select(Candidate).where(Candidate.investigation_id == investigation.id))
              ).scalars().all()
    assert len(stored) == 8 + 3 and sum(c.origin == "replenish" for c in stored) == 3
    assert (await db_session.get(Investigation, investigation.id)).stage == "results_ready"  # completed (5.6b)


async def test_a_failed_scout_stores_nothing(db_session, capsys):
    investigation = await mined(db_session)

    class Broken(FakeScout):
        async def generate_content_async(self, llm_request, stream=False):
            yield model_response("not json")

    assert await scout(sessions_on(db_session), investigation.id, model=Broken()) == 1
    assert "Nothing was stored" in capsys.readouterr().out
    stored = (await db_session.execute(select(Candidate).where(Candidate.investigation_id == investigation.id))
              ).scalars().all()
    assert stored == []
    assert (await db_session.get(Investigation, investigation.id)).stage == "generating_candidates"
