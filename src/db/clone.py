"""Copying a finished run (step pages, Phase C): to another account (scripts/clone_run.py) or to replay it live
without AI calls (src/api/replay.py).

A run is its investigation and every row that belongs to it, found through the foreign keys: the ideas and their
parts, evaluations, claims, challenges, verdicts, triage decisions, searches, retrieval hits, open problems, progress
events, AI call records and research packages. Every row gets a new id first, then all are inserted with every
reference to a copied row rewritten, also inside lists and JSON (a retrieval hit lists the searches that found it;
a progress event's data names its evaluation). Shared rows stay shared (papers, quotes, paper analyses); account
data is never copied.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Table, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import Base
from src.db.models import Investigation

NEVER = {"users", "user_sessions", "email_tokens", "credit_grants", "credit_holds", "payments", "papers", "chunks",
         "paper_analyses", "quotes", "api_cache", "investigations"}
"""Account data, the shared library, and the investigation itself (copied first, by hand)."""


def remap(value: Any, ids: dict[uuid.UUID, uuid.UUID]) -> Any:
    """`value` with every copied id replaced by its copy's (UUIDs, lists, and id strings inside JSON)."""
    if isinstance(value, uuid.UUID):
        return ids.get(value, value)
    if isinstance(value, list):
        return [remap(v, ids) for v in value]
    if isinstance(value, dict):
        return {k: remap(v, ids) for k, v in value.items()}
    if isinstance(value, str) and len(value) == 36:
        try:
            parsed = uuid.UUID(value)
        except ValueError:
            return value
        return str(ids[parsed]) if parsed in ids else value
    return value


def _owners(table: Table, copied: set[str]) -> list:
    """The table's foreign-key columns that point at copied tables: a row belongs to the run through them."""
    return [fk.parent for fk in table.foreign_keys if fk.column.table.name in copied]


async def clone_with_ids(session: AsyncSession, source: Investigation, user_id: uuid.UUID
                         ) -> tuple[uuid.UUID, dict[str, int], dict[uuid.UUID, uuid.UUID]]:
    """The copy's id, rows copied per table, and every original id -> its copy's."""
    ids: dict[uuid.UUID, uuid.UUID] = {source.id: uuid.uuid4()}
    investigations = Base.metadata.tables["investigations"]
    row = dict((await session.execute(select(investigations).where(investigations.c.id == source.id))).mappings().one())
    row = {k: remap(v, ids) for k, v in row.items()} | {"user_id": user_id}
    await session.execute(investigations.insert().values(**row))
    copied, counts, batches = {"investigations"}, {}, []
    # 1. find every row of the run and give each a new id (a row can point at a row of a table copied after it:
    #    a retrieval hit lists the searches that found it), then 2. insert them all, references rewritten
    for table in Base.metadata.sorted_tables:
        if table.name in NEVER:
            continue
        owners = _owners(table, copied)
        if not owners:
            continue
        known = list(ids)
        rows = [dict(r) for r in (await session.execute(select(table).where(
            or_(*[column.in_(known) for column in owners])))).mappings()]
        if "created_at" in table.c:
            rows.sort(key=lambda r: r["created_at"])  # parents before the rows that point at them (e.g. refinements)
        for r in rows:
            ids[r["id"]] = uuid.uuid4()
        batches.append((table, rows))
        copied.add(table.name)
        counts[table.name] = len(rows)
    for table, rows in batches:
        for r in rows:
            await session.execute(table.insert().values(**{k: remap(v, ids) for k, v in r.items()}))
    return ids[source.id], counts, ids


async def clone(session: AsyncSession, source: Investigation, user_id: uuid.UUID) -> tuple[uuid.UUID, dict[str, int]]:
    new_id, counts, _ = await clone_with_ids(session, source, user_id)
    return new_id, counts


