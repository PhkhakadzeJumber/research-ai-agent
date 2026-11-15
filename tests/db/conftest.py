"""Database test setup.

- Tests run against the TEST database only (eureka_test), never the main one.
- Each test gets `db_session`: a session inside a transaction that is ALWAYS rolled
  back at the end, so tests never leave data behind, even if the code under test
  calls session.commit() (commits become savepoints inside the outer transaction).
- If the database can't be reached, every database test is skipped and the reason
  is printed in the summary (pytest runs with -rs): start it with `docker compose up -d db`.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.config import ConfigError, get_database_settings

pytestmark = pytest.mark.db

SKIP_REASON = "database not running: start it with `docker compose up -d db` (needs Docker Desktop)"


async def _probe(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """The test database URL, or skip all database tests if it isn't usable."""
    try:
        url = get_database_settings().test_url
    except ConfigError as error:
        pytest.skip(f"database not configured: {error}")
    try:
        asyncio.run(asyncio.wait_for(_probe(url), timeout=10))
    except Exception as error:  # connection refused, wrong password, timeout, ...
        pytest.skip(f"{SKIP_REASON} ({type(error).__name__})")
    return url


@pytest.fixture
async def db_session(test_database_url: str) -> AsyncIterator[AsyncSession]:
    """A session whose work is rolled back after the test."""
    engine = create_async_engine(test_database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()
