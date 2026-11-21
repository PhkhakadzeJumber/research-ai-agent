"""Where an investigation goes after triage (doc 05 §11.3, "How triage picks the next stage"), both workflows.

route_after_triage(sessions, investigation_id), at stage `triage`, after every evaluation just
finished has its TriageDecision. The first matching line decides the next stage:
  0. the cost budget is used up                                          -> ranking
  1. a candidate is `needs_refinement` and its lineage has refinements left -> refining
  2. Mode 1: the user's idea was rejected, no alternatives generated yet  -> generating_candidates
  3. Mode 2: finalists + advanced < requested count, a replenish round left -> generating_candidates
  4. a candidate is `advanced` (waiting for a full evaluation)            -> full_evaluation
  5. otherwise                                                           -> ranking
Before that, candidates in `needs_refinement` whose lineage is out of refinements (doc 05 §6:
2 per idea, 4 per investigation) become `rejected`, with the reason.

Lines 2 and 3 need the Scout's alternatives and replenish tasks (step 5.5b); until they exist
(TASKS_AVAILABLE) they are skipped, so the run goes on to the next line instead of stopping at a
stage nothing can carry out yet.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select

from src.ai.workflows.budget import budget_left
from src.ai.workflows.evaluation import Sessions
from src.ai.workflows.states import CandidateStatus, Mode, Stage
from src.ai.workflows.transitions import StaleState, advance_investigation, set_candidate_status
from src.db.models import Candidate, Investigation

MAX_REFINEMENTS_PER_LINEAGE = 2  # doc 05 §6
MAX_REFINEMENTS_PER_INVESTIGATION = 4  # doc 05 §6 (Mode 2; Mode 1 has one lineage, so 2 bind first)
TASKS_AVAILABLE = {"alternatives": False, "replenish": False}  # step 5.5b


@dataclass
class Route:
    stage: str
    reason: str
    task: str | None = None
    """For generating_candidates: alternatives or replenish."""
    out_of_refinements: list[Candidate] = field(default_factory=list)


def refinements_used(candidates: list[Candidate]) -> int:
    return sum(1 for c in candidates if c.origin == "refine")


def refinement_room(candidate: Candidate, used: int) -> str | None:
    """Why this candidate can't be refined again, or None if it can."""
    if candidate.version - 1 >= MAX_REFINEMENTS_PER_LINEAGE:
        return f"Refinement limit reached: this idea was refined {MAX_REFINEMENTS_PER_LINEAGE} times already."
    if used >= MAX_REFINEMENTS_PER_INVESTIGATION:
        return f"Refinement limit reached: {MAX_REFINEMENTS_PER_INVESTIGATION} refinements in this investigation."
    return None


async def route_after_triage(sessions: Sessions, investigation_id: uuid.UUID) -> Route:
    async with sessions() as session:
        investigation = await session.get(Investigation, investigation_id)
        if investigation is None or investigation.stage != Stage.TRIAGE:
            raise StaleState(f"Investigation {investigation_id} isn't at triage.")
        candidates = list((await session.execute(
            select(Candidate).where(Candidate.investigation_id == investigation_id)
            .order_by(Candidate.created_at))).scalars())
        by_status: dict[str, list[Candidate]] = {}
        for c in candidates:
            by_status.setdefault(c.status, []).append(c)

        # lineages out of refinements are rejected (planned refinements count against the investigation's limit)
        used = refinements_used(candidates)
        out, refinable = [], []
        for c in by_status.get(CandidateStatus.NEEDS_REFINEMENT, []):
            reason = refinement_room(c, used + len(refinable))
            if reason:
                await set_candidate_status(session, c.id, to=CandidateStatus.REJECTED,
                                           expected_from=CandidateStatus.NEEDS_REFINEMENT, reason=reason)
                out.append(c)
            else:
                refinable.append(c)

        count = int((investigation.input or {}).get("count") or 1)
        survivors = len(by_status.get(CandidateStatus.ADVANCED, [])) + len(by_status.get(CandidateStatus.FINALIST, []))
        user_idea = next((c for c in candidates if c.origin == "user" and c.version == 1), None)
        if not budget_left(investigation):
            route = Route(Stage.RANKING, "the budget is used up: finishing with what exists")
        elif refinable:
            route = Route(Stage.REFINING, f"{len(refinable)} candidate(s) to refine")
        elif (investigation.mode == Mode.EVALUATE and TASKS_AVAILABLE["alternatives"] and user_idea is not None
              and user_idea.status == CandidateStatus.REJECTED
              and not any(c.origin == "alternative" for c in candidates)):
            route = Route(Stage.GENERATING_CANDIDATES, "your idea was rejected: looking for alternatives",
                          task="alternatives")
        elif (investigation.mode == Mode.DISCOVER and TASKS_AVAILABLE["replenish"] and survivors < count
              and not any(c.origin == "replenish" for c in candidates)):
            route = Route(Stage.GENERATING_CANDIDATES, f"{survivors} of {count} ideas survived: replenishing",
                          task="replenish")
        elif by_status.get(CandidateStatus.ADVANCED):
            route = Route(Stage.FULL_EVALUATION,
                          f"{len(by_status[CandidateStatus.ADVANCED])} advanced candidate(s) to evaluate fully")
        else:
            route = Route(Stage.RANKING, "nothing left to evaluate")
        route.out_of_refinements = out
        await advance_investigation(session, investigation_id, to=route.stage, expected_from=Stage.TRIAGE)
    return route
