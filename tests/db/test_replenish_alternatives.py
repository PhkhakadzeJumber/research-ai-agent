"""Tests for replenish (Mode 2) and alternatives (Mode 1), step 5.5b: src/ai/workflows/scouting.py
replenish_step, src/ai/workflows/alternatives.py, src/ai/workflows/generating.py, and pre-screening's second pass.
Real test database (rolled back), fake Claude and fake sources."""

import json
import re
import uuid

import pytest
from google.adk.models.base_llm import BaseLlm
from pydantic import PrivateAttr
from sqlalchemy import select

from scripts.candidate_loop import LoopModels, continue_at
from src.ai.workflows.concluding import conclude_step, finish_investigation
from src.ai.workflows.evaluation import selected_papers
from src.ai.workflows.generating import generate_step
from src.ai.workflows.light_evaluation import light_evaluation_step
from src.ai.workflows.prescreening import prescreen_step
from src.ai.workflows.routing import route_after_triage
from src.db.models import Candidate, Investigation, PaperAnalysis, Quote
from src.db.models.library import quote_text_hash
from tests.ai.test_scout_schema import candidate
from tests.db.test_clarification_workflow import sessions_on
from tests.db.test_concluding import at_concluding
from tests.db.test_evaluation_workflow import value_answer
from tests.db.test_light_evaluation import fake_models, prescreened, search
from tests.db.test_prescreening import QUICK_SETTINGS, QuickJudge, facets_for
from tests.db.test_refining import REFINE_SETTINGS
from tests.db.test_scouting import FakeScout, eight
from tests.fakes import model_response

pytestmark = [pytest.mark.anyio, pytest.mark.db]

_Q = re.compile(r'<statement label="(Q\d+)"')


async def at_replenish(db_session, *, empty_reserve=False):
    """A Mode 2 investigation at generating_candidates for a replenish: 2 light evaluations done, few survived,
    'Code-switching tool calls' waiting in reserve (or screened out with empty_reserve)."""
    investigation = await prescreened(db_session)
    await light_evaluation_step(sessions_on(db_session), investigation.id, models=fake_models(),
                                search_factory=search)
    if empty_reserve:
        reserve = (await db_session.execute(select(Candidate).where(
            Candidate.investigation_id == investigation.id, Candidate.status == "proposed"))).scalars().all()
        for c in reserve:
            c.status, c.rejection_reason = "screened_out", "test: no reserve"
        await db_session.flush()
    route = await route_after_triage(sessions_on(db_session), investigation.id)
    assert (route.stage, route.task) == ("generating_candidates", "replenish")
    return investigation


async def test_the_reserve_goes_first_without_any_call(db_session):
    investigation = await at_replenish(db_session)
    # model=None: a Scout or quick-check call would reach the real API, which the test safety net refuses
    task, outcome = await generate_step(sessions_on(db_session), investigation.id)
    assert (task, outcome.from_reserve, outcome.candidates) == ("replenish", 1, [])
    pre = await prescreen_step(sessions_on(db_session), investigation.id)
    assert pre.reused_quick == 1 and [c.title for c in pre.light] == ["Code-switching tool calls"]
    assert pre.room == 3  # the default cap of 5, minus the 2 light evaluations done
    await db_session.refresh(investigation)
    assert investigation.stage == "light_evaluation"


async def test_with_an_empty_reserve_the_scout_replenishes_once(db_session):
    investigation = await at_replenish(db_session, empty_reserve=True)

    def again_and_new(statements, items):
        first, second = list(statements.values())[:2]
        return [candidate("Judge calibration Georgian", "limitation", (first, second),
                          facets=facets_for("Judge calibration Georgian")),  # already evaluated
                candidate("Tokenizer audit", "future_work", (first, second), facets=facets_for("Tokenizer audit")),
                candidate("Dialect robustness", "practical_problem", (first, second),
                          facets=facets_for("Dialect robustness"))]

    scout = FakeScout.proposing(eight, replenish=again_and_new)
    task, outcome = await generate_step(sessions_on(db_session), investigation.id, settings=REFINE_SETTINGS,
                                        model=scout)
    assert task == "replenish" and len(outcome.candidates) == 3
    assert {c.origin for c in outcome.candidates} == {"replenish"}
    packet = scout.requests[0]
    assert "<already_tried>" in packet and 'status="screened out">Cluster pretraining: Quick feasibility' in packet

    pre = await prescreen_step(sessions_on(db_session), investigation.id, settings=QUICK_SETTINGS,
                               model=QuickJudge.judging())
    reasons = {c.title: reason for c, reason in pre.screened_out}
    assert reasons["Judge calibration Georgian"].startswith("Near-duplicate of 'Judge calibration Georgian'")
    assert {c.title for c in pre.light} == {"Tokenizer audit", "Dialect robustness"}


async def mode1_rejected(db_session):
    """A Mode 1 investigation whose idea was rejected (20 weeks of 10, no simplification: rule 1), at
    generating_candidates for alternatives. Two verified future-work statements are added to a read paper."""
    investigation_id, evaluation_id = await at_concluding(db_session, value=value_answer(timeline=[("all", 20)]))
    await conclude_step(sessions_on(db_session), evaluation_id)
    outcome = await finish_investigation(sessions_on(db_session), investigation_id)
    assert (outcome.decision.rule_number, outcome.route.task) == (1, "alternatives")
    paper, _ = (await selected_papers(db_session, evaluation_id))[0]
    analysis = (await db_session.execute(select(PaperAnalysis).where(PaperAnalysis.paper_id == paper.id))
                ).scalars().first()
    future = []
    for text in ("Future work should test smaller models.", "Other low-resource languages remain open."):
        quote = Quote(id=uuid.uuid4(), paper_id=paper.id, chunk_id=None, text=text, text_hash=quote_text_hash(text),
                      source_level="abstract", verified=True, verification_score=1.0)
        db_session.add(quote)
        future.append({"statement": text, "quote": {"quote_id": str(quote.id), "verified": True}})
    analysis.content = {**analysis.content, "future_work": future}
    await db_session.flush()
    return investigation_id


class FakeAlternatives(BaseLlm):
    """A fake Scout for the alternatives task: three ideas grounded in the first two statements offered."""

    model: str = "claude-opus-5-5"
    _requests: list = PrivateAttr(default_factory=list)

    @property
    def requests(self):
        return self._requests

    async def generate_content_async(self, llm_request, stream=False):
        text = "\n".join(p.text for c in llm_request.contents for p in c.parts if p.text)
        self._requests.append(text)
        labels = _Q.findall(text)[:2]
        yield model_response(json.dumps({"candidates": [
            candidate(title, "future_work", labels, facets=facets_for(title))
            for title in ("Smaller models, same language", "Another low-resource language", "A one-model study")]}))


async def test_a_rejected_idea_gets_grounded_alternatives(db_session):
    investigation_id = await mode1_rejected(db_session)
    fake = FakeAlternatives()
    task, outcome = await generate_step(sessions_on(db_session), investigation_id, settings=REFINE_SETTINGS,
                                        model=fake)
    packet = fake.requests[0]
    assert task == "alternatives" and '<triage rule="1">' in packet
    assert "Future work should test smaller models." in packet  # the evaluation's read papers' statements
    idea = (await db_session.execute(select(Candidate).where(Candidate.investigation_id == investigation_id,
                                                             Candidate.origin == "user"))).scalar_one()
    assert len(outcome.candidates) == 3 and not outcome.screened_out  # two verified quotes each
    for alternative in outcome.candidates:
        assert (alternative.origin, alternative.spawned_from_id, alternative.version) == ("alternative", idea.id, 1)
    assert (await db_session.get(Investigation, investigation_id)).stage == "prescreening"

    # Mode 1 pre-screening works from the user's own request
    judge = QuickJudge.judging()
    pre = await prescreen_step(sessions_on(db_session), investigation_id, settings=QUICK_SETTINGS, model=judge)
    assert "<field_request>" in judge.requests[0] and len(pre.light) == 3


async def test_the_loop_offers_alternatives_and_finishes(db_session, capsys):
    investigation_id = await mode1_rejected(db_session)
    models = LoopModels(fake_models(), scout_model=FakeAlternatives(), search_factory=search,
                        quick_model=QuickJudge.judging(default="not_feasible"))
    code = await continue_at(sessions_on(db_session), investigation_id, models, command="evaluate")
    output = capsys.readouterr().out
    assert code == 0
    assert "Looking for alternatives to your idea" in output and "3 candidates, 3 grounded" in output
    assert "Pre-screening:" in output and "3 screened out" in output
    assert "Next: ranking (nothing left to evaluate)" in output and "Done. Total cost:" in output
    investigation = await db_session.get(Investigation, investigation_id)
    await db_session.refresh(investigation)
    assert investigation.stage == "results_ready"
