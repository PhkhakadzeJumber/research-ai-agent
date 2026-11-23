"""Checkout and managing billing (step C): demo accounts (EUREKA_DEMO_ACCOUNTS) buy Pro and top-ups with no
payment provider and no money moving; every other account finds checkout closed. Only a card's brand, last four
digits and expiry ever reach the server."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from src.api.routes import billing as billing_routes
from src.config import EmailSettings
from src.db.models import Payment
from src.timeutil import now
from tests.api.conftest import TESTER
from tests.api.test_api_billing import new_user

pytestmark = [pytest.mark.anyio, pytest.mark.db]

CARD = {"card_brand": "Visa", "card_last4": "4242", "card_expiry": "12 / 28"}


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setattr(billing_routes, "get_email_settings", lambda: EmailSettings(demo_accounts=(TESTER["email"],)))


async def test_checkout_is_closed_for_accounts_that_are_not_demo_accounts(client, db_session):
    assert (await client.get("/api/v1/auth/me")).json()["plan"]["checkout_open"] is False
    closed = await client.post("/api/v1/billing/checkout", json={"product": "pro_yearly", **CARD})
    assert (closed.status_code, closed.json()["error"]["code"]) == (403, "payments_closed")
    assert (await db_session.execute(select(func.count()).select_from(Payment))).scalar_one() == 0


async def test_a_demo_account_buys_pro_and_a_top_up(client, db_session, user, demo):
    user.plan, user.plan_until = "free", None  # start on the free plan
    await db_session.flush()
    assert (await client.get("/api/v1/auth/me")).json()["plan"]["checkout_open"] is True
    early = await client.post("/api/v1/billing/checkout", json={"product": "topup", **CARD})
    assert early.json()["error"]["code"] == "pro_only"

    plan = (await client.post("/api/v1/billing/checkout", json={"product": "pro_yearly", **CARD})).json()
    assert plan["plan"] == "pro" and plan["plan_renews"] is True
    until = billing_routes.add_months(now(), 12)
    assert abs((user.plan_until - until).total_seconds()) < 60
    before = plan["credits"]["available"]
    topped = (await client.post("/api/v1/billing/checkout", json={"product": "topup", **CARD})).json()
    assert topped["credits"]["available"] == before + 50

    manage = (await client.get("/api/v1/billing/manage")).json()
    assert (manage["plan"], manage["period"], manage["plan_renews"]) == ("pro", "yearly", True)
    assert manage["card"] == {"brand": "Visa", "last4": "4242", "expiry": "12 / 28"}
    assert [(i["description"], i["amount_usd"], i["card"]) for i in manage["invoices"]] == [
        ("Top-up · 50 credits", 5.0, "Visa •••• 4242"), ("Eureka Pro · yearly", 96.0, "Visa •••• 4242")]
    receipt = await client.get(f"/api/v1/billing/invoices/{manage['invoices'][1]['id']}")
    assert receipt.status_code == 200 and "attachment" in receipt.headers["content-disposition"]
    assert "Eureka Pro · yearly" in receipt.text and "$96.00" in receipt.text and "4242" in receipt.text


async def test_buying_pro_again_extends_the_paid_period(client, db_session, user, demo):
    first = user.plan_until  # the test user is Pro for a year (tests/api/conftest.py)
    await client.post("/api/v1/billing/checkout", json={"product": "pro_monthly", **CARD})
    assert user.plan_until == billing_routes.add_months(first, 1)


async def test_cancelling_keeps_pro_until_its_paid_date(client, user):
    until = user.plan_until
    cancelled = (await client.post("/api/v1/billing/cancel")).json()
    assert cancelled["plan_renews"] is False and cancelled["plan"] == "pro"
    assert user.plan_until == until and (await client.get("/api/v1/auth/me")).json()["plan"]["plan_renews"] is False
    assert (await client.post("/api/v1/billing/renew")).json()["plan_renews"] is True


async def test_the_card_number_never_reaches_the_server(client, demo):
    for card in ({**CARD, "card_number": "4242424242424242"}, {**CARD, "card_last4": "4242424242424242"},
                 {**CARD, "card_expiry": "13 / 28"}):
        response = await client.post("/api/v1/billing/checkout", json={"product": "pro_monthly", **card})
        assert response.status_code == 422, card


async def test_nobody_sees_someone_elses_invoice(client, db_session):
    other = await new_user(db_session, "someone-else@example.com")
    payment = Payment(user_id=other.id, product="pro_monthly", amount_usd=10, card_brand="Visa", card_last4="1111",
                      card_expiry="01 / 30")
    db_session.add(payment)
    await db_session.flush()
    assert (await client.get(f"/api/v1/billing/invoices/{payment.id}")).status_code == 404
    assert (await client.get("/api/v1/billing/manage")).json()["invoices"] == []
