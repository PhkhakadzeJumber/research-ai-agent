"""Tests for light evaluation (src/ai/workflows/light_evaluation.py, src/ai/workflows/runner.py) and its part of
scripts/discover.py. Real test database (rolled back), fake Claude and fake sources, on the real evaluation
pipeline at depth light."""

import json
from decimal import Decimal

import pytest
from google.adk.models.base_llm import BaseLlm
from pydantic import PrivateAttr
from sqlalchemy import select

from scripts.discover import evaluate_light
from src.ai.workflows.light_evaluation import light_evaluation_step
from src.ai.workflows.runner import EvaluationModels
from src.ai.workflows.transitions import StaleState
from src.db.models import Candidate, Evaluation, Investigation, TriageDecision
from tests.ai.test_query_plan import q
from tests.db.test_clarification_workflow import sessions_on
from tests.db.test_evaluation_workflow import (
    NOVELTY_SETTINGS,
    READ_SETTINGS,
    SCREEN_SETTINGS,
    SETTINGS,
    SKEPTIC_SETTINGS,
    VALUE_SETTINGS,
    FakeSearch,
    novelty_answer,
    skeptic_queries_answer,
    value_answer,
)
from tests.db.test_landscape_reading import TitleScreener
from tests.db.test_mining import TitleReader
from tests.db.test_prescreening import run as prescreen
from tests.db.test_prescreening import scouted
from tests.fakes import model_response

pytestmark = [pytest.mark.anyio, pytest.mark.db]

CORE = "judge calibration core"
LIGHT_PLAN = {"queries": [
    q(CORE, "core", 1, 2, 3),
    *(q(text, "terminology_variant", 1) for text in ("judge variant one", "judge variant two", "judge variant three")),
    q("judging without method one", "without_method", 1, 3), q("judging without method two", "without_method", 1, 3),
    q("judging metric only", "facet_only", 4), q("judging insight only", "facet_only", 5),
]}


class Always(BaseLlm):
    """A fake model that gives the same answer to every call, and counts them."""

    model: str = "claude-opus-5-5"
    _text: str = PrivateAttr(default="")
    _calls: int = PrivateAttr(default=0)

    @classmethod
    def answering(cls, text):
        llm = cls()
        llm._text = text
        return llm

    @property
    def calls(self):
        return self._calls

    async def generate_content_async(self, llm_request, stream=False):
        self._calls += 1
        yield model_response(self._text)


def fake_models():
    return EvaluationModels(
        settings=SETTINGS, model=Always.answering(json.dumps(LIGHT_PLAN)),
        screen_settings=SCREEN_SETTINGS,
        screen_model=TitleScreener.scoring({"Judges fail in Georgian": 3, "Judge drift in Swahili": 2}),
        read_settings=READ_SETTINGS, read_model=TitleReader(),
        novelty_settings=NOVELTY_SETTINGS, novelty_model=Always.answering(novelty_answer()[0]),
        skeptic_settings=SKEPTIC_SETTINGS, skeptic_model=Always.answering(skeptic_queries_answer()[0]),
        value_settings=VALUE_SETTINGS, value_model=Always.answering(value_answer()[0]))


def search():
    # the core search finds two landscape papers that step 5.2a already read
    return FakeSearch({CORE: ["Judges fail in Georgian", "Judge drift in Swahili"]})


async def prescreened(db_session):
    """A Mode 2 investigation at light_evaluation with two candidates in light_evaluating."""
    investigation = await scouted(db_session)
    await prescreen(db_session, investigation, max_light=2)  # the same choice as in test_prescreening
    return investigation


def run(db_session, investigation, models=None, **kwargs):
    return light_evaluation_step(sessions_on(db_session), investigation.id, models=models or fake_models(),
                                 search_factory=search, **kwargs)


async def test_each_candidate_is_evaluated_light_and_triaged(db_session):
    investigation = await prescreened(db_session)
    models = fake_models()
    steps = []

    async def report(step, phase, outcome):
        if phase == "done":
            steps.append((step, outcome))

    outcome = await run(db_session, investigation, models, report=report)

    assert outcome.stopped is None and len(outcome.triaged) == 2
    assert [c.title for c, _ in outcome.triaged] == ["Judge calibration Georgian", "Translated benchmark artifacts"]
    names = [step for step, _ in steps]
    assert names.count("planning_queries") == 2 and names.count("concluding") == 2
    assert names.count("skeptic_review") == 2  # one Skeptic round each (light)
    reading = [o for step, o in steps if step == "reading"][0]
    assert (reading.read_now, reading.reused) == (0, 2)  # the landscape's analyses, reused for free

    evaluations = (await db_session.execute(select(Evaluation).join(Candidate).where(
        Candidate.investigation_id == investigation.id))).scalars().all()
    assert len(evaluations) == 2 and {e.depth for e in evaluations} == {"light"}
    assert all(e.counters["normalizing"] == "skipped: the facets were written by the Scout" for e in evaluations)
    decisions = (await db_session.execute(select(TriageDecision).join(Candidate, Candidate.id == TriageDecision.candidate_id).where(
        Candidate.investigation_id == investigation.id))).scalars().all()
    assert len(decisions) == 2
    for candidate, decision in outcome.triaged:
        await db_session.refresh(candidate)
        expected = {"advance": "advanced", "refine": "needs_refinement", "reject": "rejected"}.get(
            decision.action, "light_evaluating")
        assert candidate.status == expected
    await db_session.refresh(investigation)
    assert investigation.stage == "triage"


async def test_the_budget_stops_it_and_it_resumes(db_session):
    investigation = await prescreened(db_session)
    investigation.budget = {**investigation.budget, "cost_usd": float(investigation.cost_usd or 0)}  # used up
    await db_session.flush()
    outcome = await run(db_session, investigation)
    assert (outcome.stopped, outcome.stopped_at) == ("budget", "Judge calibration Georgian: planning_queries")
    assert (await db_session.get(Investigation, investigation.id)).stage == "light_evaluation"

    investigation.budget = {**investigation.budget, "cost_usd": 12.0}
    await db_session.flush()
    outcome = await run(db_session, investigation)
    assert outcome.stopped is None and len(outcome.triaged) == 2
    evaluations = (await db_session.execute(select(Evaluation).join(Candidate).where(
        Candidate.investigation_id == investigation.id))).scalars().all()
    assert len(evaluations) == 2  # the stopped evaluation was resumed, not started again


async def test_light_evaluation_needs_its_stage(db_session):
    investigation = await scouted(db_session)  # at prescreening
    with pytest.raises(StaleState):
        await run(db_session, investigation)


async def test_the_command_shows_each_evaluation_and_the_triage(db_session, capsys):
    investigation = await prescreened(db_session)
    code = await evaluate_light(sessions_on(db_session), investigation.id, models=fake_models(),
                                search_factory=search)
    output = capsys.readouterr().out
    assert code == 0
    assert "Light evaluation 1/2: Judge calibration Georgian" in output
    assert "Light evaluation 2/2: Translated benchmark artifacts" in output
    assert "plan: 8 searches" in output and "reading: 0 read, 2 from the library, 0 failed" in output
    assert "verdicts: novelty" in output and "Triage after light evaluation:" in output
    # then the router (5.5a): nothing advanced or to refine here, so ranking, which comes with 5.6
    assert "Next: ranking (nothing left to evaluate)" in output and "step 5.6" in output


async def test_the_command_says_when_the_budget_stopped_it(db_session, capsys):
    investigation = await prescreened(db_session)
    investigation.budget = {**investigation.budget, "cost_usd": float(investigation.cost_usd or Decimal(0))}
    await db_session.flush()
    code = await evaluate_light(sessions_on(db_session), investigation.id, models=fake_models(),
                                search_factory=search)
    assert code == 3
    assert "Budget used up" in capsys.readouterr().out
