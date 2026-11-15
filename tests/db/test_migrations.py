"""Tests for the Alembic migrations against the real test database.

- Round trip: every migration can be applied and undone.
- Drift check: the models in src/db/models must match what the migrations build.
  If a model changes without a migration (or the other way round), this fails.
- One head: no accidental parallel migration branches.
"""

import asyncio

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import src.db.models  # noqa: F401  (registers every table on Base.metadata)
from src.db.base import Base

pytestmark = pytest.mark.db


def run_on_database(url, function):
    """Runs a synchronous function with a database connection and returns its result."""

    async def go():
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(function)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def current_revision(connection):
    return MigrationContext.configure(connection).get_current_revision()


def vector_installed(connection):
    return connection.execute(text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")).scalar() == 1


def test_there_is_exactly_one_head(alembic_config):
    assert len(ScriptDirectory.from_config(alembic_config).get_heads()) == 1


def test_test_database_is_at_head(alembic_config, test_database_url):
    head = ScriptDirectory.from_config(alembic_config).get_current_head()
    assert run_on_database(test_database_url, current_revision) == head


def test_migrations_round_trip(alembic_config, test_database_url):
    head = ScriptDirectory.from_config(alembic_config).get_current_head()

    command.downgrade(alembic_config, "base")
    assert run_on_database(test_database_url, current_revision) is None
    assert not run_on_database(test_database_url, vector_installed)

    command.upgrade(alembic_config, "head")
    assert run_on_database(test_database_url, current_revision) == head
    assert run_on_database(test_database_url, vector_installed)


def test_models_match_migrations(test_database_url):
    def differences(connection):
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        return compare_metadata(context, Base.metadata)

    assert run_on_database(test_database_url, differences) == []
