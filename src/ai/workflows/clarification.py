"""Workflow A's clarification checkpoint: awaiting_clarification -> full_evaluation (doc 02 §2.3, doc 05 §11.2).

The user answers A1's questions (or skips them), may edit facets, and confirms. Then
their idea becomes Candidate v1, the thing the rest of Workflow A evaluates.

  load_pending()    the stored reading of an investigation waiting for clarification
  apply_answers()   if the user answered anything: one A1 "revise" call that turns the
                    free-text answers into the corrected reading (its cost is recorded
                    right away, even if the user then quits)
  apply_edits()     the user's direct facet edits (no Claude call); an edited facet
                    counts as stated by the user
  confirm_idea()    one transaction: answers + edits + confirmation on the Intake, the
                    run's assumptions on the investigation, Candidate v1 + facets
                    (proposed -> full_evaluating), and the stage -> full_evaluation

The rule from step 4.2b: if a KEY facet of the final reading was proposed by A1 rather
than stated by the user, the user must confirm explicitly; skipping (--yes) is refused.
A1's original reading on the Intake is never changed, so it can be compared with the
user's corrections.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.agents.idea_analyst import revise_idea
from src.ai.context import IdeaForAnalysis, QuestionAnswer
from src.ai.schemas.structured_idea import StructuredIdeaOutput
from src.ai.workflows.states import CandidateStatus, IllegalTransition, Stage
from src.ai.workflows.transitions import advance_investigation, set_candidate_status
from src.ai.workflows.understanding import idea_request
from src.config import RoleLlmSettings
from src.db.models import Investigation
from src.db.repositories.candidates import FacetToStore, create_root_candidate
from src.db.repositories.intakes import find_intake
from src.db.repositories.llm_calls import record_llm_calls
from src.llm import LlmError
from src.timeutil import now

Sessions = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class NotWaitingForClarification(IllegalTransition):
    """The investigation isn't at the clarification checkpoint (or was already confirmed)."""


class ConfirmationRequired(ValueError):
    """Key facets were proposed by Eureka, so the user must confirm the reading explicitly."""


@dataclass(frozen=True)
class Answer:
    question: str
    if_skipped: str
    answer: str | None = None  # None: skipped

    @property
    def skipped(self) -> bool:
        return not (self.answer and self.answer.strip())


@dataclass(frozen=True)
class PendingClarification:
    investigation_id: uuid.UUID
    request: IdeaForAnalysis
    reading: StructuredIdeaOutput


@dataclass(frozen=True)
class RevisionOutcome:
    reading: StructuredIdeaOutput | None
    revised: bool = False
    error: str | None = None


def answers_for(reading: StructuredIdeaOutput, replies: Sequence[str | None]) -> list[Answer]:
    """Pairs A1's questions with the user's replies (None or blank = skipped)."""
    return [Answer(question=q.question, if_skipped=q.if_skipped, answer=(reply or "").strip() or None)
            for q, reply in zip(reading.clarifying_questions, replies, strict=True)]


async def load_pending(sessions: Sessions, investigation_id: uuid.UUID) -> PendingClarification:
    async with sessions() as session:
        investigation = await session.get(Investigation, investigation_id)
        if investigation is None or investigation.stage != Stage.AWAITING_CLARIFICATION:
            raise NotWaitingForClarification(f"Investigation {investigation_id} isn't waiting for clarification.")
        intake = await find_intake(session, investigation_id)
        if intake is None or intake.confirmed_at is not None:
            raise NotWaitingForClarification(f"Investigation {investigation_id} has no unconfirmed reading.")
        return PendingClarification(investigation_id, idea_request(investigation),
                                    StructuredIdeaOutput.model_validate(intake.agent_reading))


async def apply_answers(
    sessions: Sessions,
    pending: PendingClarification,
    answers: Sequence[Answer],
    *,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> RevisionOutcome:
    """The reading with the user's answers applied: one Claude call if anything was answered, else none."""
    answered = tuple(QuestionAnswer(a.question, a.answer) for a in answers if not a.skipped)
    if not answered:
        return RevisionOutcome(pending.reading)
    try:
        revision = await revise_idea(pending.request, pending.reading, answered, settings=settings, model=model)
    except LlmError as error:
        async with sessions() as session:
            await record_llm_calls(session, getattr(error, "records", []), investigation_id=pending.investigation_id)
        return RevisionOutcome(None, error=f"{type(error).__name__}: {error}")
    async with sessions() as session:
        await record_llm_calls(session, revision.records, investigation_id=pending.investigation_id)
    return RevisionOutcome(revision.output, revised=True)


def apply_edits(reading: StructuredIdeaOutput, edits: Mapping[int, str]) -> StructuredIdeaOutput:
    """Replaces facet texts by position (0-based). Edited facets count as stated by the user.
    Raises ValueError for an unknown position or an edit that breaks the facet rules."""
    if not edits:
        return reading
    data = reading.model_dump()
    for position, text in edits.items():
        if not 0 <= position < len(data["facets"]):
            raise ValueError(f"There is no facet number {position + 1}.")
        data["facets"][position] |= {"text": text.strip(), "stated_by_user": True}
    return StructuredIdeaOutput.model_validate(data)


async def confirm_idea(
    sessions: Sessions,
    investigation_id: uuid.UUID,
    *,
    final: StructuredIdeaOutput,
    answers: Sequence[Answer] = (),
    edits: Mapping[int, str] | None = None,
    explicit: bool,
) -> uuid.UUID:
    """Saves the clarification and creates Candidate v1; returns its ID.

    explicit: the user looked at the final reading and confirmed it. Required when a key
    facet was proposed by A1 (ConfirmationRequired otherwise).
    """
    proposed = final.proposed_key_facets()
    if proposed and not explicit:
        kinds = ", ".join(facet.kind for facet in proposed)
        raise ConfirmationRequired(
            f"Eureka proposed these key facets ({kinds}); the idea can't be evaluated until you confirm or edit them."
        )

    async with sessions() as session:
        investigation = await advance_investigation(  # locks the investigation first
            session, investigation_id, to=Stage.FULL_EVALUATION, expected_from=Stage.AWAITING_CLARIFICATION
        )
        intake = await find_intake(session, investigation_id)
        if intake is None or intake.confirmed_at is not None:
            raise NotWaitingForClarification(f"Investigation {investigation_id} has no unconfirmed reading.")

        intake.answers = [asdict(answer) for answer in answers]
        intake.user_edits = {
            "facet_edits": {str(position + 1): text for position, text in (edits or {}).items()},
            "final_reading": final.model_dump(mode="json"),
            "confirmed_explicitly": explicit,
        }
        intake.confirmed_at = now()
        # The run's assumptions: what the final reading assumes, plus each skipped question's default.
        investigation.assumptions = [*final.assumptions, *(a.if_skipped for a in answers if a.skipped)]

        candidate = await create_root_candidate(
            session, investigation_id=investigation_id, origin="user", title=final.title,
            research_question=final.research_question,
            facets=[FacetToStore(f.kind, f.text, f.is_key, f.stated_by_user) for f in final.facets],
        )
        await set_candidate_status(session, candidate.id, to=CandidateStatus.FULL_EVALUATING,
                                   expected_from=CandidateStatus.PROPOSED)
        return candidate.id
