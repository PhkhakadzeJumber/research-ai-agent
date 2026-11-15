"""Database connections and sessions.

One Database object per process holds the connection pool. Code works through
sessions (a unit of work):

    database = Database.from_settings()
    async with database.session() as session:
        session.add(paper)
    # leaving the block commits; an exception rolls back instead
    await database.dispose()   # on shutdown

Tests don't use this class for writing: they get a session bound to a transaction
that is always rolled back (tests/db/conftest.py).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_database_settings


def create_engine(url: str) -> AsyncEngine:
    """An async engine. pool_pre_ping replaces connections that died (e.g. after a DB restart)."""
    return create_async_engine(url, pool_pre_ping=True)


class Database:
    def __init__(self, url: str) -> None:
        self.engine = create_engine(url)
        # expire_on_commit=False: objects stay usable after commit without another query
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @classmethod
    def from_settings(cls) -> Database:
        """The main database from the environment (.env locally, DATABASE_URL in the cloud)."""
        return cls(get_database_settings().url)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """A session that commits when the block ends normally and rolls back on an exception."""
        async with self._sessions() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        """Closes all pooled connections."""
        await self.engine.dispose()
