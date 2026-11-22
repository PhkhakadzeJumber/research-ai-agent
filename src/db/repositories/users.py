"""Accounts and login sessions (step 9d): storing and finding users and sessions, and claiming the investigations
that have no owner (runs started from the command line)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Investigation, User, UserSession


class EmailTaken(ValueError):
    """An account with this email already exists."""


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def find_user_by_email(session: AsyncSession, email: str) -> User | None:
    return (await session.execute(select(User).where(User.email == normalize_email(email)))).scalar_one_or_none()


async def create_user(session: AsyncSession, *, email: str, name: str | None, password_hash: str | None) -> User:
    if await find_user_by_email(session, email) is not None:
        raise EmailTaken(f"An account with {normalize_email(email)} already exists.")
    user = User(email=normalize_email(email), name=name, password_hash=password_hash)
    session.add(user)
    await session.flush()
    return user


async def create_session(session: AsyncSession, *, user_id: uuid.UUID, token_hash: str, expires_at: datetime,
                         now: datetime, user_agent: str | None) -> UserSession:
    row = UserSession(user_id=user_id, token_hash=token_hash, expires_at=expires_at, last_seen_at=now,
                      user_agent=(user_agent or "")[:200] or None)
    session.add(row)
    await session.flush()
    return row


async def find_session(session: AsyncSession, token_hash: str) -> tuple[UserSession, User] | None:
    row = (await session.execute(select(UserSession, User).join(User, User.id == UserSession.user_id)
                                 .where(UserSession.token_hash == token_hash))).first()
    return (row[0], row[1]) if row else None


async def delete_session(session: AsyncSession, token_hash: str) -> None:
    await session.execute(delete(UserSession).where(UserSession.token_hash == token_hash))


async def delete_other_sessions(session: AsyncSession, user_id: uuid.UUID, keep_token_hash: str | None) -> None:
    query = delete(UserSession).where(UserSession.user_id == user_id)
    if keep_token_hash is not None:
        query = query.where(UserSession.token_hash != keep_token_hash)
    await session.execute(query)


async def claim_unowned(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Gives every investigation without an owner to this user; returns how many."""
    result = await session.execute(update(Investigation).where(Investigation.user_id.is_(None))
                                   .values(user_id=user_id))
    return result.rowcount or 0
