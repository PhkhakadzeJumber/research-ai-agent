"""Tests for refining (src/ai/workflows/refining.py, the Scout's refine task) and the candidate loop
(scripts/candidate_loop.py). Real test database (rolled back), fake Claude and fake sources."""

import json

import pytest
from google.adk.models.base_llm import BaseLlm
from pydantic import PrivateAttr
from sqlalchemy import select

from scripts.candidate_loop import LoopModels, continue_at
from src.ai.workflows.concluding import conclude_step, finish_investigation
from src.ai.workflows.refining import refine_step
from src.ai.workflows.scouting import scouting_step
from src.ai.workflows.transitions import StaleState
from src.config import RoleLlmSettings
from src.ai.workflows.evaluation import selected_papers
from src.db.models import Candidate, CandidateInspiration, Evaluation, Investigation, LlmCall
from tests.db.test_clarification_workflow import sessions_on
from tests.db.test_concluding import at_concluding
from tests.db.test_evaluation_workflow import value_answer
from tests.db.test_light_evaluation import fake_models, search
from tests.db.test_prescreening import facets_for
from tests.db.test_scouting import SCOUT_SETTINGS, FakeScout, eight, mined
from tests.fakes import model_response

pytestmark = [pytest.mark.anyio, pytest.mark.db]

REFINE_SETTINGS = RoleLlmSettings(role="scout", model="claude-opus-5-5", effort="high", max_tokens=20000)


def refined(title="Smaller version", weeks=8, sources=()):
    return {"candidate": {
        "title": title, "research_question": "Does a 1B model still benefit?", "motivation": "P1 shows the gap.",
        "inspirations": [{"source": s, "role": "gap_hint", "note": "n"} for s in sources],
        "potential_contribution": "c", "facets": facets_for(title),
        "fit": {"compute_tier": "T1", "data_source": "public", "timeline_weeks": weeks, "complexity_note": "fits"},
        "risks": ["smaller effect"], "change_note": "One 1B model instead of three 7B models, to fit 10 weeks.",
        "addresses": "Rule 5: the timeline was 20 weeks",
    }}


class FakeRefiner(BaseLlm):
    """A fake Scout for the refine task: answers with `build(request_text)` (a dict), and keeps the requests."""

    model: str = "claude-opus-5-5"
    _build: object = PrivateAttr()
    _requests: list = PrivateAttr(default_factory=list)

    @classmethod
    def answering(cls, build):
        llm = cls()
        llm._build = build if callable(build) else (lambda text: build)
        return llm

    @property
    def requests(self):
        return self._requests

    async def generate_content_async(self, llm_request, stream=False):
        text = "\n".join(p.text for c in llm_request.contents for p in c.parts if p.text)
        self._requests.append(text)
        yield model_response(json.dumps(self._build(text)))


async def mode1_needs_refinement(db_session):
    """A Mode 1 investigation at refining: the user's idea needs 20 weeks of 10 (rule 5, with a simplification)."""
    investigation_id, evaluation_id = await at_concluding(
        db_session, value=value_answer(timeline=[("all", 20)], simplifications=["use one 1B model"]))
    await conclude_step(sessions_on(db_session), evaluation_id)
    await finish_investigation(sessions_on(db_session), investigation_id)
    # the fixture's novelty answer names no closest work; record one, so the Scout sees a paper (P1)
    evaluation = await db_session.get(Evaluation, evaluation_id)
    paper, _ = (await selected_papers(db_session, evaluation_id))[0]
    evaluation.novelty_assessment = {**(evaluation.novelty_assessment or {}), "closest_works": [
        {"paper_id": str(paper.id), "what_they_did": "RAG for English QA", "how_the_idea_differs": "Georgian",
         "minor_difference_only": False}]}
    await db_session.flush()
    idea = (await db_session.execute(select(Candidate).where(Candidate.investigation_id == investigation_id))
            ).scalar_one()
    return investigation_id, idea


def run(db_session, investigation_id, refiner):
    return refine_step(sessions_on(db_session), investigation_id, settings=REFINE_SETTINGS, model=refiner)


async def test_the_users_idea_gets_its_next_version(db_session):
    investigation_id, idea = await mode1_needs_refinement(db_session)
    refiner = FakeRefiner.answering(refined())
    outcome = await run(db_session, investigation_id, refiner)

    packet = refiner.requests[0]
    assert '<triage rule="5">' in packet and "<simplification>use one 1B model</simplification>" in packet
    assert "the timeline needs 20 weeks; 10 are available" in packet  # the code's blocking finding
    assert "<facet_matrix>" in packet and "<limits>" in packet
    assert '<work paper="P1" minor_difference_only="false">RAG for English QA -> Georgian</work>' in packet
    assert '<paper label="P1"' in packet
    (refinement,) = outcome.refinements
    child = refinement.child
    await db_session.refresh(child)
    await db_session.refresh(idea)
    assert (child.version, child.parent_id, child.root_id, child.origin) == (2, idea.id, idea.id, "refine")
    assert child.origin_strategy == "refinement" and child.status == "light_evaluating"
    assert child.change_note.startswith("One 1B model") and child.scout_notes["addresses"].startswith("Rule 5")
    assert '"' in child.motivation and "P1" not in child.motivation  # the paper label became a title
    assert child.prescreen["decision"] == "light_evaluation" and child.prescreen["refinement_of"] == str(idea.id)
    assert "grounding_quotes" not in child.prescreen["checks"]  # the user's own idea has no inspirations
    assert idea.status == "refined"
    calls = (await db_session.execute(select(LlmCall).where(LlmCall.investigation_id == investigation_id,
                                                            LlmCall.role == "scout"))).scalars().all()
    assert len(calls) == 1 and "scout_refine@1" in calls[0].prompt_version
    assert (await db_session.get(Investigation, investigation_id)).stage == "light_evaluation"


async def test_a_version_that_still_breaks_the_limits_is_screened_out(db_session):
    investigation_id, _ = await mode1_needs_refinement(db_session)
    outcome = await run(db_session, investigation_id, FakeRefiner.answering(refined(weeks=20)))
    (refinement,) = outcome.refinements
    assert not refinement.passed and refinement.reason == "Needs about 20 weeks; 10 are available."
    await db_session.refresh(refinement.child)
    assert refinement.child.status == "screened_out"


async def test_scout_lineages_keep_their_grounding(db_session):
    investigation = await mined(db_session)
    await scouting_step(sessions_on(db_session), investigation.id, settings=SCOUT_SETTINGS,
                        model=FakeScout.proposing(eight))
    by_title = {c.title: c for c in (await db_session.execute(
        select(Candidate).where(Candidate.investigation_id == investigation.id))).scalars()}
    for title in ("Calibrating judges", "Ghost"):  # two quotes + an item; one quote
        by_title[title].status = "needs_refinement"
    investigation.stage = "refining"
    await db_session.flush()

    def build(text):
        # the parent's statements are offered as Q labels; Q99 is unknown and ignored
        return refined("Calibrating judges v2" if "Calibrating judges" in text else "Ghost v2",
                       sources=("Q1", "Q99"))

    outcome = await run(db_session, investigation.id, FakeRefiner.answering(build))
    results = {r.child.title: r for r in outcome.refinements}
    assert results["Calibrating judges v2"].passed
    links = (await db_session.execute(select(CandidateInspiration).where(
        CandidateInspiration.candidate_id == results["Calibrating judges v2"].child.id))).scalars().all()
    assert sorted(link.kind for link in links) == ["opportunity_item", "quote", "quote"]  # inherited, Q1 not twice
    assert not results["Ghost v2"].passed
    assert results["Ghost v2"].reason.startswith("Grounding: 1 verified quote(s)")


async def test_refining_needs_its_stage(db_session):
    investigation = await mined(db_session)
    with pytest.raises(StaleState):
        await run(db_session, investigation.id, FakeRefiner.answering(refined()))


async def test_the_loop_refines_re_evaluates_and_routes_again(db_session, capsys):
    investigation_id, idea = await mode1_needs_refinement(db_session)
    models = LoopModels(fake_models(), scout_model=FakeRefiner.answering(refined()), search_factory=search)
    code = await continue_at(sessions_on(db_session), investigation_id, models, command="evaluate")
    output = capsys.readouterr().out

    assert code == 0
    assert "v1 -> v2: Smaller version" in output and "change: One 1B model" in output
    assert "Light evaluation 1/1: Smaller version (v2)" in output and "plan: 8 searches" in output
    assert "Triage after light evaluation:" in output and output.count("\nNext: ") == 1
    investigation = await db_session.get(Investigation, investigation_id)
    await db_session.refresh(investigation)
    assert investigation.stage in ("results_ready", "full_evaluation")  # after the router: done, or 5.6 is next
