"""Tests for src/db/base.py and src/db/session.py against the real test database.

A test-only table (ScratchRow) is created inside each test's transaction, so it is
rolled back together with the data: nothing is left in the test database.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import String, UniqueConstraint, select, text
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.db.base import NAMING_CONVENTION, AwareDateTime, Base, Timestamps, UUIDPrimaryKey
from src.db.session import Database

pytestmark = [pytest.mark.anyio, pytest.mark.db]


class ScratchBase(DeclarativeBase):
    """Separate from the app's Base, so this test table never appears in real migrations."""

    metadata = Base.metadata.__class__(naming_convention=NAMING_CONVENTION)


class ScratchRow(UUIDPrimaryKey, Timestamps, ScratchBase):
    __tablename__ = "scratch_rows"
    __table_args__ = (UniqueConstraint("name"),)

    name: Mapped[str] = mapped_column(String(100))
    happened_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)


@pytest.fixture
async def scratch(db_session):
    """Creates the scratch table inside the test transaction (rolled back afterwards)."""
    connection = await db_session.connection()
    await connection.run_sync(ScratchBase.metadata.create_all)
    return db_session


async def test_session_reaches_the_test_database(db_session):
    name = (await db_session.execute(text("SELECT current_database()"))).scalar()
    vector = (await db_session.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))).scalar()
    assert name == "eureka_test"
    assert vector is not None


async def test_rows_get_uuid_and_aware_timestamps(scratch):
    row = ScratchRow(name="first")
    scratch.add(row)
    await scratch.flush()

    assert isinstance(row.id, uuid.UUID)
    loaded = (await scratch.execute(select(ScratchRow).where(ScratchRow.name == "first"))).scalar_one()
    assert loaded.created_at.tzinfo is not None
    assert loaded.updated_at.tzinfo is not None


async def test_aware_datetimes_round_trip(scratch):
    moment = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    scratch.add(ScratchRow(name="aware", happened_at=moment))
    await scratch.flush()
    loaded = (await scratch.execute(select(ScratchRow.happened_at))).scalar_one()
    assert loaded == moment


async def test_naive_datetimes_are_refused(scratch):
    scratch.add(ScratchRow(name="naive", happened_at=datetime(2026, 11, 15, 12, 0)))
    with pytest.raises(StatementError, match="Naive datetime"):
        await scratch.flush()


async def test_test_writes_never_become_visible_outside_the_test(scratch, test_database_url):
    scratch.add(ScratchRow(name="invisible"))
    await scratch.commit()  # inside the test transaction this only releases a savepoint

    engine = create_async_engine(test_database_url)
    try:
        async with engine.connect() as other_connection:
            exists = (await other_connection.execute(text("SELECT to_regclass('public.scratch_rows')"))).scalar()
    finally:
        await engine.dispose()
    assert exists is None  # even the table is invisible to other connections


async def test_constraint_names_follow_the_convention(scratch):
    connection = await scratch.connection()
    names = (await connection.execute(text(
        "SELECT conname FROM pg_constraint WHERE conrelid = 'scratch_rows'::regclass ORDER BY conname"
    ))).scalars().all()
    assert names == ["pk_scratch_rows", "uq_scratch_rows_name"]


async def test_database_class_connects_to_the_given_url(test_database_url):
    database = Database(test_database_url)
    try:
        async with database.session() as session:
            assert (await session.execute(text("SELECT 1"))).scalar() == 1
    finally:
        await database.dispose()
