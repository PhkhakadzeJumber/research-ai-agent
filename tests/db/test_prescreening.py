"""Tests for pre-screening (src/ai/workflows/prescreening.py, src/ai/agents/quick_feasibility.py).
Real test database (rolled back), fake Claude and fake sources."""

import json
import re
import uuid
from types import SimpleNamespace

import pytest
from google.adk.models.base_llm import BaseLlm
from pydantic import PrivateAttr
from sqlalchemy import select

from src.ai.workflows.prescreening import choose_for_light, overlap, prescreen_step
from src.ai.workflows.scouting import scouting_step
from src.ai.workflows.transitions import StaleState
from src.config import RoleLlmSettings
from src.db.models import Candidate, Investigation, LlmCall
from tests.ai.test_scout_schema import candidate
from tests.db.test_clarification_workflow import sessions_on
from tests.db.test_scouting import SCOUT_SETTINGS, FakeScout, mined
from tests.fakes import model_response

pytestmark = [pytest.mark.anyio, pytest.mark.db]

QUICK_SETTINGS = RoleLlmSettings(role="feasibility_quick", model="claude-opus-5-5", effort="medium",
                                 max_tokens=16000)
_CANDIDATE = re.compile(r'<candidate label="(C\d+)"[^>]*><title>([^<]*)</title>')


class QuickJudge(BaseLlm):
    """A fake quick feasibility check: {title: level} (others feasible); `conflicts`: {title: exclusion}."""

    model: str = "claude-opus-5-5"
    _levels: dict = PrivateAttr(default_factory=dict)
    _conflicts: dict = PrivateAttr(default_factory=dict)
    _skip: set = PrivateAttr(default_factory=set)
    _default: str = PrivateAttr(default="feasible")
    _requests: list = PrivateAttr(default_factory=list)

    @classmethod
    def judging(cls, levels=None, conflicts=None, skip=(), default="feasible"):
        llm = cls()
        llm._levels, llm._conflicts, llm._skip = dict(levels or {}), dict(conflicts or {}), set(skip)
        llm._default = default
        return llm

    @property
    def requests(self):
        return self._requests

    async def generate_content_async(self, llm_request, stream=False):
        text = "\n".join(p.text for c in llm_request.contents for p in c.parts if p.text)
        self._requests.append(text)
        checks = [{"candidate": label, "proposed_level": self._levels.get(title, self._default),
                   "blocking_requirements": ["a cluster"] if self._levels.get(title, self._default) == "not_feasible"
                   else [],
                   "exclusion_conflict": self._conflicts.get(title), "note": "n"}
                  for label, title in _CANDIDATE.findall(text) if title not in self._skip]
        yield model_response(json.dumps({"checks": checks}))


def facets_for(name, method="a method"):
    return [{"kind": "problem", "text": f"{name} problem", "is_key": True},
            {"kind": "method", "text": method, "is_key": False},
            {"kind": "data_setting", "text": f"{name} data", "is_key": False},
            {"kind": "evaluation", "text": f"{name} metric", "is_key": False},
            {"kind": "contribution", "text": f"{name} insight", "is_key": False}]


def fit(tier="T1", weeks=8):
    return {"compute_tier": tier, "data_source": "public", "timeline_weeks": weeks, "complexity_note": "fits"}


def varied(statements, items):
    """Eight grounded candidates built to hit every check."""
    q = list(statements.values())
    two = (q[0], q[1])
    three = (q[0], q[1], q[2])  # better grounded: 3 papers

    def idea(title, strategy, sources=two, **changes):
        changes.setdefault("facets", facets_for(title))
        return candidate(title, strategy, sources, **changes)

    return [
        idea("Judge calibration Georgian", "limitation", three),
        idea("Judge calibration in Georgian", "future_work",
             facets=facets_for("Judge calibration Georgian")),  # a near-duplicate of the first, less grounded
        idea("Tokenizer fertility gap", "limitation", fit=fit(weeks=20)),  # over the 10 available weeks
        idea("Judge scale ladder", "contradiction", fit=fit(tier="T2")),  # over the beginner ceiling (T1)
        idea("Cluster pretraining", "practical_problem"),  # the quick check says not feasible
        idea("Clinical triage agents", "practical_problem"),  # touches the 'medical' exclusion
        idea("Code-switching tool calls", "combination"),
        idea("Translated benchmark artifacts", "practical_problem"),
    ]


async def scouted(db_session, build=varied):
    """A Mode 2 investigation at prescreening (beginner, 10 weeks, no compute constraint, excludes medical)."""
    investigation = await mined(db_session)
    await scouting_step(sessions_on(db_session), investigation.id, settings=SCOUT_SETTINGS,
                        model=FakeScout.proposing(build))
    return investigation


def run(db_session, investigation, judge=None, max_light=None):
    judge = judge or QuickJudge.judging({"Cluster pretraining": "not_feasible", "Code-switching tool calls":
                                         "feasible_with_adjustments"},
                                        {"Clinical triage agents": "medical: clinical decision support"})
    return prescreen_step(sessions_on(db_session), investigation.id, max_light=max_light, settings=QUICK_SETTINGS,
                          model=judge)


# --- pure code ---------------------------------------------------------------------------------------------

def test_word_overlap():
    assert overlap({"judge", "calibration"}, {"judge", "calibration"}) == 1.0
    assert overlap({"judge", "calibration"}, {"tokenizer"}) == 0.0
    assert overlap(set(), {"a"}) == 0.0


def test_choosing_takes_each_strategy_in_turn():
    def c(title, strategy):
        return SimpleNamespace(id=uuid.uuid4(), title=title, origin_strategy=strategy)

    rows = [(c("L1", "limitation"), 4, "feasible"), (c("L2", "limitation"), 4, "feasible"),
            (c("L3", "limitation"), 3, "feasible"), (c("F1", "future_work"), 2, "feasible"),
            (c("P1", "practical_problem"), 5, "unclear")]
    assert [x.title for x in choose_for_light(rows, 3)] == ["L1", "F1", "P1"]  # varied before strongest
    assert [x.title for x in choose_for_light(rows, 5)] == ["L1", "F1", "P1", "L2", "L3"]


# --- the step ----------------------------------------------------------------------------------------------

async def test_every_check_and_the_choice(db_session):
    investigation = await scouted(db_session)
    outcome = await run(db_session, investigation, max_light=2)

    reasons = {c.title: reason for c, reason in outcome.screened_out}
    assert set(reasons) == {"Judge calibration in Georgian", "Tokenizer fertility gap", "Judge scale ladder",
                            "Cluster pretraining", "Clinical triage agents"}
    assert reasons["Judge calibration in Georgian"].startswith("Near-duplicate of 'Judge calibration Georgian'")
    assert reasons["Tokenizer fertility gap"] == "Needs about 20 weeks; 10 are available."
    assert reasons["Judge scale ladder"] == "Needs compute tier T2; the beginner level allows up to T1."
    assert reasons["Cluster pretraining"].startswith("Quick feasibility: not feasible") and "a cluster" in reasons[
        "Cluster pretraining"]
    assert reasons["Clinical triage agents"] == "Touches one of your exclusions: medical: clinical decision support"

    # 3 pass; 2 go to light evaluation, varied by strategy; the third waits in reserve
    assert [c.title for c in outcome.light] == ["Judge calibration Georgian", "Translated benchmark artifacts"]
    assert [c.title for c in outcome.reserve] == ["Code-switching tool calls"]  # with adjustments: ranked lower
    stored = {c.title: c for c in (await db_session.execute(
        select(Candidate).where(Candidate.investigation_id == investigation.id))).scalars()}
    for c in stored.values():
        await db_session.refresh(c)
    assert stored["Judge calibration Georgian"].status == "light_evaluating"
    assert stored["Judge calibration Georgian"].prescreen["decision"] == "light_evaluation"
    assert stored["Judge calibration Georgian"].prescreen["order"] == 1
    assert stored["Code-switching tool calls"].status == "proposed"
    assert stored["Code-switching tool calls"].prescreen["decision"] == "reserve"
    assert stored["Tokenizer fertility gap"].rejection_reason == "Needs about 20 weeks; 10 are available."
    checks = stored["Translated benchmark artifacts"].prescreen["checks"]
    assert checks["compute"] == {"tier": "T1", "ceiling": "T1", "over": False}
    assert checks["quick_feasibility"]["level"] == "feasible"
    assert "feasibility_quick@1" in checks["quick_feasibility"]["prompt_version"]

    calls = (await db_session.execute(select(LlmCall).where(LlmCall.investigation_id == investigation.id,
                                                            LlmCall.role == "feasibility_quick"))).scalars().all()
    assert len(calls) == 1  # one batched call
    await db_session.refresh(investigation)
    assert investigation.stage == "light_evaluation"


async def test_only_candidates_left_after_the_code_checks_are_sent_to_the_quick_check(db_session):
    investigation = await scouted(db_session)
    judge = QuickJudge.judging()
    await run(db_session, investigation, judge)
    packet = judge.requests[0]
    assert "<field_request>" in packet and "<exclusions>medical</exclusions>" in packet
    titles = [title for _, title in _CANDIDATE.findall(packet)]
    assert "Tokenizer fertility gap" not in titles and "Judge calibration in Georgian" not in titles
    assert len(titles) == 5 and 'compute_tier="T1" weeks="8"' in packet


async def test_a_stated_compute_constraint_is_left_to_the_quick_check(db_session):
    investigation = await scouted(db_session)
    investigation.constraints_snapshot = {**investigation.constraints_snapshot, "compute": "one GPU"}
    await db_session.flush()
    outcome = await run(db_session, investigation, QuickJudge.judging())
    assert "Judge scale ladder" not in {c.title for c, _ in outcome.screened_out}  # T2 passes the code check
    stored = (await db_session.execute(select(Candidate).where(Candidate.title == "Judge scale ladder"))).scalar_one()
    await db_session.refresh(stored)
    assert stored.prescreen["checks"]["compute"] == {"tier": "T2", "constraint": "one GPU",
                                                     "judged_by": "quick feasibility"}


async def test_the_light_cap_comes_from_the_budget_and_skipped_checks_count_as_unclear(db_session):
    investigation = await scouted(db_session)
    investigation.budget = {**(investigation.budget or {}), "light_evaluations": 1}
    await db_session.flush()
    outcome = await run(db_session, investigation, QuickJudge.judging(skip={"Judge calibration Georgian"}))
    assert len(outcome.light) == 1
    assert outcome.quick[(await db_session.execute(select(Candidate.id).where(
        Candidate.title == "Judge calibration Georgian"))).scalar_one()]["level"] == "unclear"
    assert outcome.light[0].title != "Judge calibration Georgian"  # unclear ranks below feasible


async def test_prescreening_needs_its_stage(db_session):
    investigation = await mined(db_session)  # at generating_candidates
    with pytest.raises(StaleState):
        await run(db_session, investigation)
    assert (await db_session.get(Investigation, investigation.id)).stage == "generating_candidates"


def test_the_light_evaluation_cap():
    from src.ai.workflows.budget import DEFAULT_LIGHT_EVALUATIONS, light_cap, set_light_cap

    investigation = Investigation(budget={"cost_usd": 12.0})
    assert light_cap(investigation) == DEFAULT_LIGHT_EVALUATIONS == 5
    set_light_cap(investigation, 1)
    assert investigation.budget == {"cost_usd": 12.0, "light_evaluations": 1}  # the cost cap is kept
    with pytest.raises(ValueError, match="1-8"):
        set_light_cap(investigation, 9)
