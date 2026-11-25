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

from sqlalchemy import String, cast, select

from src.config import ConfigError
from src.db.clone import clone, remap  # noqa: F401  (remap: used by tests)
from src.db.models import Investigation
from src.db.repositories.users import find_user_by_email
from src.db.session import Database

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
