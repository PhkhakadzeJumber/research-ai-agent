"""API test setup (step 9a).

`client`: an HTTP client for the app whose requests use the test's `db_session` (a transaction that is always rolled
back, tests/db/conftest.py), so routes see what the test stored and nothing is left behind. No network: requests go
straight into the app (httpx's ASGI transport), no server is started.
`bare_client`: the same without a database, for routes and behaviour that don't need one.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
import pytest

from src.api.auth import COOKIE_NAME, hash_password, new_token, token_hash
from src.api.billing import add_grant
from src.api.deps import get_database, get_session
from src.db.repositories.users import claim_unowned, create_session, create_user
from src.timeutil import now
from src.api.main import create_app
from src.api.runs import RunModels, Runs
from src.config import RoleLlmSettings
from tests.db.conftest import db_session, test_database_url  # noqa: F401  (database fixtures, shared)
from tests.db.test_clarification_workflow import sessions_on

IDEA_SETTINGS = RoleLlmSettings(role="idea_analyst", model="claude-opus-5-5", effort="medium", max_tokens=16000)


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
    # https: the login cookie is Secure, so the client only sends it over https (as a browser would)
    return httpx.AsyncClient(transport=transport, base_url="https://test")


@pytest.fixture
def app():
    app = create_app()
    app.dependency_overrides[get_database] = lambda: FakeDatabase()
    return app


@pytest.fixture
async def bare_client(app) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(app) as client:
        yield client


TESTER = {"email": "tester@example.com", "name": "Test Person", "password": "correct horse battery"}


async def logged_in(db_session, client: httpx.AsyncClient, user) -> None:  # noqa: F811
    """Gives the client a valid session cookie for this user (as logging in would)."""
    token, moment = new_token(), now()
    await create_session(db_session, user_id=user.id, token_hash=token_hash(token),
                         expires_at=moment + timedelta(days=30), now=moment, user_agent="tests")
    client.cookies.set(COOKIE_NAME, token)


async def make_pro(db_session, user, credits: int = 1000) -> None:  # noqa: F811
    """Makes the account Pro for a year with `credits` extra credits (on top of the monthly ones)."""
    user.plan, user.plan_until = "pro", now() + timedelta(days=365)
    if credits:
        await add_grant(db_session, user, credits, source="admin", expires_at=now() + timedelta(days=365),
                        note="tests")
    await db_session.flush()


@pytest.fixture
async def user(db_session):  # noqa: F811  (the shared fixture)
    """The test user the client is logged in as: a Pro account with plenty of credits, so tests of runs aren't
    about the plan (tests/api/test_api_billing.py tests plans and credits, with free accounts too)."""
    user = await create_user(db_session, email=TESTER["email"], name=TESTER["name"],
                             password_hash=hash_password(TESTER["password"]))
    await make_pro(db_session, user)
    return user


@pytest.fixture
async def client(app, db_session, user) -> AsyncIterator[httpx.AsyncClient]:  # noqa: F811  (the shared fixture)
    """Logged in as `user`. Investigations the test data builders create have no owner (like command-line runs);
    before each request they are given to `user`, as `scripts.users claim` would, so the routes see them."""
    async def session():
        await claim_unowned(db_session, user.id)
        yield db_session
        await db_session.flush()

    app.dependency_overrides[get_session] = session
    # routes that open their own short sessions (the live stream) use the runs' sessions: the test's session here
    app.state.runs = ManualRuns(sessions_on(db_session), RunModels(idea_settings=IDEA_SETTINGS))
    async with make_client(app) as client:
        await logged_in(db_session, client, user)
        yield client
    if isinstance(app.state.runs, ManualRuns):
        app.state.runs.discard()


class ManualRuns(Runs):
    """Runs that wait until the test says so (await runs.drain()), instead of starting at once: the test's one
    database session is then never used by a request and a run at the same time."""

    def __init__(self, sessions, models: RunModels) -> None:
        super().__init__(sessions, models=models)
        self.queued: list = []

    def _launch(self, run) -> None:
        self.queued.append(run)

    async def drain(self) -> None:
        while self.queued:
            await self.queued.pop(0)

    def discard(self) -> None:
        for run in self.queued:
            run.close()


@pytest.fixture
def runs(app, db_session):  # noqa: F811  (the shared fixture)
    """The app's background runs on the test's session, with fake Claude (set runs.models in a test as needed)."""
    manual = ManualRuns(sessions_on(db_session), RunModels(idea_settings=IDEA_SETTINGS))
    app.state.runs = manual
    yield manual
    manual.discard()
