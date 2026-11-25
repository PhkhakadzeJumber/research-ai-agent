"""Local-only tools (EUREKA_DEV_REPLAY): replaying a finished run live, without AI calls (src/api/replay.py). When the
setting is off (always, in the cloud) these routes answer 404, as if they didn't exist."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from src.api.auth import CurrentUser
from src.api.deps import RunsDep, SessionDep
from src.api.errors import ApiError, not_found
from src.api.replay import NothingToReplay, start_replay
from src.api.routes.investigations import get_investigation
from src.api.schemas.common import ERRORS, ApiModel
from src.config import get_api_settings

router = APIRouter(prefix="/dev", tags=["dev"])


class ReplayStatus(ApiModel):
    enabled: bool


class ReplayStarted(ApiModel):
    investigation_id: uuid.UUID
    """The copy being replayed: open it to watch."""


@router.get("/replay", response_model=ReplayStatus)
async def replay_status(user: CurrentUser) -> ReplayStatus:
    return ReplayStatus(enabled=get_api_settings().dev_replay)


@router.post("/replay/{investigation_id}", status_code=202, response_model=ReplayStarted, responses=ERRORS)
async def replay(investigation_id: uuid.UUID, session: SessionDep, user: CurrentUser, runs: RunsDep) -> ReplayStarted:
    """Copies the finished run and plays it again, live: no AI call, nothing charged."""
    if not get_api_settings().dev_replay:
        raise not_found("Page")
    source = await get_investigation(session, investigation_id, user)
    try:
        new_id = await start_replay(session, runs, source, user)
    except NothingToReplay as error:
        raise ApiError(409, "not_finished", str(error)) from None
    return ReplayStarted(investigation_id=new_id)
