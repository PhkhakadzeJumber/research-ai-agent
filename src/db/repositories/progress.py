"""Storing and reading progress events (src/db/models/progress.py; step 9c)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Investigation, ProgressEvent


async def append_event(session: AsyncSession, investigation_id: uuid.UUID, *, kind: str, message: str,
                       stage: str | None = None, step: str | None = None, data: dict[str, Any] | None = None,
                       candidate_id: uuid.UUID | None = None) -> ProgressEvent:
    """The next event of this investigation (seq = last + 1), with the investigation's cost at this moment.
    The investigation row is locked while the number is chosen, so two writers can't take the same one."""
    investigation = (await session.execute(select(Investigation).where(Investigation.id == investigation_id)
                                           .with_for_update())).scalar_one()
    last = (await session.execute(select(func.max(ProgressEvent.seq)).where(
        ProgressEvent.investigation_id == investigation_id))).scalar_one()
    event = ProgressEvent(investigation_id=investigation_id, seq=(last or 0) + 1, kind=kind, stage=stage, step=step,
                          message=message, data=data or {}, candidate_id=candidate_id,
                          cost_usd=Decimal(investigation.cost_usd or 0))
    session.add(event)
    await session.flush()
    return event


async def events_after(session: AsyncSession, investigation_id: uuid.UUID, after: int = 0,
                       limit: int = 500) -> list[ProgressEvent]:
    return list((await session.execute(
        select(ProgressEvent).where(ProgressEvent.investigation_id == investigation_id, ProgressEvent.seq > after)
        .order_by(ProgressEvent.seq).limit(limit))).scalars())


async def last_event(session: AsyncSession, investigation_id: uuid.UUID,
                     kind: str | None = None) -> ProgressEvent | None:
    query = select(ProgressEvent).where(ProgressEvent.investigation_id == investigation_id)
    if kind is not None:
        query = query.where(ProgressEvent.kind == kind)
    return (await session.execute(query.order_by(ProgressEvent.seq.desc()).limit(1))).scalar_one_or_none()
