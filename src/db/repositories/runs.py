"""Creating and locking investigations (ER region 1)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Investigation


async def create_investigation(
    session: AsyncSession,
    *,
    mode: str,
    complexity: str,
    input: dict[str, Any],
    user_id: uuid.UUID | None = None,
    constraints: dict[str, Any] | None = None,
) -> Investigation:
    """A new investigation in status `created`, stage `intake` (doc 05 §11)."""
    investigation = Investigation(
        mode=mode, complexity=complexity, input=input, user_id=user_id, constraints_snapshot=constraints or {}
    )
    session.add(investigation)
    await session.flush()
    return investigation


async def lock_investigation(session: AsyncSession, investigation_id: uuid.UUID) -> Investigation | None:
    """The investigation, locked (SELECT ... FOR UPDATE) until the transaction ends.

    State changes read the current stage under this lock, so two workers can't both
    move the same investigation from the same stage. populate_existing refreshes an
    already-loaded object with the locked row's values.
    """
    return (await session.execute(
        select(Investigation).where(Investigation.id == investigation_id)
        .with_for_update().execution_options(populate_existing=True)
    )).scalar_one_or_none()
