"""Full evaluation of the best advanced candidates (stage `full_evaluation`, both workflows, step 5.6a).

full_evaluation_step(sessions, investigation_id):
  1. choosing (code): if no candidate is in `full_evaluating` yet, the best `advanced` ones by their light
     verdicts (the ranking rules, src/ai/evidence/ranking.py) move to `full_evaluating`: at most
     PER_MODE (Mode 2: 3, Mode 1: 2), and at most FULL_LIMIT full evaluations per investigation (doc 05 §6;
     Mode 1's own idea counts). The others stay `advanced` ("passed screening, not fully evaluated");
  2. the upgrade (doc 05 §1: "reuses everything already gathered"): the full evaluation extends the
     candidate's light one (extends_id) and starts at planning_queries. The light evaluation's searches
     and hits are copied in, with their screening scores, so screening only pays for new papers and the
     papers already read come from the library. Its Skeptic round is carried over, so the full evaluation
     runs at most MAX_ROUNDS["full"] - 1 more rounds. Planning, novelty and value are done afresh on the
     larger evidence;
  3. each evaluation runs through the shared runner, then triage: finalize -> `finalist`, refine or reject
     as usual. When all are done the stage moves to `triage` (the router decides what comes next).
Like light evaluation, it stops cleanly when the budget is used up or an evaluation fails, and resumes.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy import func, select

from src.ai.evidence.ranking import Standing, ranking_key
from src.ai.workflows.concluding import triage_candidate
from src.ai.workflows.evaluation import Sessions, begin_evaluation
from src.ai.workflows.routing import FULL_LIMIT, full_evaluations_done
from src.ai.workflows.runner import EvaluationModels, Report, run_evaluation
from src.ai.workflows.states import CandidateStatus, Stage
from src.ai.workflows.transitions import StaleState, advance_investigation, set_candidate_status, start_evaluation
from src.db.models import (
    Candidate,
    Claim,
    ClaimEvidence,
    Evaluation,
    Investigation,
    Quote,
    RetrievalHit,
    SearchQuery,
    TriageDecision,
    Verdict,
)

PER_MODE = {"discover": 3, "evaluate": 2}  # doc 05 §11.3 line 4: Mode 2 the best <=3, Mode 1 the best <=2


@dataclass
class FullOutcome:
    chosen: list[Candidate] = field(default_factory=list)
    """Moved to full_evaluating in this run (empty when resuming)."""
    triaged: list[tuple[Candidate, TriageDecision]] = field(default_factory=list)
    stopped: str | None = None
    stopped_at: str | None = None


async def standing_of(session, evaluation_id: uuid.UUID) -> Standing:
    """The evaluation's verdict categories and its verified supporting quotes (ranking, doc 05 §3)."""
    verdicts = {v.dimension: v.category for v in (await session.execute(
        select(Verdict).where(Verdict.evaluation_id == evaluation_id))).scalars()}
    quotes = (await session.execute(
        select(func.count(func.distinct(ClaimEvidence.quote_id))).select_from(ClaimEvidence)
        .join(Claim, Claim.id == ClaimEvidence.claim_id).join(Quote, Quote.id == ClaimEvidence.quote_id)
        .where(Claim.evaluation_id == evaluation_id, ClaimEvidence.stance == "supports", Quote.verified.is_(True))
    )).scalar_one()
    return Standing(verdicts.get("recommendation"), verdicts.get("novelty"), verdicts.get("feasibility"),
                    verdicts.get("significance"), quotes)


async def _latest(session, candidate_id: uuid.UUID, depth: str) -> Evaluation | None:
    return (await session.execute(
        select(Evaluation).where(Evaluation.candidate_id == candidate_id, Evaluation.depth == depth)
        .order_by(Evaluation.started_at.desc()).limit(1))).scalar_one_or_none()


async def choose_for_full(session, investigation: Investigation) -> list[Candidate]:
    advanced = list((await session.execute(select(Candidate).where(
        Candidate.investigation_id == investigation.id, Candidate.status == CandidateStatus.ADVANCED)
        .order_by(Candidate.created_at))).scalars())
    room = min(PER_MODE.get(investigation.mode, 2), FULL_LIMIT - await full_evaluations_done(session, investigation.id))
    if room <= 0 or not advanced:
        return []
    standings = {}
    for c in advanced:
        light = await _latest(session, c.id, "light")
        standings[c.id] = await standing_of(session, light.id) if light else Standing(None, None, None, None)
    chosen = sorted(advanced, key=lambda c: ranking_key(standings[c.id]))[:room]
    for c in chosen:
        await set_candidate_status(session, c.id, to=CandidateStatus.FULL_EVALUATING,
                                   expected_from=CandidateStatus.ADVANCED)
    return chosen


async def upgrade_evaluation(sessions: Sessions, candidate_id: uuid.UUID) -> uuid.UUID:
    """A full evaluation extending the candidate's light one, with its searches, hits and Skeptic round copied.
    Without a concluded light evaluation it is a fresh full evaluation."""
    async with sessions() as session:
        light = await _latest(session, candidate_id, "light")
        if light is None or light.status not in ("done", "partial"):
            light = None
        else:
            full = await start_evaluation(session, candidate_id, depth="full", extends_id=light.id)
            queries = list((await session.execute(
                select(SearchQuery).where(SearchQuery.evaluation_id == light.id))).scalars())
            new_ids = {}
            for q in queries:
                copy = SearchQuery(investigation_id=q.investigation_id, evaluation_id=full.id, origin=q.origin,
                                   query_type=q.query_type, round=q.round, source=q.source, query_text=q.query_text,
                                   filters=q.filters, result_count=q.result_count, latency_ms=q.latency_ms,
                                   error=q.error)
                session.add(copy)
                await session.flush()
                new_ids[q.id] = copy.id
            hits = list((await session.execute(
                select(RetrievalHit).where(RetrievalHit.evaluation_id == light.id))).scalars())
            session.add_all(RetrievalHit(
                investigation_id=h.investigation_id, evaluation_id=full.id, paper_id=h.paper_id,
                found_by_query_ids=[new_ids[q] for q in h.found_by_query_ids if q in new_ids],
                source_rank=h.source_rank, embed_score=h.embed_score, llm_score=h.llm_score, llm_reason=h.llm_reason,
                screened=h.screened, selected_for_reading=False) for h in hits)
            full.counters = {"normalizing": "skipped: extends the light evaluation",
                             "upgraded_from": str(light.id), "copied_hits": len(hits),
                             "skeptic_rounds": list((light.counters or {}).get("skeptic_rounds", []))}
            await session.flush()
            return full.id
    return await begin_evaluation(sessions, candidate_id, depth="full")


async def full_evaluation_step(
    sessions: Sessions,
    investigation_id: uuid.UUID,
    *,
    models: EvaluationModels | None = None,
    search_factory=None,
    report: Report | None = None,
    on_candidate: Callable[[Candidate, int, int], Awaitable[None]] | None = None,
) -> FullOutcome:
    outcome = FullOutcome()
    async with sessions() as session:
        investigation = await session.get(Investigation, investigation_id)
        if investigation is None or investigation.stage != Stage.FULL_EVALUATION:
            raise StaleState(f"Investigation {investigation_id} isn't at full_evaluation.")
        waiting = list((await session.execute(select(Candidate).where(
            Candidate.investigation_id == investigation_id, Candidate.status == CandidateStatus.FULL_EVALUATING)
            .order_by(Candidate.created_at))).scalars())
        if not waiting:
            outcome.chosen = await choose_for_full(session, investigation)
            waiting = outcome.chosen

    for number, candidate in enumerate(waiting, start=1):
        if on_candidate is not None:
            await on_candidate(candidate, number, len(waiting))
        async with sessions() as session:
            full = await _latest(session, candidate.id, "full")
        if full is None or full.status == "failed":
            evaluation_id = await upgrade_evaluation(sessions, candidate.id)
        else:
            evaluation_id = full.id
        result = await run_evaluation(sessions, evaluation_id, investigation_id, models=models,
                                      search_factory=search_factory, report=report)
        if result.ended not in ("done", "partial"):
            outcome.stopped, outcome.stopped_at = result.ended, f"{candidate.title}: {result.step}"
            return outcome
        async with sessions() as session:
            decision = await triage_candidate(session, candidate.id, evaluation_id,
                                              expected_from=CandidateStatus.FULL_EVALUATING)
            outcome.triaged.append((await session.get(Candidate, candidate.id), decision))

    async with sessions() as session:
        await advance_investigation(session, investigation_id, to=Stage.TRIAGE, expected_from=Stage.FULL_EVALUATION)
    return outcome
