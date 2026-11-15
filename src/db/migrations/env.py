"""Alembic environment: how migrations connect to the database.

The database URL comes from Eureka's settings, never from alembic.ini:
  - normally get_database_settings().url (.env locally, DATABASE_URL in the cloud);
  - tests pass the test database URL in config.attributes["database_url"].

target_metadata is Base.metadata with every model imported (src/db/models), so
`alembic revision --autogenerate` can compare the models with the database.

Commands (from the repo root, venv active, database running):
    alembic upgrade head                              apply all migrations
    alembic downgrade -1                              undo the last migration
    alembic revision --autogenerate -m "add papers"   draft a migration from model changes
    alembic current / alembic history                 where the database is / all migrations
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import src.db.models  # noqa: F401  (registers every table on Base.metadata)
from src.config import get_database_settings
from src.db.base import Base

config = context.config

# Logging setup from alembic.ini, but only when run from the command line:
# programmatic runs (tests) keep the application's logging untouched.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> str:
    return config.attributes.get("database_url") or get_database_settings().url


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: writes the SQL instead of running it (for review)."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # compare_type: autogenerate also notices changed column types, not only new/removed columns
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(database_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
