"""Applying investigation, candidate and evaluation state changes to the database (doc 05 §11.7).

Every change:
  1. locks the investigation row (two workers can't move it at once),
  2. checks the change against the state graph (src/ai/workflows/states.py),
  3. sets the new stage and the status that goes with it (doc 05 §11.1),
  4. flushes, without committing.

The caller writes the stage's outputs in the same session, and they commit together
(er-model §1.2): after a crash, the stored stage always has its outputs, so a restart
resumes at the last committed stage. Typical use:

    async with database.session() as session:          # one transaction
        investigation = await advance_investigation(session, inv_id, to="triage",
                                                    expected_from="full_evaluation")
        session.add(...)                               # the stage's outputs

Candidate statuses (set_candidate_status, step 4.3) and evaluation steps
(start_evaluation, advance_evaluation, end_evaluation, step 4.4) work the same way.
Progress events (doc 10 ProgressEvent) are emitted from here once that table exists.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.workflows.states import (
    FINAL_INVESTIGATION_STATUSES,
    CandidateStatus,
    EvaluationStatus,
    EvaluationStep,
    STAGE_STATUS,
    IllegalTransition,
    InvestigationStatus,
    Stage,
    check_candidate_status,
    check_stage,
    check_step,
)
from src.db.models import Candidate, Evaluation, Investigation
from src.db.repositories.evaluations import create_evaluation, lock_evaluation
from src.db.repositories.candidates import lock_candidate
from src.db.repositories.runs import lock_investigation
from src.timeutil import now


class InvestigationNotFound(LookupError):
    pass


class CandidateNotFound(LookupError):
    pass


class EvaluationNotFound(LookupError):
    pass


class StaleState(IllegalTransition):
    """The investigation isn't in the stage the caller expected: another worker moved it first."""


async def _locked(session: AsyncSession, investigation_id: uuid.UUID) -> Investigation:
    investigation = await lock_investigation(session, investigation_id)
    if investigation is None:
        raise InvestigationNotFound(f"No investigation {investigation_id}.")
    if investigation.status in FINAL_INVESTIGATION_STATUSES:
        raise IllegalTransition(f"investigation {investigation_id} is {investigation.status}: it can't change again")
    return investigation


async def start_investigation(session: AsyncSession, investigation_id: uuid.UUID) -> Investigation:
    """created -> running, at stage intake."""
    investigation = await _locked(session, investigation_id)
    if investigation.status != InvestigationStatus.CREATED:
        raise StaleState(f"investigation {investigation_id} was already started ({investigation.status})")
    investigation.status = InvestigationStatus.RUNNING
    await session.flush()
    return investigation


async def advance_investigation(
    session: AsyncSession,
    investigation_id: uuid.UUID,
    *,
    to: str,
    expected_from: str | None = None,
    budget_stop: bool = False,
) -> Investigation:
    """Moves the investigation to stage `to` if the state graph allows it.

    expected_from: the stage the caller believes it's in; if another worker moved the
        investigation in the meantime, StaleState is raised instead of moving it twice.
    budget_stop: a budget or the time limit ran out; also allows the early finish to
        `ranking` (doc 05 §11.6).
    Raises InvestigationNotFound, IllegalTransition (StaleState included).
    """
    investigation = await _locked(session, investigation_id)
    if investigation.status == InvestigationStatus.CREATED:
        raise IllegalTransition(f"investigation {investigation_id} hasn't been started")
    if expected_from is not None and investigation.stage != Stage(expected_from):
        raise StaleState(f"investigation {investigation_id} is at {investigation.stage}, not {expected_from}")
    target = check_stage(investigation.mode, investigation.stage, to, budget_stop=budget_stop)
    investigation.stage = target
    investigation.status = STAGE_STATUS[target]
    await session.flush()
    return investigation


async def cancel_investigation(session: AsyncSession, investigation_id: uuid.UUID) -> Investigation:
    """The user cancelled: status cancelled, the stage keeps its last value (doc 05 §11.6)."""
    return await _finish(session, investigation_id, InvestigationStatus.CANCELLED)


async def fail_investigation(session: AsyncSession, investigation_id: uuid.UUID) -> Investigation:
    """A mandatory stage failed: status failed, the stage keeps its last value (doc 05 §11.6)."""
    return await _finish(session, investigation_id, InvestigationStatus.FAILED)


async def _finish(session: AsyncSession, investigation_id: uuid.UUID, status: InvestigationStatus) -> Investigation:
    investigation = await _locked(session, investigation_id)
    if investigation.status == InvestigationStatus.COMPLETED:
        raise IllegalTransition(f"investigation {investigation_id} has completed results; it can't become {status}")
    investigation.status = status
    await session.flush()
    return investigation


async def set_candidate_status(
    session: AsyncSession,
    candidate_id: uuid.UUID,
    *,
    to: str,
    expected_from: str,
    reason: str | None = None,
) -> Candidate:
    """Moves a candidate to status `to` if it's still in `expected_from` and the graph allows it.

    expected_from is required: parallel workers (Mode 2 evaluations) must never both move
    one candidate (doc 10 §6.3). reason: the rejection reason, stored when `to` is rejected or screened_out.
    Raises CandidateNotFound, IllegalTransition (StaleState included).
    """
    candidate = await lock_candidate(session, candidate_id)
    if candidate is None:
        raise CandidateNotFound(f"No candidate {candidate_id}.")
    if candidate.status != CandidateStatus(expected_from):
        raise StaleState(f"candidate {candidate_id} is {candidate.status}, not {expected_from}")
    target = check_candidate_status(candidate.status, to)
    candidate.status = target
    if target in (CandidateStatus.REJECTED, CandidateStatus.SCREENED_OUT):
        candidate.rejection_reason = reason
    await session.flush()
    return candidate


# --- evaluations (doc 05 §11.4) -------------------------------------------------------------------------

async def start_evaluation(
    session: AsyncSession, candidate_id: uuid.UUID, *, depth: str, extends_id: uuid.UUID | None = None
) -> Evaluation:
    """A new evaluation pass at its first step: normalizing, or planning_queries when it extends a light one."""
    first = check_step(None, EvaluationStep.PLANNING_QUERIES if extends_id else EvaluationStep.NORMALIZING)
    return await create_evaluation(session, candidate_id=candidate_id, depth=depth, step=first,
                                   extends_id=extends_id)


async def _locked_evaluation(session: AsyncSession, evaluation_id: uuid.UUID) -> Evaluation:
    evaluation = await lock_evaluation(session, evaluation_id)
    if evaluation is None:
        raise EvaluationNotFound(f"No evaluation {evaluation_id}.")
    if evaluation.status != EvaluationStatus.RUNNING:
        raise IllegalTransition(f"evaluation {evaluation_id} has ended ({evaluation.status})")
    return evaluation


async def advance_evaluation(
    session: AsyncSession, evaluation_id: uuid.UUID, *, to: str, expected_from: str
) -> Evaluation:
    """Moves a running evaluation to step `to`. Reaching `done` ends it (status done, or partial
    if a step degraded it). Raises EvaluationNotFound, IllegalTransition (StaleState included)."""
    evaluation = await _locked_evaluation(session, evaluation_id)
    if evaluation.step != EvaluationStep(expected_from):
        raise StaleState(f"evaluation {evaluation_id} is at {evaluation.step}, not {expected_from}")
    evaluation.step = check_step(evaluation.step, to)
    if evaluation.step == EvaluationStep.DONE:
        degraded = bool((evaluation.counters or {}).get("degraded_steps"))
        evaluation.status = EvaluationStatus.PARTIAL if degraded else EvaluationStatus.DONE
        evaluation.finished_at = now()
    await session.flush()
    return evaluation


async def note_degraded(session: AsyncSession, evaluation_id: uuid.UUID, step: str, reason: str) -> Evaluation:
    """Records that a step failed and was worked around: the evaluation will end `partial`
    instead of stopping the investigation (doc 05 §11.4)."""
    evaluation = await _locked_evaluation(session, evaluation_id)
    counters = dict(evaluation.counters or {})
    counters["degraded_steps"] = [*counters.get("degraded_steps", []), {"step": step, "reason": reason}]
    evaluation.counters = counters
    await session.flush()
    return evaluation


async def end_evaluation(session: AsyncSession, evaluation_id: uuid.UUID, *, status: str = "failed") -> Evaluation:
    """Ends a running evaluation early (e.g. failed). The step keeps its last value."""
    if EvaluationStatus(status) == EvaluationStatus.RUNNING:
        raise IllegalTransition("an evaluation can't be ended as running")
    evaluation = await _locked_evaluation(session, evaluation_id)
    evaluation.status = EvaluationStatus(status)
    evaluation.finished_at = now()
    await session.flush()
    return evaluation
