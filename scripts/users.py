"""Accounts from the command line (step 9d).

Usage (from the repo root, venv active, database running):
    python -m scripts.users create you@example.com --name "Your Name"   asks for the password (not shown)
    python -m scripts.users claim you@example.com                       gives every investigation without an owner
                                                                         (command-line runs) to this account
    python -m scripts.users list                                        accounts and how many investigations each has

Runs started from the command line have no owner, so no one sees them on the website until they are claimed.
Free: no Claude calls. Exit code: 0 ok, 2 invalid input or an unavailable database.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import func, select

from src.api.auth import MIN_PASSWORD, hash_password
from src.config import ConfigError
from src.db.models import Investigation, User
from src.db.repositories.users import EmailTaken, claim_unowned, create_user, find_user_by_email
from src.db.session import Database


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m scripts.users", description="Manage Eureka accounts.")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create an account (asks for the password)")
    create.add_argument("email")
    create.add_argument("--name", default=None)
    claim = commands.add_parser("claim", help="give every investigation without an owner to this account")
    claim.add_argument("email")
    commands.add_parser("list", help="accounts and their investigations")
    return parser.parse_args(argv)


def ask_password(ask=getpass.getpass) -> str | None:
    password = ask("Password (at least 10 characters, not shown): ")
    if len(password) < MIN_PASSWORD:
        print(f"Error: the password needs at least {MIN_PASSWORD} characters.", file=sys.stderr)
        return None
    if ask("The same password again: ") != password:
        print("Error: the two passwords differ.", file=sys.stderr)
        return None
    return password


async def run(args: argparse.Namespace, database: Database, ask=getpass.getpass) -> int:
    async with database.session() as session:
        if args.command == "create":
            password = ask_password(ask)
            if password is None:
                return 2
            try:
                user = await create_user(session, email=args.email, name=args.name,
                                         password_hash=hash_password(password))
            except EmailTaken as error:
                print(f"Error: {error}", file=sys.stderr)
                return 2
            print(f"Created the account {user.email}.")
            return 0
        if args.command == "claim":
            user = await find_user_by_email(session, args.email)
            if user is None:
                print(f"Error: no account {args.email}: sign up on the website or use `create` first.",
                      file=sys.stderr)
                return 2
            count = await claim_unowned(session, user.id)
            print(f"{count} investigation(s) now belong to {user.email}.")
            return 0
        rows = (await session.execute(
            select(User.email, User.name, func.count(Investigation.id))
            .outerjoin(Investigation, Investigation.user_id == User.id).group_by(User.id).order_by(User.email))).all()
        unowned = (await session.execute(select(func.count()).select_from(Investigation).where(
            Investigation.user_id.is_(None)))).scalar_one()
        for email, name, count in rows:
            print(f"  {email}  {name or ''}  | {count} investigation(s)")
        hint = " (claim them with `claim <email>`)" if unowned else ""
        print(f"Without an owner: {unowned} investigation(s){hint}")
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
