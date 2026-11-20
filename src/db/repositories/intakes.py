"""Storing and reading Intakes (doc 10 §3.2): A1's reading of a request, one per investigation."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Intake


async def store_intake(
    session: AsyncSession,
    *,
    investigation_id: uuid.UUID,
    mode: str,
    agent_reading: dict[str, Any],
    schema_version: str,
    prompt_version: str,
) -> Intake:
    """Stores the agent's reading unchanged. A second Intake for one investigation is refused (unique)."""
    intake = Intake(investigation_id=investigation_id, mode=mode, agent_reading=agent_reading,
                    schema_version=schema_version, prompt_version=prompt_version)
    session.add(intake)
    await session.flush()
    return intake


async def find_intake(session: AsyncSession, investigation_id: uuid.UUID) -> Intake | None:
    return (await session.execute(
        select(Intake).where(Intake.investigation_id == investigation_id)
    )).scalar_one_or_none()
