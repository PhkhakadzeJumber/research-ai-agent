"""Tests for accounts (step 9d): sign up, log in, log out, sessions and their cookie, the login attempt limit, the
cross-site check, every route needing a login, each user seeing only their own data, the profile and its limits
reaching new investigations, the password change, and `scripts.users`. Real test database (rolled back), no
network, no cost."""

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from scripts.users import parse_args, run as users_command
from src.api.auth import COOKIE_NAME, token_hash
from src.ai.workflows.value_feasibility import available_weeks
from src.api.schemas.accounts import profile_constraints
from src.db.models import Candidate, Investigation, User, UserSession
from src.timeutil import now
from tests.api.conftest import TESTER, logged_in, make_client
from tests.db.test_clarification_workflow import sessions_on
from tests.db.test_concluding import at_concluding
from tests.db.test_proposal import finalist_investigation

pytestmark = [pytest.mark.anyio, pytest.mark.db]

NEW = {"name": "Ana Beridze", "email": "Ana@Example.com", "password": "a long enough password"}


# --- sign up, log in, log out -----------------------------------------------------------------------------

async def test_sign_up_logs_you_in_with_a_safe_cookie(app, client, db_session):
    anonymous = make_client(app)
    async with anonymous:
        response = await anonymous.post("/api/v1/auth/signup", json=NEW)
        assert response.status_code == 201
        assert response.json()["email"] == "ana@example.com" and response.json()["name"] == "Ana Beridze"
        cookie = response.headers["set-cookie"]
        assert cookie.startswith(f"{COOKIE_NAME}=") and "HttpOnly" in cookie and "Secure" in cookie
        assert "samesite=lax" in cookie.lower() and "Max-Age=2592000" in cookie  # 30 days
        assert (await anonymous.get("/api/v1/auth/me")).json()["email"] == "ana@example.com"

        user = (await db_session.execute(select(User).where(User.email == "ana@example.com"))).scalar_one()
        assert user.password_hash.startswith("$argon2") and NEW["password"] not in user.password_hash
        token = anonymous.cookies.get(COOKIE_NAME)
        stored = (await db_session.execute(select(UserSession).where(UserSession.user_id == user.id))).scalar_one()
        assert stored.token_hash == token_hash(token) and token not in stored.token_hash  # only the hash is kept

        taken = await anonymous.post("/api/v1/auth/signup", json={**NEW, "email": "ana@example.com"})
        assert (taken.status_code, taken.json()["error"]["code"]) == (409, "email_taken")
        for bad in ({**NEW, "password": "short"}, {**NEW, "email": "not-an-email"}, {**NEW, "name": ""}):
            assert (await anonymous.post("/api/v1/auth/signup", json=bad)).status_code == 422


async def test_log_in_and_log_out(app, client, db_session):
    async with make_client(app) as browser:
        wrong = await browser.post("/api/v1/auth/login", json={"email": TESTER["email"], "password": "nope nope"})
        unknown = await browser.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "x"})
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]  # nothing to learn from it

        ok = await browser.post("/api/v1/auth/login", json={"email": TESTER["email"].upper(),
                                                            "password": TESTER["password"]})
        assert ok.status_code == 200 and ok.json()["email"] == TESTER["email"]
        assert (await browser.get("/api/v1/auth/me")).status_code == 200

        token = browser.cookies.get(COOKIE_NAME)
        response = await browser.post("/api/v1/auth/logout")
        assert response.status_code == 204 and f'{COOKIE_NAME}=""' in response.headers["set-cookie"]
        assert (await db_session.execute(select(UserSession).where(
            UserSession.token_hash == token_hash(token)))).first() is None
        browser.cookies.set(COOKIE_NAME, token)  # the old token no longer works
        assert (await browser.get("/api/v1/auth/me")).status_code == 401


async def test_too_many_failed_logins_pause_logging_in(app, client):
    async with make_client(app) as browser:
        for _ in range(5):
            await browser.post("/api/v1/auth/login", json={"email": TESTER["email"], "password": "wrong password"})
        paused = await browser.post("/api/v1/auth/login", json={"email": TESTER["email"],
                                                                "password": TESTER["password"]})
        assert (paused.status_code, paused.json()["error"]["code"]) == (429, "too_many_attempts")


async def test_sessions_expire_and_are_extended_while_used(client, db_session, user):
    [stored] = (await db_session.execute(select(UserSession).where(UserSession.user_id == user.id))).scalars()
    stored.last_seen_at = now() - timedelta(days=2)
    stored.expires_at = now() + timedelta(days=1)
    await db_session.flush()
    assert (await client.get("/api/v1/auth/me")).status_code == 200
    await db_session.refresh(stored)
    assert stored.expires_at > now() + timedelta(days=29)  # used: extended to 30 days again

    stored.expires_at = now() - timedelta(seconds=1)
    await db_session.flush()
    assert (await client.get("/api/v1/auth/me")).json()["error"]["code"] == "not_logged_in"


async def test_requests_from_other_sites_are_refused(client):
    refused = await client.post("/api/v1/auth/logout", headers={"Origin": "https://evil.example"})
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "bad_origin")
    allowed = await client.post("/api/v1/auth/logout", headers={"Origin": "http://localhost:3000"})
    assert allowed.status_code == 204


# --- every route needs a login, and each user sees only their own data -----------------------------------

async def test_every_route_but_health_and_login_needs_a_login(app, client, db_session):
    some = uuid.uuid4()
    async with make_client(app) as anonymous:
        assert (await anonymous.get("/health")).status_code == 200
        for method, path in [("get", "/api/v1/investigations"), ("get", f"/api/v1/investigations/{some}"),
                             ("get", f"/api/v1/investigations/{some}/progress"),
                             ("get", f"/api/v1/investigations/{some}/events"), ("get", "/api/v1/papers"),
                             ("get", f"/api/v1/candidates/{some}"), ("get", "/api/v1/profile"),
                             ("post", f"/api/v1/investigations/{some}/resume"), ("get", "/api/v1/auth/me")]:
            response = await getattr(anonymous, method)(path)
            assert (response.status_code, response.json()["error"]["code"]) == (401, "not_logged_in"), path
        created = await anonymous.post("/api/v1/investigations", json={"mode": "evaluate", "idea": "x"})
        assert created.status_code == 401
    assert (await db_session.execute(select(Investigation))).first() is None


async def test_someone_elses_investigation_is_not_found(app, client, db_session):
    investigation_id = await finalist_investigation(db_session)
    assert (await client.get(f"/api/v1/investigations/{investigation_id}")).status_code == 200  # now the tester's
    [candidate] = (await db_session.execute(select(Candidate).where(
        Candidate.investigation_id == investigation_id))).scalars().all()
    papers = (await client.get("/api/v1/papers?read=true&limit=1")).json()["items"]
    assert papers[0]["investigations"] == 1

    other = User(email="other@example.com", name="Other", password_hash=None)
    db_session.add(other)
    await db_session.flush()
    async with make_client(app) as browser:
        await logged_in(db_session, browser, other)
        assert (await browser.get("/api/v1/investigations")).json() == {"items": [], "total": 0}
        base = f"/api/v1/investigations/{investigation_id}"
        for method, path in [("get", base), ("get", f"{base}/results"), ("get", f"{base}/progress"),
                             ("get", f"{base}/activity"), ("get", f"{base}/events"), ("get", f"{base}/cost"),
                             ("get", f"{base}/report"), ("post", f"{base}/resume"), ("post", f"{base}/stop"),
                             ("get", f"/api/v1/candidates/{candidate.id}"),
                             ("get", f"/api/v1/candidates/{candidate.id}/claims"),
                             ("post", f"/api/v1/candidates/{candidate.id}/proposal")]:
            response = await getattr(browser, method)(path)
            assert response.status_code == 404, path  # the same as an investigation that doesn't exist
        clarify = await browser.post(f"{base}/clarification", json={"confirm": True})
        assert clarify.status_code == 404
        paper = (await browser.get(f"/api/v1/papers/{papers[0]['id']}")).json()
        assert paper["investigations"] == 0 and paper["used_in"] == []  # the library never says whose it was


# --- the profile ------------------------------------------------------------------------------------------

LIMITS = {"academic_level": "undergraduate", "weeks_available": 12, "hours_per_week": 10,
          "compute": "free_notebook_gpu", "compute_note": "Colab", "data_access": "public_only",
          "skills": "Python, basic PyTorch", "ethics": "no human-subject data"}


async def test_the_profile_and_its_limits_reach_new_investigations(client, runs, db_session):
    profile = (await client.get("/api/v1/profile")).json()
    assert profile == {"name": TESTER["name"], "email": TESTER["email"], "limits": {
        key: None for key in profile["limits"]}}

    updated = await client.put("/api/v1/profile", json={"name": "Test P.", "limits": LIMITS})
    assert updated.status_code == 200 and updated.json()["name"] == "Test P."
    assert updated.json()["limits"]["weeks_available"] == 12
    bad = await client.put("/api/v1/profile", json={"limits": {"compute": "supercomputer"}})
    assert bad.status_code == 422

    started = (await client.post("/api/v1/investigations", json={
        "mode": "evaluate", "idea": "RAG for Georgian QA", "constraints": {"compute": "one A100 for a week"}})).json()
    detail = (await client.get(f"/api/v1/investigations/{started['id']}")).json()
    assert detail["constraints"] == {
        "academic level": "undergraduate", "available time": "12 weeks, 10 hours per week",
        "compute": "one A100 for a week",  # typed for this run: it wins over the profile
        "data access": "public data only", "skills": "Python, basic PyTorch", "ethics limits": "no human-subject data"}
    investigation = await db_session.get(Investigation, uuid.UUID(started["id"]))
    assert investigation.user_id is not None


def test_the_limits_as_the_agents_read_them():
    constraints = profile_constraints(LIMITS)
    assert constraints["compute"] == "free notebook GPU (e.g. Colab) (Colab)"
    assert available_weeks(constraints) == 12  # the timeline check finds it
    assert profile_constraints({}) == {} and profile_constraints({"academic_level": "phd"}) == {"academic level": "PhD"}


async def test_changing_the_password_logs_out_the_other_sessions(app, client, db_session, user):
    async with make_client(app) as laptop:
        await logged_in(db_session, laptop, user)
        wrong = await client.post("/api/v1/auth/password", json={"current": "not it at all", "new": "x" * 12})
        assert (wrong.status_code, wrong.json()["error"]["code"]) == (401, "wrong_password")
        changed = await client.post("/api/v1/auth/password", json={"current": TESTER["password"],
                                                                   "new": "a brand new password"})
        assert changed.status_code == 204
        assert (await client.get("/api/v1/auth/me")).status_code == 200  # this session stays
        assert (await laptop.get("/api/v1/auth/me")).status_code == 401  # the other one ended
        relogin = await laptop.post("/api/v1/auth/login", json={"email": TESTER["email"],
                                                                "password": "a brand new password"})
        assert relogin.status_code == 200


# --- scripts.users ----------------------------------------------------------------------------------------

async def test_the_users_command(db_session, capsys):
    database = SimpleNamespace(session=sessions_on(db_session))
    investigation_id, _ = await at_concluding(db_session)  # a command-line run: no owner

    answers = iter(["a good long password", "a good long password"])
    code = await users_command(parse_args(["create", "Me@Example.com", "--name", "Me"]), database,
                               ask=lambda prompt: next(answers))
    assert code == 0 and "Created the account me@example.com." in capsys.readouterr().out
    mismatch = iter(["a good long password", "something else"])
    assert await users_command(parse_args(["create", "x@example.com"]), database, ask=lambda p: next(mismatch)) == 2

    assert await users_command(parse_args(["claim", "me@example.com"]), database) == 0
    assert "1 investigation(s) now belong to me@example.com." in capsys.readouterr().out
    investigation = await db_session.get(Investigation, investigation_id)
    me = (await db_session.execute(select(User).where(User.email == "me@example.com"))).scalar_one()
    assert investigation.user_id == me.id
    assert await users_command(parse_args(["claim", "nobody@example.com"]), database) == 2

    await users_command(parse_args(["list"]), database)
    out = capsys.readouterr().out
    assert "me@example.com  Me  | 1 investigation(s)" in out and "Without an owner: 0" in out
