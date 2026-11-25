"""Copy a finished run to another account (step pages, Phase C): to show a real run on a demo or test account
without paying for a new one.

Usage (from the repo root, venv active, database running):
    python -m scripts.clone_run <investigation id or its first 8 characters> --to you@example.com

Copies the investigation and everything that belongs to it, with new ids: the ideas and their parts, evaluations,
claims, challenges, verdicts, triage decisions, searches, retrieval hits, open problems, progress events, AI call
records and research packages. A reference to any copied row is rewritten to the copy, also inside JSON (e.g. a
progress event's evaluation id). Shared rows stay shared (papers, quotes, paper analyses). Never copied: account
data (credits, payments, logins, email links). The user's uploaded document is copied too (it belongs to the run):
clone only to an account you control.
Free: no AI call. Exit code: 0 ok, 2 invalid input or an unavailable database.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from typing import Any

from sqlalchemy import String, Table, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import ConfigError
from src.db.base import Base
from src.db.models import Investigation
from src.db.repositories.users import find_user_by_email
from src.db.session import Database

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


async def clone(session: AsyncSession, source: Investigation, user_id: uuid.UUID) -> tuple[uuid.UUID, dict[str, int]]:
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
    return ids[source.id], counts


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m scripts.clone_run", description="Copy a run to another account.")
    parser.add_argument("investigation", help="its id, or the first characters of it")
    parser.add_argument("--to", required=True, help="the account's email")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace, database: Database) -> int:
    async with database.session() as session:
        user = await find_user_by_email(session, args.to)
        if user is None:
            print(f"Error: no account {args.to}.", file=sys.stderr)
            return 2
        matches = list((await session.execute(select(Investigation).where(
            cast(Investigation.id, String).like(f"{args.investigation.lower()}%")))).scalars())
        if len(matches) != 1:
            print(f"Error: {len(matches)} investigations match {args.investigation!r}; give more of the id.",
                  file=sys.stderr)
            return 2
        new_id, counts = await clone(session, matches[0], user.id)
        await session.commit()
        rows = sum(counts.values())
        print(f"Copied investigation {matches[0].id} to {user.email} as {new_id} ({rows} rows: "
              + ", ".join(f"{n} {t}" for t, n in counts.items() if n) + ").")
        return 0


async def main_async(args: argparse.Namespace) -> int:
    try:
        database = Database.from_settings()
    except ConfigError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    try:
        if not await database.ping():
            print("Error: the database isn't reachable: start it with `docker compose up -d db`.", file=sys.stderr)
            return 2
        return await run(args, database)
    finally:
        await database.dispose()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
