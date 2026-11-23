"""Plans and credits (docs/18; step P2): what each plan may start, what runs reserve and are charged, refunds, the
monthly Pro credits, the free plan's limits, and that nobody can touch someone else's credits."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from src.api import billing
from src.api.auth import hash_password
from src.db.models import CreditGrant, CreditHold, Investigation
from src.db.repositories.runs import create_investigation
from src.db.repositories.users import create_user
from src.timeutil import now
from tests.api.conftest import logged_in, make_client, make_pro
from tests.ai.test_structured_idea_schema import VALID as READING
from tests.api.test_api_runs import IDEA
from tests.db.test_clarification_workflow import sessions_on
from tests.fakes import ScriptedLlm, answer

pytestmark = [pytest.mark.anyio, pytest.mark.db]


async def new_user(db_session, email: str = "free@example.com", verified: bool = True):
    """An account on the free plan; verified (step B) unless asked, since a free evaluation needs that."""
    user = await create_user(db_session, email=email, name="Someone", password_hash=hash_password("long enough pw"))
    user.email_verified_at = now() if verified else None
    await db_session.flush()
    return user


async def new_investigation(db_session, user, mode: str = "evaluate", cost: str = "0") -> Investigation:
    investigation = await create_investigation(db_session, mode=mode, complexity="beginner",
                                               input={"idea": "an idea"} if mode == "evaluate" else {"field": "NLP"},
                                               user_id=user.id)
    investigation.cost_usd = Decimal(cost)
    await db_session.flush()
    return investigation


async def spend(db_session, investigation: Investigation, usd: str) -> None:
    """What a run's Claude calls would add to the investigation's cost."""
    investigation.cost_usd = Decimal(investigation.cost_usd) + Decimal(usd)
    await db_session.flush()


# --- the plans ------------------------------------------------------------------------------------------------

async def test_a_free_account_gets_one_evaluation_a_month_and_nothing_else(db_session):
    user = await new_user(db_session)
    first = await new_investigation(db_session, user)
    hold = await billing.reserve(db_session, user, first, "evaluate")
    assert (hold.free, hold.price) == (True, 25)  # the price only sets the run's cost cap: nothing is taken
    assert first.budget["cost_usd"] == pytest.approx(1.5)
    assert await billing.free_evaluations_left(db_session, user) == 0

    second = await new_investigation(db_session, user)
    with pytest.raises(billing.ApiError) as refused:
        await billing.reserve(db_session, user, second, "evaluate")
    assert (refused.value.status, refused.value.code) == (403, "free_limit_reached")
    for action, mode in (("discover", "discover"), ("package", "evaluate")):
        with pytest.raises(billing.ApiError) as refused:
            await billing.reserve(db_session, user, await new_investigation(db_session, user, mode), action)
        assert (refused.value.status, refused.value.code) == (403, "pro_only")


async def test_a_free_evaluation_needs_a_verified_email_but_pro_does_not(db_session):
    unverified = await new_user(db_session, verified=False)
    with pytest.raises(billing.ApiError) as refused:
        await billing.reserve(db_session, unverified, await new_investigation(db_session, unverified), "evaluate")
    assert (refused.value.status, refused.value.code) == (403, "email_not_verified")
    await make_pro(db_session, unverified, credits=0)
    assert (await billing.reserve(db_session, unverified, await new_investigation(db_session, unverified),
                                  "evaluate")).free is False


async def test_last_months_free_evaluation_does_not_count(db_session):
    user = await new_user(db_session)
    old = await new_investigation(db_session, user)
    await billing.reserve(db_session, user, old, "evaluate")
    old.created_at = billing.month_start(now()) - timedelta(days=3)
    await db_session.flush()
    assert await billing.free_evaluations_left(db_session, user) == 1


async def test_free_evaluations_pause_when_the_free_plan_reaches_its_monthly_cost(db_session, monkeypatch):
    monkeypatch.setattr(billing, "get_billing_settings", lambda: billing.BillingSettings(free_monthly_budget_usd=2))
    someone = await new_user(db_session, "someone@example.com")
    theirs = await new_investigation(db_session, someone)
    await billing.reserve(db_session, someone, theirs, "evaluate")
    await spend(db_session, theirs, "2.10")
    await billing.settle(db_session, theirs.id, "done")

    user = await new_user(db_session)
    with pytest.raises(billing.ApiError) as refused:
        await billing.reserve(db_session, user, await new_investigation(db_session, user), "evaluate")
    assert (refused.value.status, refused.value.code) == (503, "free_unavailable")


async def test_pro_runs_need_enough_credits(db_session):
    user = await new_user(db_session)
    await make_pro(db_session, user, credits=0)  # just the monthly 100
    discovery = await new_investigation(db_session, user, "discover")
    hold = await billing.reserve(db_session, user, discovery, "discover")
    assert (hold.free, hold.price) == (False, 60)
    assert discovery.budget["cost_usd"] == pytest.approx(3.6)
    assert (await billing.balance(db_session, user.id)).available == 40

    with pytest.raises(billing.ApiError) as refused:
        await billing.reserve(db_session, user, await new_investigation(db_session, user, "discover"), "discover")
    assert (refused.value.status, refused.value.code) == (402, "not_enough_credits")
    assert "needs 60 credits; you have 40" in refused.value.message
    await billing.reserve(db_session, user, await new_investigation(db_session, user), "evaluate")  # 25 fits


async def test_pro_ends_with_its_paid_period(db_session):
    user = await new_user(db_session)
    await make_pro(db_session, user, credits=500)
    user.plan_until = now() - timedelta(minutes=1)
    await db_session.flush()
    assert not billing.is_pro(user)
    with pytest.raises(billing.ApiError) as refused:  # the credits stay, but discovery is Pro only
        await billing.reserve(db_session, user, await new_investigation(db_session, user, "discover"), "discover")
    assert refused.value.code == "pro_only"


async def test_the_monthly_credits_are_granted_once_per_month(db_session):
    user = await new_user(db_session)
    await make_pro(db_session, user, credits=0)
    for _ in range(3):
        await billing.grant_monthly(db_session, user)
    grants = (await db_session.execute(select(CreditGrant).where(CreditGrant.user_id == user.id))).scalars().all()
    assert [(g.source, g.amount, g.period) for g in grants] == [("monthly", 100, now().strftime("%Y-%m"))]
    assert grants[0].expires_at == billing.next_month(now())

    free = await new_user(db_session, "free2@example.com")
    await billing.grant_monthly(db_session, free)
    assert (await billing.balance(db_session, free.id)).available == 0


# --- reserve, then settle -------------------------------------------------------------------------------------

async def test_a_run_is_charged_what_it_used_and_never_more_than_its_price(db_session):
    user = await new_user(db_session)
    await make_pro(db_session, user, credits=50)
    cheap = await new_investigation(db_session, user, cost="0.30")  # what an earlier run cost: not this one's
    await billing.reserve(db_session, user, cheap, "evaluate")
    await spend(db_session, cheap, "0.81")
    [hold] = await billing.settle(db_session, cheap.id, "done")
    assert (hold.status, hold.outcome, hold.charged) == ("settled", "charged", 21)  # ceil(0.81 / 0.04)
    assert hold.usd_used == Decimal("0.81")

    dear = await new_investigation(db_session, user)
    await billing.reserve(db_session, user, dear, "evaluate")
    await spend(db_session, dear, "1.49")  # over the price (it may cost up to 1.5x): we absorb it
    [hold] = await billing.settle(db_session, dear.id, "budget")
    assert hold.charged == 25

    b = await billing.balance(db_session, user.id)
    # monthly credits expire first, so they're used first: 100 - 21 - 25; the 50 extra are untouched
    assert (b.monthly, b.other, b.held, b.available) == (54, 50, 0, 104)


@pytest.mark.parametrize("reason", [None, "failed", "account_refused", "interrupted"])
async def test_a_run_that_fails_on_our_side_is_refunded(db_session, reason):
    user = await new_user(db_session)
    await make_pro(db_session, user, credits=0)
    investigation = await new_investigation(db_session, user)
    await billing.reserve(db_session, user, investigation, "evaluate")
    await spend(db_session, investigation, "0.60")
    [hold] = await billing.settle(db_session, investigation.id, reason)
    assert (hold.outcome, hold.charged, hold.usd_used) == ("refunded", 0, Decimal("0.60"))
    assert (await billing.balance(db_session, user.id)).available == 100


async def test_later_runs_of_an_action_reserve_what_is_left_of_its_price(db_session):
    user = await new_user(db_session)
    await make_pro(db_session, user, credits=0)
    investigation = await new_investigation(db_session, user)
    await billing.reserve(db_session, user, investigation, "evaluate")
    await spend(db_session, investigation, "0.04")  # the first reading: 1 credit
    await billing.settle(db_session, investigation.id, "nothing")

    hold = await billing.reserve(db_session, user, investigation, "evaluate", continuing=True)  # the clarification
    assert hold.price == 24
    await spend(db_session, investigation, "0.95")
    await billing.settle(db_session, investigation.id, "budget")
    assert hold.charged == 24  # the whole action: 25 credits, its price

    resumed = await billing.reserve(db_session, user, investigation, "evaluate", continuing=True)
    assert resumed.price == 5  # nothing left of the price: a resume costs a fifth of it, at most
    assert investigation.budget["cost_usd"] == pytest.approx(0.99 + 5 * 0.04 * 1.5)


async def test_a_free_evaluation_can_continue_but_not_past_its_price(db_session):
    user = await new_user(db_session)
    investigation = await new_investigation(db_session, user)
    await billing.reserve(db_session, user, investigation, "evaluate")
    await spend(db_session, investigation, "0.40")
    await billing.settle(db_session, investigation.id, "stopped")
    hold = await billing.reserve(db_session, user, investigation, "evaluate", continuing=True)  # its own resume
    assert (hold.free, hold.price) == (True, 15)
    await spend(db_session, investigation, "0.90")
    await billing.settle(db_session, investigation.id, "budget")
    with pytest.raises(billing.ApiError) as refused:
        await billing.reserve(db_session, user, investigation, "evaluate", continuing=True)
    assert refused.value.code == "free_limit_reached" and "Upgrade to Pro to finish it" in refused.value.message


async def test_a_lower_budget_lowers_the_cap_but_a_higher_one_cannot_raise_it(db_session):
    user = await new_user(db_session)
    await make_pro(db_session, user, credits=0)
    low, high = await new_investigation(db_session, user), await new_investigation(db_session, user)
    await billing.reserve(db_session, user, low, "evaluate", budget_usd=0.5)
    await billing.reserve(db_session, user, high, "evaluate", budget_usd=20)
    assert (low.budget["cost_usd"], high.budget["cost_usd"]) == (pytest.approx(0.5), pytest.approx(1.5))


async def test_interrupted_runs_get_their_credits_back_at_server_start(db_session):
    user = await new_user(db_session)
    await make_pro(db_session, user, credits=0)
    investigation = await new_investigation(db_session, user)
    await billing.reserve(db_session, user, investigation, "evaluate")
    await spend(db_session, investigation, "0.50")
    assert await billing.settle_interrupted(sessions_on(db_session)) == 1
    hold = (await db_session.execute(select(CreditHold))).scalar_one()
    assert (hold.status, hold.outcome) == ("settled", "refunded")
    assert (await billing.balance(db_session, user.id)).available == 100


# --- through the API ------------------------------------------------------------------------------------------

async def test_a_run_through_the_api_reserves_then_is_charged_when_it_ends(client, runs, db_session, user):
    runs.models.idea_model = ScriptedLlm.with_answers(answer(json.dumps(READING)))
    before = (await client.get("/api/v1/auth/me")).json()["plan"]
    assert before["plan"] == "pro" and before["credits"]["available"] == 1100
    assert before["prices"] == {"evaluate": 25, "discover": 60, "package": 5}

    started = (await client.post("/api/v1/investigations", json=IDEA)).json()
    assert (await client.get("/api/v1/auth/me")).json()["plan"]["credits"]["held"] == 25
    await runs.drain()  # the first reading ends (it waits for the user): its run is settled
    investigation = await db_session.get(Investigation, uuid.UUID(started["id"]))
    hold = (await db_session.execute(select(CreditHold).where(
        CreditHold.investigation_id == investigation.id))).scalar_one()
    assert hold.status == "settled" and hold.charged == billing.credits_for(investigation.cost_usd) > 0
    after = (await client.get("/api/v1/auth/me")).json()["plan"]["credits"]
    assert (after["held"], after["available"]) == (0, 1100 - hold.charged)

    page = (await client.get("/api/v1/billing")).json()
    run = next(e for e in page["history"] if e["kind"] == "run")
    assert (run["title"].startswith("Evaluate · "), run["credits"], run["status"]) == (True, -hold.charged, "charged")
    assert run["detail"] == f"price 25, used {hold.charged}"
    assert {e["title"] for e in page["history"] if e["kind"] == "grant"} == {"Monthly credits · Pro",
                                                                             "Credits from Eureka · tests"}
    monthly = next(e for e in page["history"] if e["title"] == "Monthly credits · Pro")
    last_day = billing.next_month(now()) - timedelta(days=1)  # the month's last day, in Tbilisi
    assert monthly["detail"] == f"expire after {last_day.day} {last_day:%b %Y}"


async def test_the_api_refuses_what_the_plan_does_not_allow(app, db_session, runs):
    user = await new_user(db_session)

    async def session():
        yield db_session
        await db_session.flush()

    from src.api.deps import get_session
    app.dependency_overrides[get_session] = session
    async with make_client(app) as client:
        await logged_in(db_session, client, user)
        me = (await client.get("/api/v1/auth/me")).json()["plan"]
        assert (me["plan"], me["free_evaluations_left"], me["credits"]["available"]) == ("free", 1, 0)
        discover = await client.post("/api/v1/investigations", json={"mode": "discover", "field": "NLP"})
        assert (discover.status_code, discover.json()["error"]["code"]) == (403, "pro_only")
        assert (await client.post("/api/v1/investigations", json=IDEA)).status_code == 202
        again = await client.post("/api/v1/investigations", json=IDEA)
        assert (again.status_code, again.json()["error"]["code"]) == (403, "free_limit_reached")
        page = (await client.get("/api/v1/billing")).json()
        assert page["history"][0]["status"] == "reserved" and page["history"][0]["credits"] == 0


async def test_nobody_sees_or_spends_someone_elses_credits(client, db_session, user):
    other = await new_user(db_session, "other@example.com")
    await make_pro(db_session, other, credits=0)
    theirs = await new_investigation(db_session, other)
    await billing.reserve(db_session, other, theirs, "evaluate")
    page = (await client.get("/api/v1/billing")).json()
    assert all(e["investigation_id"] != str(theirs.id) for e in page["history"])
    assert page["credits"]["held"] == 0 and page["credits"]["available"] == 1100
    assert (await db_session.execute(select(func.count()).select_from(CreditGrant).where(
        CreditGrant.user_id == user.id))).scalar_one() == 2


# --- what the pages show (step P4) ----------------------------------------------------------------------------

async def test_the_plans_are_public(bare_client):
    response = await bare_client.get("/api/v1/plans")
    assert response.status_code == 200
    plans = response.json()
    assert plans["prices"] == {"evaluate": 25, "discover": 60, "package": 5}
    assert (plans["pro_monthly_usd"], plans["pro_yearly_usd"], plans["topup_credits"], plans["topup_usd"]) == (
        10.0, 96.0, 50, 5.0)
    assert (plans["pro_monthly_credits"], plans["free_evaluations"]) == (100, 1)


async def test_an_investigation_shows_its_credits_while_running_and_after(client, db_session, user):
    investigation = await new_investigation(db_session, user)
    await billing.reserve(db_session, user, investigation, "evaluate")
    await spend(db_session, investigation, "0.10")
    url = f"/api/v1/investigations/{investigation.id}"
    running = (await client.get(url)).json()["credits"]
    assert running == {"used": 3, "limit": 25, "free": False}  # ceil(0.10 / 0.04), of the 25 reserved
    await billing.settle(db_session, investigation.id, "nothing")
    assert (await client.get(f"{url}/progress")).json()["credits"] == {"used": 3, "limit": 3, "free": False}
    listed = (await client.get("/api/v1/investigations")).json()["items"]
    assert next(i for i in listed if i["id"] == str(investigation.id))["credits"]["used"] == 3

    free_user = await new_user(db_session)
    theirs = await new_investigation(db_session, free_user)
    await billing.reserve(db_session, free_user, theirs, "evaluate")
    assert (await billing.investigation_credits(db_session, theirs.id))["free"] is True
