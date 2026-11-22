"""Plans and credits (docs/18; step P2): who may start what, and what each run is charged.

Every route that starts a run first reserves its price (`reserve`), in the same transaction; every run settles its
hold when it ends (`settle_run`, called by src/api/runs.py), so a hold is open only while its run is. A run is
charged ceil(its cost / credit value), at most the price it reserved; a failure on our side charges nothing.

An action can take several runs: an evaluation is the first reading, then (after the clarification) the evaluation
itself, then any resumes after a stop. The first run reserves the full price; each later one reserves what is left of
it, so the whole action never costs more than its price. If nothing is left (a run that hit its cost cap), a resume
reserves a fifth of the price (shown to the user before they resume).

Free accounts: a full evaluation per month (Mode 1 only), no packages, no discovery; their runs take free holds
(nothing is taken from grants), whose dollar cost counts toward a monthly cap on the free plan as a whole.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import distinct, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.workflows.budget import set_cost_cap
from src.api.errors import ApiError
from src.config import BillingSettings, get_billing_settings
from src.db.models import CreditGrant, CreditHold, Investigation, User
from src.timeutil import now

log = logging.getLogger("eureka.billing")

REFUNDED_REASONS = (None, "failed", "account_refused", "interrupted")
"""How a run ended when it is our fault: an error, the AI provider refusing our account, a server restart."""
RESUME_SHARE = 0.2
"""What a resume reserves when nothing is left of the action's price."""


# --- time -----------------------------------------------------------------------------------------------------

def month_start(at: datetime) -> datetime:
    return at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)  # Tbilisi time, like every timestamp


def next_month(at: datetime) -> datetime:
    start = month_start(at)
    return start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)


# --- plans ----------------------------------------------------------------------------------------------------

def is_pro(user: User, at: datetime | None = None) -> bool:
    return user.plan == "pro" and user.plan_until is not None and user.plan_until > (at or now())


def full_price(action: str, settings: BillingSettings | None = None) -> int:
    s = settings or get_billing_settings()
    return {"evaluate": s.price_evaluate, "discover": s.price_discover, "package": s.price_package}[action]


async def grant_monthly(session: AsyncSession, user: User, at: datetime | None = None) -> None:
    """A Pro account's credits for this month, granted the first time they're needed (no scheduled job): once per
    month, even if requests race (the unique period)."""
    at = at or now()
    if not is_pro(user, at):
        return
    amount = get_billing_settings().pro_monthly_credits
    await session.execute(insert(CreditGrant).values(
        id=uuid.uuid4(), user_id=user.id, source="monthly", period=at.strftime("%Y-%m"), amount=amount,
        remaining=amount, expires_at=next_month(at)).on_conflict_do_nothing(
        index_elements=["user_id", "source", "period"]))


async def add_grant(session: AsyncSession, user: User, amount: int, *, source: str, expires_at: datetime,
                    note: str | None = None) -> CreditGrant:
    grant = CreditGrant(user_id=user.id, source=source, amount=amount, remaining=amount, expires_at=expires_at,
                        note=note)
    session.add(grant)
    await session.flush()
    return grant


# --- balances -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Balance:
    available: int
    """What a new run may reserve: unexpired credits minus the prices of runs in progress."""
    monthly: int
    """Left of this month's Pro allowance."""
    other: int
    """Left of top-ups and admin grants."""
    held: int
    """Reserved by runs in progress."""


async def balance(session: AsyncSession, user_id: uuid.UUID, at: datetime | None = None) -> Balance:
    at = at or now()
    rows = (await session.execute(
        select(CreditGrant.source, func.coalesce(func.sum(CreditGrant.remaining), 0))
        .where(CreditGrant.user_id == user_id, CreditGrant.expires_at > at).group_by(CreditGrant.source))).all()
    by_source = {source: int(total) for source, total in rows}
    held = int((await session.execute(
        select(func.coalesce(func.sum(CreditHold.price), 0))
        .where(CreditHold.user_id == user_id, CreditHold.status == "held", CreditHold.free.is_(False)))).scalar_one())
    monthly = by_source.get("monthly", 0)
    other = sum(total for source, total in by_source.items() if source != "monthly")
    return Balance(available=max(0, monthly + other - held), monthly=monthly, other=other, held=held)


async def free_evaluations_left(session: AsyncSession, user: User, at: datetime | None = None) -> int:
    """Free evaluations the account may still start this month (investigations started this month count)."""
    at = at or now()
    used = (await session.execute(
        select(func.count(distinct(CreditHold.investigation_id)))
        .join(Investigation, Investigation.id == CreditHold.investigation_id)
        .where(CreditHold.user_id == user.id, CreditHold.free.is_(True),
               Investigation.created_at >= month_start(at)))).scalar_one()
    return max(0, get_billing_settings().free_evaluations - int(used))


async def free_plan_cost(session: AsyncSession, at: datetime | None = None) -> Decimal:
    """What free accounts together cost this month: settled free runs, plus the most the running ones may cost."""
    at = at or now()
    s = get_billing_settings()
    settled = (await session.execute(
        select(func.coalesce(func.sum(CreditHold.usd_used), 0))
        .where(CreditHold.free.is_(True), CreditHold.status == "settled",
               CreditHold.settled_at >= month_start(at)))).scalar_one()
    running = (await session.execute(
        select(func.coalesce(func.sum(CreditHold.price), 0))
        .where(CreditHold.free.is_(True), CreditHold.status == "held"))).scalar_one()
    return Decimal(settled) + Decimal(str(int(running) * s.credit_usd * s.run_cap_factor))


async def price_left(session: AsyncSession, investigation_id: uuid.UUID, action: str) -> int:
    """What is left of an action's price after its earlier runs (packages are separate actions: always full)."""
    price = full_price(action)
    if action == "package":
        return price
    charged = (await session.execute(
        select(func.coalesce(func.sum(CreditHold.charged), 0))
        .where(CreditHold.investigation_id == investigation_id, CreditHold.action == action,
               CreditHold.status == "settled"))).scalar_one()
    return price - int(charged)


# --- reserving ------------------------------------------------------------------------------------------------

async def reserve(session: AsyncSession, user: User, investigation: Investigation, action: str, *,
                  continuing: bool = False, candidate_id: uuid.UUID | None = None,
                  budget_usd: float | None = None) -> CreditHold:
    """Checks the plan and the credits, and reserves the run's price; raises ApiError (nothing reserved) if the
    run may not start. Sets the investigation's cost cap for this run: what it has cost so far, plus the price in
    dollars times the cap factor (or less, if the request asked for a lower budget)."""
    s = get_billing_settings()
    at = now()
    # one reservation per account at a time: two requests can't both spend the same credits
    user = (await session.execute(select(User).where(User.id == user.id).with_for_update()
                                 .execution_options(populate_existing=True))).scalar_one()
    pro = is_pro(user, at)
    if action in ("discover", "package") and not pro:
        what = "Finding ideas" if action == "discover" else "Research packages"
        raise ApiError(403, "pro_only", f"{what} are part of Pro. Upgrade to use them.")

    left = await price_left(session, investigation.id, action)
    if not pro:  # a free evaluation
        if not continuing:
            if await free_evaluations_left(session, user, at) <= 0:
                raise ApiError(403, "free_limit_reached", "You've used this month's free evaluation. Upgrade to Pro, "
                                                          "or wait until the 1st.")
            if await free_plan_cost(session, at) >= Decimal(str(s.free_monthly_budget_usd)):
                raise ApiError(503, "free_unavailable", "Free evaluations are paused until next month because of "
                                                        "demand. Pro works as usual.")
        elif left <= 0:
            raise ApiError(403, "free_limit_reached", "This evaluation reached the free plan's limit. Upgrade to "
                                                      "Pro to finish it.")
        price, free = left, True
    else:
        await grant_monthly(session, user, at)
        price = left if left > 0 else math.ceil(full_price(action, s) * RESUME_SHARE)
        if not continuing:
            price = full_price(action, s)
        available = (await balance(session, user.id, at)).available
        if available < price:
            raise ApiError(402, "not_enough_credits", f"This needs {price} credits; you have {available}. Top up, or "
                                                      "wait for next month's credits.")
        free = False

    cost = Decimal(investigation.cost_usd or 0)
    hold = CreditHold(user_id=user.id, investigation_id=investigation.id, candidate_id=candidate_id, action=action,
                      price=price, free=free, cost_at_start=cost)
    session.add(hold)
    if action != "package":
        cap = cost + Decimal(str(round(price * s.credit_usd * s.run_cap_factor, 6)))
        if budget_usd is not None:
            cap = min(cap, Decimal(str(budget_usd)))
        set_cost_cap(investigation, max(cap, cost + Decimal("0.01")))
    await session.flush()
    return hold


async def release(session: AsyncSession, hold: CreditHold) -> None:
    """Undoes a reservation whose run didn't start (e.g. another run started first)."""
    await session.delete(hold)
    await session.flush()


# --- settling -------------------------------------------------------------------------------------------------

def credits_for(usd: Decimal, settings: BillingSettings | None = None) -> int:
    s = settings or get_billing_settings()
    return max(0, math.ceil(float(usd) / s.credit_usd - 1e-9))


async def settle(session: AsyncSession, investigation_id: uuid.UUID, reason: str | None,
                 at: datetime | None = None) -> list[CreditHold]:
    """Settles the investigation's open holds: charged what the run used (at most the price), or nothing if it
    ended through our fault (reason in REFUNDED_REASONS). Credits come from the grants that expire first."""
    at = at or now()
    holds = list((await session.execute(
        select(CreditHold).where(CreditHold.investigation_id == investigation_id, CreditHold.status == "held")
        .with_for_update())).scalars())
    if not holds:
        return []
    investigation = await session.get(Investigation, investigation_id)
    await session.refresh(investigation, ["cost_usd"])
    for hold in holds:
        used = max(Decimal(0), Decimal(investigation.cost_usd or 0) - Decimal(hold.cost_at_start))
        refunded = reason in REFUNDED_REASONS
        hold.charged = 0 if refunded else min(hold.price, credits_for(used))
        hold.outcome = "refunded" if refunded else "charged"
        hold.usd_used, hold.status, hold.settled_at = used, "settled", at
        if not hold.free and hold.charged:
            await _take(session, hold.user_id, hold.charged, at)
    await session.flush()
    return holds


async def _take(session: AsyncSession, user_id: uuid.UUID, amount: int, at: datetime) -> None:
    grants = (await session.execute(
        select(CreditGrant).where(CreditGrant.user_id == user_id, CreditGrant.remaining > 0)
        .order_by(CreditGrant.expires_at <= at, CreditGrant.expires_at).with_for_update())).scalars()
    for grant in grants:
        taken = min(grant.remaining, amount)
        grant.remaining -= taken
        amount -= taken
        if amount == 0:
            return
    log.warning("user %s was charged %d credits more than their grants held", user_id, amount)


async def settle_run(sessions, investigation_id: uuid.UUID, reason: str | None) -> None:
    """Called when a run ends (src/api/runs.py), in its own session. Never raises: a run's end must be recorded
    even if billing fails (the hold then stays open and is settled at the next server start)."""
    try:
        async with sessions() as session:
            await settle(session, investigation_id, reason)
            await session.commit()
    except Exception:  # noqa: BLE001
        log.exception("settling the credits of %s failed", investigation_id)


async def settle_interrupted(sessions) -> int:
    """At server start: runs don't survive a restart, so their open holds are settled as our fault (refunded)."""
    async with sessions() as session:
        ids = list((await session.execute(
            select(CreditHold.investigation_id).where(CreditHold.status == "held").distinct())).scalars())
        for investigation_id in ids:
            await settle(session, investigation_id, "interrupted")
        await session.commit()
    return len(ids)
