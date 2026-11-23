"""Accounts from the command line (step 9d).

Usage (from the repo root, venv active, database running):
    python -m scripts.users create you@example.com --name "Your Name"   asks for the password (not shown)
    python -m scripts.users claim you@example.com                       gives every investigation without an owner
                                                                         (command-line runs) to this account
    python -m scripts.users list                                        accounts, their plan and investigations
    python -m scripts.users grant-pro you@example.com [--months 12]     Pro for N months (default 1), extending a
                                                                         current Pro period; until payments (step P5)
    python -m scripts.users end-pro you@example.com                     back to the free plan (credits are kept)
    python -m scripts.users grant-credits you@example.com 50 [--note "why"] [--months 12]
                                                                         extra credits, valid N months (default 12)
    python -m scripts.users credits you@example.com                     plan, credits, and what each run cost us

Runs started from the command line have no owner, so no one sees them on the website until they are claimed.
Free: no Claude calls. Plans and credits: docs/18. Exit code: 0 ok, 2 invalid input or an unavailable database.
"""

from __future__ import annotations

import argparse
import asyncio
import calendar
import getpass
import sys
from datetime import datetime

from sqlalchemy import func, select

from src.api import billing
from src.api.auth import MIN_PASSWORD, hash_password
from src.config import ConfigError
from src.db.models import CreditHold, Investigation, User
from src.db.repositories.users import EmailTaken, claim_unowned, create_user, find_user_by_email
from src.db.session import Database
from src.timeutil import now


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m scripts.users", description="Manage Eureka accounts.")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create an account (asks for the password)")
    create.add_argument("email")
    create.add_argument("--name", default=None)
    claim = commands.add_parser("claim", help="give every investigation without an owner to this account")
    claim.add_argument("email")
    commands.add_parser("list", help="accounts, their plan and investigations")
    pro = commands.add_parser("grant-pro", help="make the account Pro for N months (extends a current period)")
    pro.add_argument("email")
    pro.add_argument("--months", type=int, default=1)
    end = commands.add_parser("end-pro", help="back to the free plan now (credits are kept)")
    end.add_argument("email")
    grant = commands.add_parser("grant-credits", help="give the account extra credits")
    grant.add_argument("email")
    grant.add_argument("amount", type=int)
    grant.add_argument("--note", default=None)
    grant.add_argument("--months", type=int, default=12)
    credits = commands.add_parser("credits", help="the account's plan, credits and runs (with their cost to us)")
    credits.add_argument("email")
    return parser.parse_args(argv)


def add_months(moment: datetime, months: int) -> datetime:
    """The same day N months later (the month's last day if that day doesn't exist, e.g. 31 Jan + 1 = 28 Feb)."""
    month = moment.month - 1 + months
    year, month = moment.year + month // 12, month % 12 + 1
    return moment.replace(year=year, month=month, day=min(moment.day, calendar.monthrange(year, month)[1]))


async def billing_command(args: argparse.Namespace, session) -> int:
    user = await find_user_by_email(session, args.email)
    if user is None:
        print(f"Error: no account {args.email}.", file=sys.stderr)
        return 2
    at = now()
    if args.command == "grant-pro":
        if args.months < 1:
            print("Error: --months must be at least 1.", file=sys.stderr)
            return 2
        start = user.plan_until if billing.is_pro(user, at) else at
        user.plan, user.plan_until = "pro", add_months(start, args.months)
        await session.flush()
        await billing.grant_monthly(session, user, at)
        print(f"{user.email} is Pro until {user.plan_until:%d %b %Y}.")
        return 0
    if args.command == "end-pro":
        user.plan, user.plan_until = "free", None
        print(f"{user.email} is on the free plan now (their credits are kept).")
        return 0
    if args.command == "grant-credits":
        if args.amount < 1 or args.months < 1:
            print("Error: the amount and --months must be at least 1.", file=sys.stderr)
            return 2
        grant = await billing.add_grant(session, user, args.amount, source="admin",
                                        expires_at=add_months(at, args.months), note=args.note)
        print(f"Gave {user.email} {grant.amount} credits, valid until {grant.expires_at:%d %b %Y}.")
        return 0
    b = await billing.balance(session, user.id, at)
    plan = f"Pro until {user.plan_until:%d %b %Y}" if billing.is_pro(user, at) else "Free"
    print(f"{user.email}: {plan} | {b.available} credits available ({b.monthly} monthly, {b.other} other, "
          f"{b.held} reserved)")
    holds = (await session.execute(select(CreditHold).where(CreditHold.user_id == user.id)
                                   .order_by(CreditHold.created_at.desc()).limit(20))).scalars()
    for hold in holds:
        cost = f"${hold.usd_used:.2f}" if hold.usd_used is not None else "running"
        charged = "free" if hold.free else f"{hold.charged if hold.charged is not None else '-'} of {hold.price}"
        print(f"  {hold.created_at:%d %b %H:%M}  {hold.action:<8} {hold.outcome or hold.status:<8} "
              f"credits {charged:<10} cost {cost}  {hold.investigation_id}")
    return 0


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
        if args.command in ("grant-pro", "end-pro", "grant-credits", "credits"):
            code = await billing_command(args, session)
            await session.commit()
            return code
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
            user.email_verified_at = now()  # made by an admin: no link needed
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
        users = {u.id: u for u in (await session.execute(select(User))).scalars()}
        rows = (await session.execute(
            select(User.id, User.email, User.name, func.count(Investigation.id))
            .outerjoin(Investigation, Investigation.user_id == User.id).group_by(User.id).order_by(User.email))).all()
        unowned = (await session.execute(select(func.count()).select_from(Investigation).where(
            Investigation.user_id.is_(None)))).scalar_one()
        for user_id, email, name, count in rows:
            plan = "Pro" if billing.is_pro(users[user_id]) else "Free"
            print(f"  {email}  {name or ''}  | {plan} | {count} investigation(s)")
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
