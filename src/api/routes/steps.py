"""The step pages' data (Figma pages 3 and 4): the stepper, and one step's page. Read-only and free: everything
comes from what the run stored (src/api/steps.py)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from src.api.deps import RunsDep, SessionDep
from src.api.auth import CurrentUser
from src.api.errors import ApiError, not_found
from src.api.routes.investigations import get_investigation
from src.api.schemas.common import ERRORS
from src.api.schemas.steps import StepPage, Steps
from src.api.steps import step_page, steps_overview
from src.db.models import Candidate

router = APIRouter(prefix="/investigations", tags=["steps"])


@router.get("/{investigation_id}/steps", response_model=Steps, responses=ERRORS)
async def get_steps(investigation_id: uuid.UUID, session: SessionDep, user: CurrentUser, runs: RunsDep) -> Steps:
    """Every step of the mode, in order: its state (done, running, waiting, paused, failed, upcoming), how long it
    took and about how many credits it used."""
    investigation = await get_investigation(session, investigation_id, user)
    return await steps_overview(session, investigation, runs.state(investigation_id))


@router.get("/{investigation_id}/steps/{key}", response_model=StepPage, responses=ERRORS)
async def get_step(investigation_id: uuid.UUID, key: str, session: SessionDep, user: CurrentUser, runs: RunsDep,
                   candidate: uuid.UUID | None = Query(None, description="Mode 2: a finalist, for its own steps "
                                                                         "(search … verdict)")) -> StepPage:
    """One step's page: what the step does, what Eureka did in this run, its numbers, its log and its output."""
    investigation = await get_investigation(session, investigation_id, user)
    idea = None
    if candidate is not None:
        if investigation.mode != "discover":
            raise ApiError(422, "invalid_request", "candidate is for a Find me an idea run's finalists.")
        idea = await session.get(Candidate, candidate)
        if idea is None or idea.investigation_id != investigation.id:
            raise not_found("Idea")
    page = await step_page(session, investigation, runs.state(investigation_id), key, idea)
    if page is None:
        raise not_found("Step")
    return page
