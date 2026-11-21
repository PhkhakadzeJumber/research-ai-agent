"""Workflow B's first stages (doc 05 §3, §11.3; step 5.1a):
intake -> understanding -> awaiting_clarification -> landscape_retrieval.

  understand_field()    starts a Mode 2 investigation and asks the Idea Analyst for the field
                        brief (4-8 subtopics with landscape searches); the brief is stored
                        unchanged as the Intake's agent reading. A failure fails the
                        investigation (understanding is mandatory, doc 05 §11.6).
  load_pending_brief()  the stored brief of an investigation waiting for clarification
  apply_brief_answers() if the user answered anything: one revise call (no new questions)
  remove_subtopics()    the user drops subtopics they don't want (no Claude call)
  confirm_brief()       one transaction: answers, edits and the final brief on the Intake,
                        the run's assumptions on the investigation, and the stage ->
                        landscape_retrieval. Exclusions are absolute: subtopics that still
                        touch an excluded topic are removed by code here.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from src.ai.agents.idea_analyst import analyse_field, revise_field
from src.ai.context import FieldRequest, QuestionAnswer
from src.ai.schemas.field_brief import FieldBriefOutput, violating_subtopics
from src.ai.workflows.clarification import Answer, NotWaitingForClarification
from src.ai.workflows.evaluation import Sessions
from src.ai.workflows.states import Mode, Stage
from src.ai.workflows.transitions import advance_investigation, fail_investigation, start_investigation
from src.config import RoleLlmSettings
from src.db.models import Investigation
from src.db.repositories.intakes import find_intake, store_intake
from src.db.repositories.llm_calls import record_llm_calls
from src.llm import LlmError
from src.timeutil import now

logger = logging.getLogger(__name__)


class NotADiscoveryInvestigation(ValueError):
    """The investigation isn't Mode 2, or has no field."""


def field_request(investigation: Investigation) -> FieldRequest:
    data = investigation.input or {}
    field = (data.get("field") or "").strip()
    if investigation.mode != Mode.DISCOVER or not field:
        raise NotADiscoveryInvestigation(f"Investigation {investigation.id} is not a Mode 2 request with a field.")
    constraints = tuple((str(k).replace("_", " "), str(v))
                        for k, v in (investigation.constraints_snapshot or {}).items() if v not in (None, "", []))
    return FieldRequest(field=field, complexity=investigation.complexity, interests=data.get("interests") or None,
                        technologies=data.get("technologies") or None,
                        exclusions=tuple(data.get("exclusions") or ()), domain=data.get("domain") or None,
                        constraints=constraints, count=int(data.get("count") or 3))


@dataclass(frozen=True)
class FieldOutcome:
    investigation_id: uuid.UUID
    brief: FieldBriefOutput | None = None
    problems: tuple[str, ...] = ()
    """Exclusion violations still in the brief (removed by code at confirmation)."""
    error: str | None = None


async def understand_field(sessions: Sessions, investigation_id: uuid.UUID, *,
                           settings: RoleLlmSettings | None = None, model: Any | None = None) -> FieldOutcome:
    async with sessions() as session:
        investigation = await start_investigation(session, investigation_id)
        request = field_request(investigation)
        await advance_investigation(session, investigation_id, to=Stage.UNDERSTANDING, expected_from=Stage.INTAKE)
    try:
        reading = await analyse_field(request, settings=settings, model=model)
    except LlmError as error:
        logger.warning("field brief failed for %s: %s", investigation_id, error)
        async with sessions() as session:
            await record_llm_calls(session, getattr(error, "records", []), investigation_id=investigation_id)
            await fail_investigation(session, investigation_id)
        return FieldOutcome(investigation_id, error=f"{type(error).__name__}: {error}")
    async with sessions() as session:
        await store_intake(session, investigation_id=investigation_id, mode=Mode.DISCOVER,
                           agent_reading=reading.output.model_dump(mode="json"),
                           schema_version=reading.schema_version, prompt_version=reading.prompt_version)
        await record_llm_calls(session, reading.records, investigation_id=investigation_id)
        await advance_investigation(session, investigation_id, to=Stage.AWAITING_CLARIFICATION,
                                    expected_from=Stage.UNDERSTANDING)
    return FieldOutcome(investigation_id, reading.output, tuple(reading.problems))


@dataclass(frozen=True)
class PendingBrief:
    investigation_id: uuid.UUID
    request: FieldRequest
    brief: FieldBriefOutput


async def load_pending_brief(sessions: Sessions, investigation_id: uuid.UUID) -> PendingBrief:
    async with sessions() as session:
        investigation = await session.get(Investigation, investigation_id)
        if investigation is None or investigation.stage != Stage.AWAITING_CLARIFICATION:
            raise NotWaitingForClarification(f"Investigation {investigation_id} isn't waiting for clarification.")
        intake = await find_intake(session, investigation_id)
        if intake is None or intake.confirmed_at is not None:
            raise NotWaitingForClarification(f"Investigation {investigation_id} has no unconfirmed brief.")
        return PendingBrief(investigation_id, field_request(investigation),
                            FieldBriefOutput.model_validate(intake.agent_reading))


@dataclass(frozen=True)
class BriefRevision:
    brief: FieldBriefOutput | None
    revised: bool = False
    error: str | None = None


async def apply_brief_answers(sessions: Sessions, pending: PendingBrief, answers: Sequence[Answer], *,
                              settings: RoleLlmSettings | None = None, model: Any | None = None) -> BriefRevision:
    answered = tuple(QuestionAnswer(a.question, a.answer) for a in answers if not a.skipped)
    if not answered:
        return BriefRevision(pending.brief)
    try:
        revision = await revise_field(pending.request, pending.brief, answered, settings=settings, model=model)
    except LlmError as error:
        async with sessions() as session:
            await record_llm_calls(session, getattr(error, "records", []), investigation_id=pending.investigation_id)
        return BriefRevision(None, error=f"{type(error).__name__}: {error}")
    async with sessions() as session:
        await record_llm_calls(session, revision.records, investigation_id=pending.investigation_id)
    return BriefRevision(revision.output, revised=True)


def remove_subtopics(brief: FieldBriefOutput, numbers: Sequence[int]) -> FieldBriefOutput:
    """The brief without the subtopics at these positions (1-based). At least one must remain."""
    unknown = [n for n in numbers if not 1 <= n <= len(brief.subtopics)]
    if unknown:
        raise ValueError(f"There is no subtopic number {unknown[0]}.")
    kept = [s for n, s in enumerate(brief.subtopics, start=1) if n not in set(numbers)]
    if not kept:
        raise ValueError("At least one subtopic must remain.")
    return brief.model_copy(update={"subtopics": kept})


async def confirm_brief(sessions: Sessions, investigation_id: uuid.UUID, *, final: FieldBriefOutput,
                        answers: Sequence[Answer] = (), removed: Sequence[str] = ()) -> FieldBriefOutput:
    """Saves the clarification and moves to landscape_retrieval. Returns the brief that will be searched."""
    async with sessions() as session:
        investigation = await advance_investigation(session, investigation_id, to=Stage.LANDSCAPE_RETRIEVAL,
                                                    expected_from=Stage.AWAITING_CLARIFICATION)
        intake = await find_intake(session, investigation_id)
        if intake is None or intake.confirmed_at is not None:
            raise NotWaitingForClarification(f"Investigation {investigation_id} has no unconfirmed brief.")
        request = field_request(investigation)
        violating = set(violating_subtopics(final, request.exclusions))
        if violating and len(violating) < len(final.subtopics):
            final = final.model_copy(update={"subtopics": [s for s in final.subtopics if s.name not in violating]})
        intake.answers = [asdict(answer) for answer in answers]
        intake.user_edits = {"removed_subtopics": list(removed), "removed_for_exclusions": sorted(violating),
                             "final_brief": final.model_dump(mode="json")}
        intake.confirmed_at = now()
        investigation.assumptions = [*final.assumptions, *(a.if_skipped for a in answers if a.skipped)]
    return final
