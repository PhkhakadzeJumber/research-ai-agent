"""API test setup (step 9a).

`client`: an HTTP client for the app whose requests use the test's `db_session` (a transaction that is always rolled
back, tests/db/conftest.py), so routes see what the test stored and nothing is left behind. No network: requests go
straight into the app (httpx's ASGI transport), no server is started.
`bare_client`: the same without a database, for routes and behaviour that don't need one.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from src.api.deps import get_database, get_session
from src.api.main import create_app
from tests.db.conftest import db_session, test_database_url  # noqa: F401  (database fixtures, shared)


class FakeDatabase:
    def __init__(self, reachable: bool = True) -> None:
        self.reachable = reachable

    async def ping(self) -> bool:
        return self.reachable

    @asynccontextmanager
    async def session(self):
        # FastAPI opens a route's dependencies before it validates the query, so `bare_client` requests to
        # database routes still need a session: one that is never used (the request fails validation first)
        yield None


def make_client(app) -> httpx.AsyncClient:
    # raise_app_exceptions=False: an unexpected error becomes the app's 500 response, as a real client sees it
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def app():
    app = create_app()
    app.dependency_overrides[get_database] = lambda: FakeDatabase()
    return app


@pytest.fixture
async def bare_client(app) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(app) as client:
        yield client


@pytest.fixture
async def client(app, db_session) -> AsyncIterator[httpx.AsyncClient]:  # noqa: F811  (the shared fixture)
    async def session():
        yield db_session
        await db_session.flush()

    app.dependency_overrides[get_session] = session
    async with make_client(app) as client:
        yield client
