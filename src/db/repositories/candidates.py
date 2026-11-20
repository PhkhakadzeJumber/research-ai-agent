"""Creating, locking and reading candidates and their facets (ER region 4)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Candidate, Facet


@dataclass(frozen=True)
class FacetToStore:
    kind: str
    text: str
    is_key: bool
    stated_by_user: bool


async def create_root_candidate(
    session: AsyncSession,
    *,
    investigation_id: uuid.UUID,
    origin: str,
    title: str,
    research_question: str,
    facets: Sequence[FacetToStore],
    spawned_from_id: uuid.UUID | None = None,
) -> Candidate:
    """Version 1 of a new lineage (its own root), in status `proposed`, with its facets in order."""
    candidate_id = uuid.uuid4()
    candidate = Candidate(id=candidate_id, root_id=candidate_id, investigation_id=investigation_id, version=1,
                          origin=origin, title=title, research_question=research_question,
                          spawned_from_id=spawned_from_id)
    session.add(candidate)
    await session.flush()  # the candidate row must exist before its facets point at it
    session.add_all(
        Facet(candidate_id=candidate_id, position=position, kind=facet.kind, text=facet.text,
              is_key=facet.is_key, stated_by_user=facet.stated_by_user)
        for position, facet in enumerate(facets)
    )
    await session.flush()
    return candidate


async def lock_candidate(session: AsyncSession, candidate_id: uuid.UUID) -> Candidate | None:
    """The candidate, locked until the transaction ends (status changes read it under this lock)."""
    return (await session.execute(
        select(Candidate).where(Candidate.id == candidate_id)
        .with_for_update().execution_options(populate_existing=True)
    )).scalar_one_or_none()


async def candidate_facets(session: AsyncSession, candidate_id: uuid.UUID) -> list[Facet]:
    return list((await session.execute(
        select(Facet).where(Facet.candidate_id == candidate_id).order_by(Facet.position)
    )).scalars())


async def investigation_candidates(session: AsyncSession, investigation_id: uuid.UUID) -> list[Candidate]:
    return list((await session.execute(
        select(Candidate).where(Candidate.investigation_id == investigation_id).order_by(Candidate.created_at)
    )).scalars())
