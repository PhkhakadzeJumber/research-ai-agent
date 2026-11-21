"""Ranking the results of an investigation (stage `ranking`, doc 05 §3 and §11.1, step 5.6b), both workflows.

ranked_results(session, investigation_id) orders what the investigation produced, from stored data only:
  - finalists: ordered by the ranking rules (src/ai/evidence/ranking.py) on the verdicts of their latest
    full evaluation, each with the reason it comes before the next one;
  - passed screening, not fully evaluated: `advanced` candidates (light verdicts), in the same order;
  - the rest, with what happened and why: rejected, screened out, refined (superseded by a later version),
    waiting in reserve, and evaluations left open (e.g. rule 7, expand search, which isn't carried out yet).

The ranking is computed, never stored: it is a pure function of frozen verdicts, so it can't drift from them.
complete_results(sessions, investigation_id) moves `ranking -> reporting -> results_ready`; the report itself is
built on demand (scripts/report.py).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select

from src.ai.evidence.ranking import Standing, ranking_key, reasons
from src.ai.workflows.concluding import wrap_up
from src.ai.workflows.evaluation import Sessions
from src.ai.workflows.full_evaluation import standing_of
from src.ai.workflows.states import CandidateStatus
from src.db.models import Candidate, Evaluation

OTHER_ORDER = (CandidateStatus.FULL_EVALUATING, CandidateStatus.LIGHT_EVALUATING, CandidateStatus.NEEDS_REFINEMENT,
               CandidateStatus.PROPOSED, CandidateStatus.REFINED, CandidateStatus.REJECTED,
               CandidateStatus.SCREENED_OUT)
OTHER_NOTES = {
    CandidateStatus.FULL_EVALUATING: "flagged: its evaluation asked for an expanded search, which isn't carried out yet",
    CandidateStatus.LIGHT_EVALUATING: "its light evaluation didn't finish",
    CandidateStatus.NEEDS_REFINEMENT: "sent back for refinement; no refinement was carried out",
    CandidateStatus.PROPOSED: "waiting in reserve (never evaluated)",
}


@dataclass(frozen=True)
class Ranked:
    candidate: Candidate
    evaluation: Evaluation | None
    standing: Standing
    ahead_because: str | None = None
    """Why it comes before the next one in its list (None for the last)."""


@dataclass
class RankedResults:
    finalists: list[Ranked] = field(default_factory=list)
    advanced: list[Ranked] = field(default_factory=list)
    others: list[tuple[Candidate, str]] = field(default_factory=list)
    """(candidate, what happened and why)."""


async def _latest(session, candidate_id: uuid.UUID, depth: str) -> Evaluation | None:
    return (await session.execute(
        select(Evaluation).where(Evaluation.candidate_id == candidate_id, Evaluation.depth == depth,
                                 Evaluation.status.in_(("done", "partial")))
        .order_by(Evaluation.started_at.desc()).limit(1))).scalar_one_or_none()


async def _ordered(session, candidates: list[Candidate], depth: str) -> list[Ranked]:
    rows = []
    for c in candidates:
        evaluation = await _latest(session, c.id, depth)
        standing = await standing_of(session, evaluation.id) if evaluation else Standing(None, None, None, None)
        rows.append((c, evaluation, standing))
    rows.sort(key=lambda row: ranking_key(row[2]))
    return [Ranked(c, e, s, reasons(s, rows[n + 1][2]) if n + 1 < len(rows) else None)
            for n, (c, e, s) in enumerate(rows)]


async def ranked_results(session, investigation_id: uuid.UUID) -> RankedResults:
    candidates = list((await session.execute(select(Candidate).where(
        Candidate.investigation_id == investigation_id).order_by(Candidate.created_at))).scalars())
    by_status: dict[str, list[Candidate]] = {}
    for c in candidates:
        by_status.setdefault(c.status, []).append(c)
    results = RankedResults(
        finalists=await _ordered(session, by_status.get(CandidateStatus.FINALIST, []), "full"),
        advanced=await _ordered(session, by_status.get(CandidateStatus.ADVANCED, []), "light"),
    )
    later = {c.parent_id: c for c in candidates if c.parent_id}
    for status in OTHER_ORDER:
        for c in by_status.get(status, []):
            if status == CandidateStatus.REFINED and c.id in later:
                why = f"refined into v{later[c.id].version}: {later[c.id].change_note or later[c.id].title}"
            else:
                why = c.rejection_reason or OTHER_NOTES.get(status, status.replace("_", " "))
            results.others.append((c, why))
    return results


async def complete_results(sessions: Sessions, investigation_id: uuid.UUID) -> None:
    """ranking -> reporting -> results_ready: the ranking is computed and the report built on demand."""
    await wrap_up(sessions, investigation_id)
