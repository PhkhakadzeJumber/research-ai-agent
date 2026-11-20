"""Workflow A's first stages: intake -> understanding -> awaiting_clarification (doc 05 §2, §11.2).

understand_idea(sessions, investigation_id):
  1. one transaction: start the investigation and move it to `understanding`
     (committed, so a progress view sees the stage while Claude works);
  2. the Idea Analyst reads the idea, outside any transaction (no row lock held during
     a call that takes seconds);
  3. one transaction: store the Intake + the LLM call records, and move to
     `awaiting_clarification`. They commit together (er-model §1.2).

If A1 fails (refusal, cut-off, invalid output, API error), `understanding` is a
mandatory stage, so the investigation is marked `failed` (doc 05 §11.6); any calls
that were made are still recorded, so their cost is counted.

Confirming, editing or skipping the questions comes with step 4.3 (it creates Candidate v1).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.agents.idea_analyst import analyse_idea
from src.ai.context import IdeaForAnalysis
from src.ai.schemas.structured_idea import StructuredIdeaOutput
from src.ai.workflows.states import Mode, Stage
from src.ai.workflows.transitions import advance_investigation, fail_investigation, start_investigation
from src.config import RoleLlmSettings
from src.db.models import Investigation
from src.db.repositories.intakes import store_intake
from src.db.repositories.llm_calls import record_llm_calls
from src.llm import LlmError

logger = logging.getLogger(__name__)

Sessions = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class NotAnIdeaInvestigation(ValueError):
    """understand_idea() was given an investigation that isn't Mode 1, or has no idea text."""


@dataclass(frozen=True)
class UnderstandingOutcome:
    investigation_id: uuid.UUID
    reading: StructuredIdeaOutput | None = None
    intake_id: uuid.UUID | None = None
    error: str | None = None


def idea_request(investigation: Investigation) -> IdeaForAnalysis:
    """The Idea Analyst's view of a Mode 1 investigation (its input and constraints snapshot)."""
    data = investigation.input or {}
    idea = (data.get("idea") or "").strip()
    if investigation.mode != Mode.EVALUATE or not idea:
        raise NotAnIdeaInvestigation(f"Investigation {investigation.id} is not a Mode 1 idea with text.")
    constraints = tuple(
        (str(name).replace("_", " "), str(value))
        for name, value in (investigation.constraints_snapshot or {}).items() if value not in (None, "", [])
    )
    return IdeaForAnalysis(idea=idea, complexity=investigation.complexity, field=data.get("field") or None,
                           seed_papers=tuple(data.get("seed_papers") or ()), constraints=constraints)


async def understand_idea(
    sessions: Sessions,
    investigation_id: uuid.UUID,
    *,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> UnderstandingOutcome:
    """Runs intake + understanding for a Mode 1 investigation in status `created`."""
    async with sessions() as session:
        investigation = await start_investigation(session, investigation_id)
        request = idea_request(investigation)
        await advance_investigation(session, investigation_id, to=Stage.UNDERSTANDING, expected_from=Stage.INTAKE)

    try:
        reading = await analyse_idea(request, settings=settings, model=model)
    except LlmError as error:
        logger.warning("idea_analyst failed for %s: %s", investigation_id, error)
        async with sessions() as session:
            await record_llm_calls(session, getattr(error, "records", []), investigation_id=investigation_id)
            await fail_investigation(session, investigation_id)
        return UnderstandingOutcome(investigation_id, error=f"{type(error).__name__}: {error}")

    async with sessions() as session:
        intake = await store_intake(
            session, investigation_id=investigation_id, mode=Mode.EVALUATE,
            agent_reading=reading.output.model_dump(mode="json"),
            schema_version=reading.schema_version, prompt_version=reading.prompt_version,
        )
        await record_llm_calls(session, reading.records, investigation_id=investigation_id)
        await advance_investigation(session, investigation_id, to=Stage.AWAITING_CLARIFICATION,
                                    expected_from=Stage.UNDERSTANDING)
        intake_id = intake.id  # read inside the transaction: safe with any session factory
    return UnderstandingOutcome(investigation_id, reading=reading.output, intake_id=intake_id)
