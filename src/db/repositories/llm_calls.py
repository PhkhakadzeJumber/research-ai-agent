"""Storing Claude call records (from src/llm.py) in the llm_calls table.

If the calls belong to an investigation, their estimated cost is also added to the
investigation's running total, which budget checks read (doc 05 §6).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Investigation, LlmCall
from src.llm import LlmCallRecord


async def record_llm_calls(
    session: AsyncSession,
    records: Sequence[LlmCallRecord],
    *,
    investigation_id: uuid.UUID | None = None,
    evaluation_id: uuid.UUID | None = None,
) -> list[LlmCall]:
    """Stores the records; adds their known costs to the investigation's total."""
    rows = [
        LlmCall(
            investigation_id=investigation_id, evaluation_id=evaluation_id, role=record.role, model=record.model,
            effort=record.effort, prompt_version=record.prompt_version, attempt=record.attempt,
            input_tokens=record.input_tokens, output_tokens=record.output_tokens,
            thinking_tokens=record.thinking_tokens, cache_read_tokens=record.cache_read_tokens,
            cache_creation_tokens=record.cache_creation_tokens, latency_ms=record.latency_ms,
            stop_reason=record.stop_reason, cost_usd=record.cost_usd,
        )
        for record in records
    ]
    session.add_all(rows)
    known_cost = sum((r.cost_usd for r in records if r.cost_usd is not None), Decimal(0))
    if investigation_id is not None and known_cost:
        await session.execute(
            update(Investigation)
            .where(Investigation.id == investigation_id)
            .values(cost_usd=Investigation.cost_usd + known_cost)
        )
    await session.flush()
    return rows
