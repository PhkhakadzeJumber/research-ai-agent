"""Health checks (doc 11 §3.3), outside /api/v1 and never behind login.

/health: the process is up (for "is it running?"). /ready: it can also reach the database (for a load balancer or
Cloud Run deciding whether to send it traffic). Neither touches Claude or any paid service.
"""

from __future__ import annotations

from fastapi import APIRouter

from src.api.deps import DatabaseDep
from src.api.errors import ApiError
from src.api.schemas.common import ApiModel, ErrorResponse

router = APIRouter(tags=["health"])


class HealthStatus(ApiModel):
    status: str


@router.get("/health", response_model=HealthStatus)
async def health() -> HealthStatus:
    return HealthStatus(status="ok")


@router.get("/ready", response_model=HealthStatus,
            responses={503: {"model": ErrorResponse, "description": "The database isn't reachable"}})
async def ready(database: DatabaseDep) -> HealthStatus:
    if not await database.ping():
        raise ApiError(503, "unavailable", "The database isn't reachable.")
    return HealthStatus(status="ready")
