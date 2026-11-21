"""Tests for the Novelty Investigator's revision after a Skeptic round, challenge resolution, the Skeptic
override and the round loop (src/ai/workflows/skeptic_round.py revision_step). Real test database
(rolled back), fake Claude and sources, on the real pipeline up to revising_novelty."""

import json

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from src.ai.schemas.novelty import NoveltyRevisionOutput
from src.ai.workflows.evaluation import novelty_step, novelty_verdict_for
from src.ai.workflows.skeptic_round import loop_next, revision_step
from src.db.models import Challenge, Claim, ClaimEvidence, Evaluation, FacetMatch
from tests.db.test_clarification_workflow import sessions_on
from tests.db.test_evaluation_workflow import (
    NOVELTY_SETTINGS,
    FakeSearch,
    novelty_answer,
    screening_answer,
    skeptic_queries_answer,
)
from tests.db.test_novelty_step import at_novelty
from tests.db.test_screening_reading import READING
from tests.db.test_skeptic_round import ATTACK, GAP, run_round
from tests.fakes import ScriptedLlm, answer

pytestmark = [pytest.mark.anyio, pytest.mark.db]

PRIOR_WORK = {"target_claim": "C1", "type": "prior_work_found", "argument": "New study A already did it",
              "evidence": [{"paper": "P1", "quote": "We study New study A"}], "same_on_facets": [1, 2, 3]}


def revision(responses=(), cells=(), proposed="potential_opportunity"):
    return answer(json.dumps({
        "facet_matches": [{"paper": p, "facet": f, "overlap": o, "quote": q, "rationale": "r"} for p, f, o, q in cells],
        "responses": list(responses), "proposed_verdict": proposed}))


def rebut(quote, paper="P1"):
    return {"challenge": "K1", "response": "rebut", "rebuttal": {
        "kind": "gap", "text": "the setting differs", "statement_type": "evidence", "inferred": False,
        "evidence": [{"paper": paper, "quote": quote, "stance": "supports"}]}}


async def at_revision(db_session, challenge=PRIOR_WORK):
    """revising_novelty after round 1, with one challenge (K1) against the gap claim (C1).
    P1 in the revision is 'Paper 0' (the only paper already in the matrix)."""
    _, evaluation_id = await at_novelty(db_session)
    await novelty_step(sessions_on(db_session), evaluation_id, settings=NOVELTY_SETTINGS,
                       model=ScriptedLlm.with_answers(novelty_answer([("P1", 4, "different", None)], claims=[GAP])))
    skeptic = ScriptedLlm.with_answers(skeptic_queries_answer(ATTACK, "a b c", "d e f", "g h i"),
                                       answer(json.dumps({"challenges": [challenge]})))
    await run_round(db_session, evaluation_id, FakeSearch({ATTACK: ["New study A"]}), skeptic,
                    screen=ScriptedLlm.with_answers(screening_answer(3)), reader=ScriptedLlm.with_answers(READING))
    return evaluation_id


async def revise(db_session, evaluation_id, *answers):
    return await revision_step(sessions_on(db_session), evaluation_id, settings=NOVELTY_SETTINGS,
                               model=ScriptedLlm.with_answers(*answers))


async def stored(db_session, evaluation_id):
    challenge = (await db_session.execute(select(Challenge).where(Challenge.evaluation_id == evaluation_id))
                 ).scalar_one()
    await db_session.refresh(challenge)
    target = await db_session.get(Claim, challenge.target_claim_id)
    await db_session.refresh(target)
    return challenge, target


# --- the shape --------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("responses", "message"), [
    ([{"challenge": "K1", "response": "rebut"}], "needs a claim with quoted evidence"),
    ([{**rebut("x"), "response": "accept"}], "has no rebuttal"),
    ([{"challenge": "K1", "response": "accept"}, {"challenge": "K1", "response": "accept"}], "one response"),
])
def test_malformed_revisions_are_refused(responses, message):
    with pytest.raises(ValidationError, match=message):
        NoveltyRevisionOutput.model_validate({"responses": responses, "proposed_verdict": "potential_opportunity"})


# --- resolving challenges ---------------------------------------------------------------------------------

async def test_an_accepted_challenge_refutes_its_claim(db_session):
    evaluation_id = await at_revision(db_session)
    outcome = await revise(db_session, evaluation_id, revision([{"challenge": "K1", "response": "accept"}]))
    challenge, target = await stored(db_session, evaluation_id)
    assert outcome.resolutions == {"K1": "accepted"}
    assert (challenge.resolution, target.status) == ("accepted", "refuted")


async def test_a_rebuttal_with_a_verified_quote_defends_its_claim(db_session):
    evaluation_id = await at_revision(db_session)
    outcome = await revise(db_session, evaluation_id, revision([rebut("We study Paper 0")]))
    challenge, target = await stored(db_session, evaluation_id)
    assert outcome.resolutions == {"K1": "rebutted"}
    assert (challenge.resolution, target.status) == ("rebutted", "defended")
    rebuttal = await db_session.get(Claim, challenge.rebuttal_claim_id)
    assert (rebuttal.status, rebuttal.author_agent) == ("verified", "novelty")
    assert len((await db_session.execute(select(ClaimEvidence).where(ClaimEvidence.claim_id == rebuttal.id)))
               .scalars().all()) == 1


async def test_an_opinion_without_a_verified_quote_leaves_the_challenge_open(db_session):
    evaluation_id = await at_revision(db_session)
    outcome = await revise(db_session, evaluation_id, revision([rebut("We study something else entirely")]))
    challenge, target = await stored(db_session, evaluation_id)
    assert outcome.resolutions == {"K1": "unresolved"}
    assert (challenge.resolution, target.status) == ("unresolved", "challenged")
    assert (await db_session.get(Claim, challenge.rebuttal_claim_id)).status == "unverified_quote"


async def test_an_unanswered_challenge_stays_unresolved(db_session):
    evaluation_id = await at_revision(db_session)
    outcome = await revise(db_session, evaluation_id, revision())
    assert outcome.resolutions == {"K1": "unresolved"}


async def test_the_skeptics_papers_get_matrix_cells_and_existing_cells_are_kept(db_session):
    evaluation_id = await at_revision(db_session)
    before = len((await db_session.execute(select(FacetMatch).where(FacetMatch.evaluation_id == evaluation_id)))
                 .scalars().all())
    outcome = await revise(db_session, evaluation_id, revision(cells=[
        ("P1", 4, "same", "We study Paper 0"),  # P1 facet 4 exists already: not overwritten
        ("P2", 1, "different", None), ("P2", 2, "similar", "We study")]))
    cells = (await db_session.execute(select(FacetMatch).where(FacetMatch.evaluation_id == evaluation_id))
             ).scalars().all()
    assert outcome.new_cells == 2 and len(cells) == before + 2
    assert [c.overlap for c in cells if c.paper_id == outcome.papers["P1"].id] == ["different"]


# --- the Skeptic override ---------------------------------------------------------------------------------

async def test_a_verified_same_on_every_key_facet_overrides_unless_rebutted_with_evidence(db_session):
    evaluation_id = await at_revision(db_session)
    await revise(db_session, evaluation_id, revision())  # the challenge stays unresolved
    verdict, _ = await novelty_verdict_for(db_session, evaluation_id)
    assert (verdict.category, verdict.rule) == ("previously_investigated", "N1S")
    assert verdict.rules_version == "novelty-rules-v2"


async def test_a_verified_rebuttal_stops_the_override(db_session):
    evaluation_id = await at_revision(db_session)
    await revise(db_session, evaluation_id, revision([rebut("We study Paper 0")]))
    verdict, _ = await novelty_verdict_for(db_session, evaluation_id)
    assert verdict.rule != "N1S"


async def test_no_override_when_a_key_facet_is_missing(db_session):
    evaluation_id = await at_revision(db_session, challenge={**PRIOR_WORK, "same_on_facets": [1, 2]})
    await revise(db_session, evaluation_id, revision())
    verdict, _ = await novelty_verdict_for(db_session, evaluation_id)
    assert verdict.rule != "N1S"


# --- the loop ---------------------------------------------------------------------------------------------

async def test_the_loop_goes_back_to_the_skeptic_until_three_rounds(db_session):
    evaluation_id = await at_revision(db_session)
    outcome = await revise(db_session, evaluation_id, revision([{"challenge": "K1", "response": "accept"}]))
    assert outcome.next_step == "skeptic_review"  # round 1 was not saturated, rounds left

    row = await db_session.get(Evaluation, evaluation_id)
    await db_session.refresh(row)
    unsaturated = {"round": 1, "saturated": False}
    row.counters = {**row.counters, "skeptic_rounds": [unsaturated, {**unsaturated, "round": 2},
                                                       {**unsaturated, "round": 3}]}
    assert loop_next(row) == "assessing_value_feasibility"  # three rounds done
    row.depth = "light"
    row.counters = {**row.counters, "skeptic_rounds": [unsaturated]}
    assert loop_next(row) == "assessing_value_feasibility"  # light: one round


async def test_a_failed_revision_degrades_and_keeps_the_challenges_open(db_session):
    evaluation_id = await at_revision(db_session)
    broken = answer("not json")
    outcome = await revise(db_session, evaluation_id, broken, broken)
    assert outcome.error.startswith("the Novelty Investigator's revision failed")
    challenge, _ = await stored(db_session, evaluation_id)
    assert challenge.resolution == "unresolved"
    row = await db_session.get(Evaluation, evaluation_id)
    await db_session.refresh(row)
    assert row.counters["degraded_steps"][-1]["step"] == "revising_novelty" and row.step == "skeptic_review"
