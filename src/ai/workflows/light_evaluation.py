"""Light evaluation of the pre-screened candidates (Mode 2, stage `light_evaluation`, step 5.4).

light_evaluation_step(sessions, investigation_id) takes the candidates in `light_evaluating`,
in their pre-screening order, one at a time:
  1. it starts (or resumes) a light evaluation: the same steps as Mode 1's full evaluation, at
     depth light (doc 05 §1: fewer searches, up to 12 papers read, one Skeptic round), through
     the shared runner (src/ai/workflows/runner.py). Papers already in the library, such as the
     landscape read in step 5.2a, cost nothing to read again;
  2. once the evaluation has concluded, triage (doc 05 §4) decides: rule 9 -> `advanced`,
     rules 2/4/5 -> `needs_refinement`, rules 1/3/6 -> `rejected` (rule 7 leaves it flagged).

It stops early, with everything kept, when the budget is used up before a paid step or an
evaluation fails; run it again (the command's --continue) to go on: a failed evaluation is
started afresh. When every candidate has been triaged, the stage moves to `triage`. Carrying
out refinements, replenishing and the full evaluation of the best candidates come with
steps 5.5-5.6.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from src.ai.evidence.verdicts import LABELS
from src.ai.workflows.concluding import triage_candidate
from src.ai.workflows.evaluation import Sessions, begin_evaluation
from src.ai.workflows.runner import EvaluationModels, Report, run_evaluation
from src.ai.workflows.states import CandidateStatus, Stage
from src.ai.workflows.transitions import StaleState, advance_investigation
from src.db.models import Candidate, Investigation, TriageDecision
from src.db.repositories.evaluations import latest_evaluation

LIGHT = "light"


@dataclass
class LightOutcome:
    triaged: list[tuple[Candidate, TriageDecision]] = field(default_factory=list)
    stopped: str | None = None
    """None when every candidate was triaged; otherwise budget / failed / retrieval_failed."""
    stopped_at: str | None = None
    """'<candidate title>: <step>' where it stopped."""


async def _waiting(sessions: Sessions, investigation_id: uuid.UUID) -> list[Candidate]:
    async with sessions() as session:
        candidates = list((await session.execute(
            select(Candidate).where(Candidate.investigation_id == investigation_id,
                                    Candidate.status == CandidateStatus.LIGHT_EVALUATING)
        )).scalars())
    return sorted(candidates, key=lambda c: ((c.prescreen or {}).get("order", 99), c.created_at))


async def light_evaluation_step(
    sessions: Sessions,
    investigation_id: uuid.UUID,
    *,
    models: EvaluationModels | None = None,
    search_factory=None,
    report: Report | None = None,
    on_candidate: Callable[[Candidate, int, int], Awaitable[None]] | None = None,
) -> LightOutcome:
    async with sessions() as session:
        investigation = await session.get(Investigation, investigation_id)
        if investigation is None or investigation.stage != Stage.LIGHT_EVALUATION:
            raise StaleState(f"Investigation {investigation_id} isn't at light_evaluation.")
    outcome = LightOutcome()
    waiting = await _waiting(sessions, investigation_id)
    for number, candidate in enumerate(waiting, start=1):
        if on_candidate is not None:
            await on_candidate(candidate, number, len(waiting))
        async with sessions() as session:
            evaluation = await latest_evaluation(session, candidate.id)
        if evaluation is None or evaluation.status == "failed":
            evaluation_id = await begin_evaluation(sessions, candidate.id, depth=LIGHT)
        else:
            evaluation_id = evaluation.id
        result = await run_evaluation(sessions, evaluation_id, investigation_id, models=models,
                                      search_factory=search_factory, report=report)
        if result.ended not in ("done", "partial"):
            outcome.stopped, outcome.stopped_at = result.ended, f"{candidate.title}: {result.step}"
            return outcome
        async with sessions() as session:
            decision = await triage_candidate(session, candidate.id, evaluation_id,
                                              expected_from=CandidateStatus.LIGHT_EVALUATING)
            outcome.triaged.append((await session.get(Candidate, candidate.id), decision))

    async with sessions() as session:
        await advance_investigation(session, investigation_id, to=Stage.TRIAGE, expected_from=Stage.LIGHT_EVALUATION)
    return outcome


def short_step_line(step: str, outcome: Any) -> str | None:
    """One line per finished step, for Mode 2's compact output."""
    if step == "planning_queries":
        return f"plan: {len(outcome.plan.queries)} searches"
    if step == "retrieving":
        return f"search: {outcome.unique_papers} unique papers"
    if step == "screening":
        return f"screening: {outcome.selected} selected to read"
    if step == "reading":
        return f"reading: {outcome.read_now} read, {outcome.reused} from the library, {outcome.failed} failed"
    if step == "assessing_novelty":
        return "novelty: assessed" if not outcome.error else f"novelty: {outcome.error}"
    if step == "skeptic_review":
        return "Skeptic: one round done"
    if step == "revising_novelty":
        return "novelty revised after the Skeptic"
    if step == "assessing_value_feasibility":
        return "value and feasibility: assessed"
    if step == "concluding":
        return (f"verdicts: novelty {LABELS[outcome.novelty.category]} | "
                f"significance {LABELS[outcome.significance.category]} | "
                f"feasibility {LABELS[outcome.feasibility.category]} | "
                f"recommendation {LABELS[outcome.recommendation.category]}")
    return None
