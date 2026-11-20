"""Creating, locking and finding evaluations (ER region 5)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Evaluation, Paper, RetrievalHit


async def create_evaluation(
    session: AsyncSession, *, candidate_id: uuid.UUID, depth: str, step: str, extends_id: uuid.UUID | None = None
) -> Evaluation:
    evaluation = Evaluation(candidate_id=candidate_id, depth=depth, step=step, extends_id=extends_id)
    session.add(evaluation)
    await session.flush()
    return evaluation


async def lock_evaluation(session: AsyncSession, evaluation_id: uuid.UUID) -> Evaluation | None:
    """The evaluation, locked until the transaction ends (step changes read it under this lock)."""
    return (await session.execute(
        select(Evaluation).where(Evaluation.id == evaluation_id)
        .with_for_update().execution_options(populate_existing=True)
    )).scalar_one_or_none()


async def latest_evaluation(session: AsyncSession, candidate_id: uuid.UUID) -> Evaluation | None:
    return (await session.execute(
        select(Evaluation).where(Evaluation.candidate_id == candidate_id)
        .order_by(Evaluation.started_at.desc()).limit(1)
    )).scalar_one_or_none()


async def count_hits(session: AsyncSession, evaluation_id: uuid.UUID) -> int:
    """Unique papers found in this evaluation's scope."""
    return (await session.execute(
        select(func.count()).select_from(RetrievalHit).where(RetrievalHit.evaluation_id == evaluation_id)
    )).scalar_one()


async def top_hits(session: AsyncSession, evaluation_id: uuid.UUID, *, limit: int = 10) -> list[tuple[Paper, RetrievalHit]]:
    """The evaluation's papers found by the most queries, then by best rank: a rough first ordering
    before screening (step 4.5) decides what is actually relevant."""
    rows = await session.execute(
        select(Paper, RetrievalHit).join(RetrievalHit, RetrievalHit.paper_id == Paper.id)
        .where(RetrievalHit.evaluation_id == evaluation_id)
        .order_by(func.cardinality(RetrievalHit.found_by_query_ids).desc(), RetrievalHit.source_rank.asc().nulls_last())
        .limit(limit)
    )
    return [(paper, hit) for paper, hit in rows.all()]
