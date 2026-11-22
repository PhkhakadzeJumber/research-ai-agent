"""Things routes ask FastAPI for (dependencies): the database and a session per request.

The app keeps one Database (connection pool) on app.state, created at startup. Each request gets its own session,
committed when the request ends normally and rolled back on an error. Tests replace `get_session` with a session
inside a rolled-back transaction (tests/api/conftest.py).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.runs import Runs
from src.db.session import Database


def get_database(request: Request) -> Database:
    return request.app.state.database


async def get_session(database: Annotated[Database, Depends(get_database)]) -> AsyncIterator[AsyncSession]:
    async with database.session() as session:
        yield session


def get_runs(request: Request) -> Runs:
    """The background runs (src/api/runs.py), one manager per app."""
    return request.app.state.runs


SessionDep = Annotated[AsyncSession, Depends(get_session)]
DatabaseDep = Annotated[Database, Depends(get_database)]
RunsDep = Annotated[Runs, Depends(get_runs)]
