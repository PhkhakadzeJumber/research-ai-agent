"""Verifying the email and resetting a forgotten password by emailed links (step B). No email leaves the test: a
fake sender records them, and the links are read out of the recorded emails."""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import func, select

from src.api import email as mail
from src.api.auth import COOKIE_NAME
from src.config import EmailSettings
from src.db.models import EmailToken, UserSession
from src.timeutil import now
from tests.api.conftest import TESTER, make_client

pytestmark = [pytest.mark.anyio, pytest.mark.db]

NEW = {"name": "Nino Kapanadze", "email": "nino@example.com", "password": "a long enough password"}


@pytest.fixture
def outbox(monkeypatch):
    sent: list[mail.Email] = []

    async def fake_send(email, client=None):
        sent.append(email)
        return True

    monkeypatch.setattr(mail, "send", fake_send)
    return sent


def token_of(email: mail.Email) -> str:
    return parse_qs(urlparse(email.link).query)["token"][0]


async def test_signing_up_sends_a_verification_link_that_works_once(app, client, db_session, outbox):
    async with make_client(app) as anonymous:
        me = (await anonymous.post("/api/v1/auth/signup", json=NEW)).json()
        assert me["email_verified"] is False
        [email] = outbox
        assert (email.to, email.subject) == ("nino@example.com", "Verify your email for Eureka")
        assert email.link.startswith("http://localhost:3000/verify-email?token=")
        stored = (await db_session.execute(select(EmailToken))).scalar_one()
        assert token_of(email) not in stored.token_hash  # only the hash is kept

        # a free evaluation waits for the verification
        refused = await anonymous.post("/api/v1/investigations", json={"mode": "evaluate", "idea": "An idea to test"})
        assert (refused.status_code, refused.json()["error"]["code"]) == (403, "email_not_verified")

        assert (await anonymous.post("/api/v1/auth/verify-email", json={"token": token_of(email)})).status_code == 204
        assert (await anonymous.get("/api/v1/auth/me")).json()["email_verified"] is True
        again = await anonymous.post("/api/v1/auth/verify-email", json={"token": token_of(email)})
        assert (again.status_code, again.json()["error"]["code"]) == (400, "link_invalid")


async def test_a_new_link_replaces_the_old_one_and_only_once_a_minute(app, client, db_session, outbox, user):
    user.email_verified_at = None
    await db_session.flush()
    assert (await client.post("/api/v1/auth/verify-email/send")).status_code == 204
    soon = await client.post("/api/v1/auth/verify-email/send")
    assert (soon.status_code, soon.json()["error"]["code"]) == (429, "too_soon")
    first = (await db_session.execute(select(EmailToken))).scalar_one()
    first.created_at = now() - timedelta(minutes=2)
    await db_session.flush()
    assert (await client.post("/api/v1/auth/verify-email/send")).status_code == 204
    old, new = outbox
    replaced = await client.post("/api/v1/auth/verify-email", json={"token": token_of(old)})
    assert replaced.json()["error"]["code"] == "link_invalid"
    assert (await client.post("/api/v1/auth/verify-email", json={"token": token_of(new)})).status_code == 204
    done = await client.post("/api/v1/auth/verify-email/send")
    assert (done.status_code, done.json()["error"]["code"]) == (409, "already_verified")


async def test_an_expired_link_says_so(client, db_session, outbox, user):
    user.email_verified_at = None
    await db_session.flush()
    await client.post("/api/v1/auth/verify-email/send")
    row = (await db_session.execute(select(EmailToken))).scalar_one()
    row.expires_at = now() - timedelta(seconds=1)
    await db_session.flush()
    expired = await client.post("/api/v1/auth/verify-email", json={"token": token_of(outbox[0])})
    assert (expired.status_code, expired.json()["error"]["code"]) == (400, "link_expired")


async def test_a_forgotten_password_is_reset_by_the_emailed_link(app, client, db_session, outbox, user):
    async with make_client(app) as anonymous:
        nobody = await anonymous.post("/api/v1/auth/password-reset/request", json={"email": "nobody@example.com"})
        known = await anonymous.post("/api/v1/auth/password-reset/request", json={"email": TESTER["email"]})
        assert nobody.status_code == known.status_code == 202  # the same answer: no way to probe for accounts
        [email] = outbox
        assert email.to == TESTER["email"] and email.link.startswith("http://localhost:3000/reset-password?token=")

        short = await anonymous.post("/api/v1/auth/password-reset", json={"token": token_of(email), "password": "short"})
        assert short.status_code == 422
        reset = await anonymous.post("/api/v1/auth/password-reset",
                                     json={"token": token_of(email), "password": "a brand new password"})
        assert reset.status_code == 200 and reset.json()["email"] == TESTER["email"]
        assert anonymous.cookies.get(COOKIE_NAME)  # logged in here
        # every other session was logged out: the test client's own cookie stops working
        assert (await client.get("/api/v1/auth/me")).status_code == 401
        assert (await db_session.execute(select(func.count()).select_from(UserSession).where(
            UserSession.user_id == user.id))).scalar_one() == 1
        old = await anonymous.post("/api/v1/auth/login", json={"email": TESTER["email"], "password": TESTER["password"]})
        assert old.status_code == 401
        assert (await anonymous.post("/api/v1/auth/login", json={"email": TESTER["email"],
                                                                 "password": "a brand new password"})).status_code == 200
        reused = await anonymous.post("/api/v1/auth/password-reset",
                                      json={"token": token_of(email), "password": "yet another password"})
        assert reused.json()["error"]["code"] == "link_invalid"


async def test_emails_go_through_resend_or_to_the_log(monkeypatch, caplog):
    email = mail.verification_email("nino@example.com", "Nino", "http://localhost:3000/verify-email?token=abc")
    monkeypatch.setattr(mail, "get_email_settings", lambda: EmailSettings())
    with caplog.at_level("INFO", logger="eureka.email"):
        assert await mail.send(email) is True  # no key: logged, with the link on a local site
    assert "Verify your email for Eureka" in caplog.text and "token=abc" in caplog.text

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(url=str(request.url), auth=request.headers["authorization"], body=request.read().decode())
        return httpx.Response(200, json={"id": "email-1"})

    monkeypatch.setattr(mail, "get_email_settings", lambda: EmailSettings(resend_api_key="test-key"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await mail.send(email, client) is True
    assert seen["url"] == mail.RESEND_URL and seen["auth"] == "Bearer test-key"
    assert '"to":["nino@example.com"]' in seen["body"] and "Verify my email" in seen["body"]

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403, json={}))) as client:
        assert await mail.send(email, client) is False  # refused: reported, never raised

