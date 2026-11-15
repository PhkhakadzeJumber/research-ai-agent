"""Creating investigations (ER region 1). Minimal until the orchestrator arrives (step 4)."""

from __future__ import annotations

import uuid
from typing import Any

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
