"""One Skeptic round (doc 05 §11.4 step `skeptic_review`; code-driven, step 4.7a).

skeptic_round_step(sessions, evaluation_id, search):
  1. the Skeptic plans 4-8 adversarial searches (it sees the candidate, the Novelty
     Investigator's claims and verdict, and the titles already examined);
  2. code runs them on every source (stored with origin `skeptic` and the round number);
  3. papers new to this evaluation are screened; the most relevant new ones (up to
     READ_PER_ROUND) are read into the library;
  4. the Skeptic writes challenges quoting those new papers; code verifies every quote and
     stores the challenges; the claims they target become `challenged`;
  5. the round's saturation statistic (new relevant papers) is recorded.

Next step: `revising_novelty` if there are challenges or new relevant papers for the
Novelty Investigator to answer (step 4.7b); otherwise the round found nothing new
(saturation) and the evaluation moves on to `assessing_value_feasibility`. A failed
Skeptic call degrades the step; the evaluation goes on without that round's challenges.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, or_, select

from src.ai.agents.skeptic import AttackPlan, CheckedChallenge, plan_attack, write_challenges
from src.ai.context import ChunkForReading, ClaimForSkeptic, PaperForNovelty
from src.ai.workflows.evaluation import (
    READ_THRESHOLD,
    Sessions,
    SearchesLike,
    at_step,
    candidate_for_planning,
    novelty_verdict_for,
    papers_for_novelty,
    score_unscreened,
)
from src.ai.workflows.reading import LibraryOutcome, read_into_library
from src.ai.workflows.states import EvaluationStep
from src.ai.workflows.transitions import advance_evaluation, note_degraded
from src.config import RoleLlmSettings
from src.db.models import Candidate, Challenge, Chunk, Claim, Paper, PaperAnalysis, RetrievalHit
from src.db.repositories.analyses import QuoteToStore, store_quotes
from src.db.repositories.llm_calls import record_llm_calls
from src.db.repositories.search_trail import save_search
from src.llm import LlmCallRecord, LlmError
from src.retrieval.records import SearchRequest

logger = logging.getLogger(__name__)

READ_PER_ROUND = 5
SKEPTIC_RESULTS_PER_SOURCE = 20


@dataclass
class RoundOutcome:
    round: int
    plan: AttackPlan | None = None
    searches: int = 0
    new_papers: int = 0
    new_relevant: int = 0
    read: list[tuple[Paper, LibraryOutcome]] = field(default_factory=list)
    challenges: list[CheckedChallenge] = field(default_factory=list)
    papers: dict[str, Paper] = field(default_factory=dict)
    """label (P1, ...) -> paper, as shown to the Skeptic for its challenges."""
    claims: dict[str, Claim] = field(default_factory=dict)
    """label (C1, ...) -> claim."""
    next_step: str = ""
    error: str | None = None

    @property
    def saturated(self) -> bool:
        return self.error is None and self.new_relevant == 0


def _found_count():
    return func.cardinality(RetrievalHit.found_by_query_ids)


async def _context(session, evaluation_id):
    evaluation = await at_step(session, evaluation_id, EvaluationStep.SKEPTIC_REVIEW)
    candidate = await session.get(Candidate, evaluation.candidate_id)
    view = await candidate_for_planning(session, candidate)
    claims = list((await session.execute(
        select(Claim).where(Claim.evaluation_id == evaluation_id, Claim.author_agent == "novelty")
        .order_by(Claim.created_at)
    )).scalars())
    labelled = {f"C{n}": claim for n, claim in enumerate(claims, start=1)}
    verdict, _ = await novelty_verdict_for(session, evaluation_id)
    examined = tuple(f"{paper.title} ({paper.year or 'n.d.'})"
                     for paper, _, _ in await papers_for_novelty(session, evaluation_id, limit=100))
    before = set((await session.execute(
        select(RetrievalHit.paper_id).where(RetrievalHit.evaluation_id == evaluation_id)
    )).scalars())
    round_no = len((evaluation.counters or {}).get("skeptic_rounds", [])) + 1
    return evaluation, candidate, view, labelled, verdict, examined, before, round_no


async def _finish_round(sessions, evaluation_id, investigation_id, outcome: RoundOutcome, records, stats):
    """Records costs and the round's statistic, and moves to the next step."""
    outcome.next_step = (EvaluationStep.REVISING_NOVELTY if (outcome.challenges or outcome.new_relevant)
                         else EvaluationStep.ASSESSING_VALUE_FEASIBILITY)
    async with sessions() as session:
        await record_llm_calls(session, records, investigation_id=investigation_id, evaluation_id=evaluation_id)
        if outcome.error:
            await note_degraded(session, evaluation_id, "skeptic_review", outcome.error)
        evaluation = await advance_evaluation(session, evaluation_id, to=outcome.next_step,
                                              expected_from=EvaluationStep.SKEPTIC_REVIEW)
        rounds = list((evaluation.counters or {}).get("skeptic_rounds", []))
        rounds.append({"round": outcome.round, **stats, "new_relevant": outcome.new_relevant,
                       "saturated": outcome.saturated})
        evaluation.counters = {**evaluation.counters, "skeptic_rounds": rounds}


async def skeptic_round_step(
    sessions: Sessions,
    evaluation_id: uuid.UUID,
    search: SearchesLike,
    *,
    read_limit: int = READ_PER_ROUND,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
    screen_settings: RoleLlmSettings | None = None,
    screen_model: Any | None = None,
    read_settings: RoleLlmSettings | None = None,
    read_model: Any | None = None,
) -> RoundOutcome:
    async with sessions() as session:
        evaluation, candidate, view, claims, verdict, examined, before, round_no = await _context(session,
                                                                                                    evaluation_id)
        investigation_id = candidate.investigation_id
    outcome = RoundOutcome(round=round_no, claims=claims)
    claim_views = tuple(ClaimForSkeptic(label, c.kind, c.text, c.status) for label, c in claims.items())
    verdict_text = f"{verdict.label} ({'; '.join(verdict.trace)})"
    records: list[LlmCallRecord] = []

    # 1. plan the attack
    try:
        outcome.plan = await plan_attack(view, claim_views, verdict_text, examined, settings=settings, model=model)
        records += outcome.plan.records
    except LlmError as error:
        outcome.error = f"the Skeptic failed to plan its searches ({type(error).__name__}: {error})"
        await _finish_round(sessions, evaluation_id, investigation_id, outcome, getattr(error, "records", []),
                            {"searches": 0, "new_papers": 0, "read": 0, "challenges": 0})
        return outcome

    # 2. run the searches
    queries = outcome.plan.output.queries
    results = await asyncio.gather(*(
        search.search(SearchRequest(query=query.text, limit=SKEPTIC_RESULTS_PER_SOURCE)) for query in queries))
    for query, result in zip(queries, results):
        async with sessions() as session:
            await save_search(session, investigation_id, result, origin="skeptic", evaluation_id=evaluation_id,
                              query_type=query.angle, round=round_no)
    outcome.searches = len(results)

    # 3. screen the new papers, read the most relevant
    await score_unscreened(sessions, evaluation_id, view=view, investigation_id=investigation_id,
                           settings=screen_settings, model=screen_model)
    async with sessions() as session:
        has_text = or_(Paper.abstract.is_not(None), select(Chunk.id).where(Chunk.paper_id == Paper.id).exists())
        new_hits = (await session.execute(
            select(RetrievalHit, Paper, has_text).join(Paper, Paper.id == RetrievalHit.paper_id)
            .where(RetrievalHit.evaluation_id == evaluation_id, RetrievalHit.paper_id.not_in(before))
            .order_by(RetrievalHit.llm_score.desc().nulls_last(), _found_count().desc(),
                      RetrievalHit.source_rank.asc().nulls_last())
        )).all()
        outcome.new_papers = len(new_hits)
        relevant = [(hit, paper) for hit, paper, readable in new_hits
                    if (hit.llm_score or 0) >= READ_THRESHOLD and readable]
        outcome.new_relevant = sum(1 for hit, _, _ in new_hits if (hit.llm_score or 0) >= READ_THRESHOLD)
        to_read = relevant[:read_limit]
        for hit, _ in to_read:
            hit.selected_for_reading = True
    read = await read_into_library(sessions, [paper.id for _, paper in to_read], investigation_id=investigation_id,
                                   evaluation_id=evaluation_id, settings=read_settings, model=read_model)
    outcome.read = [(paper, result) for (_, paper), result in zip(to_read, read)]

    # 4. challenges from the papers that were read
    readable = [paper for paper, result in outcome.read if not result.error]
    async with sessions() as session:
        views = []
        for n, paper in enumerate(readable, start=1):
            analysis = (await session.execute(
                select(PaperAnalysis).where(PaperAnalysis.paper_id == paper.id)
                .order_by(PaperAnalysis.created_at.desc()).limit(1)
            )).scalar_one_or_none()
            chunks = (await session.execute(select(Chunk).where(Chunk.paper_id == paper.id)
                                            .order_by(Chunk.position))).scalars().all()
            outcome.papers[f"P{n}"] = paper
            views.append(PaperForNovelty(f"P{n}", paper.title, paper.tier, paper.year, paper.abstract,
                                         tuple(ChunkForReading(str(c.id), c.section_title, c.text) for c in chunks),
                                         analysis.content if analysis else None))
    if views:
        try:
            reading = await write_challenges(view, claim_views, verdict_text, tuple(views), settings=settings,
                                             model=model)
            records += reading.records
            outcome.challenges = reading.challenges
            if reading.dropped:
                logger.info("evaluation %s: Skeptic named unknown labels: %s", evaluation_id, reading.dropped)
        except LlmError as error:
            records += getattr(error, "records", [])
            outcome.error = f"the Skeptic failed to write challenges ({type(error).__name__}: {error})"

    # 5. store the challenges
    async with sessions() as session:
        for challenge in outcome.challenges:
            quote_ids = []
            for n, (label, quote) in enumerate(challenge.evidence):
                stored = await store_quotes(session, outcome.papers[label].id, [QuoteToStore(
                    f"challenge:{n}", quote.text, quote.chunk_id, quote.verified, quote.score)])
                if stored[f"challenge:{n}"] is not None:
                    quote_ids.append(stored[f"challenge:{n}"].id)
            target = claims.get(challenge.target_claim) if challenge.target_claim else None
            session.add(Challenge(
                evaluation_id=evaluation_id, round=round_no, target_claim_id=target.id if target else None,
                type=challenge.type, argument=challenge.argument, statement_type=challenge.statement_type,
                evidence_quote_ids=list(dict.fromkeys(quote_ids)),
                new_paper_ids=[outcome.papers[label].id for label in challenge.papers],
                same_on_facets=challenge.same_on_facets,
            ))
            if target is not None:
                stored_claim = await session.get(Claim, target.id)
                stored_claim.status = "challenged"
        await session.flush()

    await _finish_round(sessions, evaluation_id, investigation_id, outcome, records, {
        "searches": outcome.searches, "new_papers": outcome.new_papers, "read": len(readable),
        "challenges": len(outcome.challenges)})
    return outcome
