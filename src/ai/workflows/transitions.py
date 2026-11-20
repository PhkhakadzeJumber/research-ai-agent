"""Applying investigation and candidate state changes to the database (doc 05 §11.7).

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

Candidate statuses work the same way (set_candidate_status, step 4.3). Evaluation steps
get their function when the Evaluation table arrives; check_step already exists.
Progress events (doc 10 ProgressEvent) are emitted from here once that table exists.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.workflows.states import (
    FINAL_INVESTIGATION_STATUSES,
    CandidateStatus,
    STAGE_STATUS,
    IllegalTransition,
    InvestigationStatus,
    Stage,
    check_candidate_status,
    check_stage,
)
from src.db.models import Candidate, Investigation
from src.db.repositories.candidates import lock_candidate
from src.db.repositories.runs import lock_investigation


class InvestigationNotFound(LookupError):
    pass


class CandidateNotFound(LookupError):
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
    one candidate (doc 10 §6.3). reason: the rejection reason, stored when `to` is rejected.
    Raises CandidateNotFound, IllegalTransition (StaleState included).
    """
    candidate = await lock_candidate(session, candidate_id)
    if candidate is None:
        raise CandidateNotFound(f"No candidate {candidate_id}.")
    if candidate.status != CandidateStatus(expected_from):
        raise StaleState(f"candidate {candidate_id} is {candidate.status}, not {expected_from}")
    target = check_candidate_status(candidate.status, to)
    candidate.status = target
    if target == CandidateStatus.REJECTED:
        candidate.rejection_reason = reason
    await session.flush()
    return candidate
