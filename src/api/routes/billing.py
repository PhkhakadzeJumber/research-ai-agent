"""The account's plan and credits (docs/18; step P2): GET /billing for the Plan & billing page. What the plan
allows and what runs are charged is decided in src/api/billing.py.

Checkout (step C): the accounts in EUREKA_DEMO_ACCOUNTS can buy Pro and top-ups here, and it takes effect at once;
no payment provider is called and no money moves (the card form keeps the card number in the browser). For every
other account checkout is closed ("Payments aren't open yet")."""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

import uuid

from fastapi import APIRouter
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api import billing
from src.api.auth import CurrentUser
from src.api.deps import SessionDep
from src.api.routes.investigations import describe
from src.api.errors import ApiError
from src.api.schemas.billing import (
    Billing,
    BillingEntry,
    Card,
    CheckoutRequest,
    Credits,
    Invoice,
    Manage,
    PlanInfo,
    Plans,
    Prices,
)
from src.api.schemas.common import ERRORS
from src.config import get_billing_settings, get_email_settings
from src.db.models import Candidate, CreditGrant, CreditHold, Investigation, Payment, User
from src.timeutil import TZ, now

router = APIRouter(prefix="/billing", tags=["billing"])
public = APIRouter(tags=["billing"])


@public.get("/plans", response_model=Plans)
async def get_plans() -> Plans:
    """What Free and Pro include and cost, for the Pricing page: no login needed."""
    s = get_billing_settings()
    return Plans(prices=Prices(evaluate=s.price_evaluate, discover=s.price_discover, package=s.price_package),
                 pro_monthly_credits=s.pro_monthly_credits, free_evaluations=s.free_evaluations,
                 pro_monthly_usd=s.pro_monthly_usd, pro_yearly_usd=s.pro_yearly_usd, topup_credits=s.topup_credits,
                 topup_usd=s.topup_usd)

HISTORY = 100
GRANT_TITLES = {"monthly": "Monthly credits · Pro", "topup": "Top-up", "admin": "Credits from Eureka"}
ACTION_TITLES = {"evaluate": "Evaluate", "discover": "Find me an idea", "package": "Research package"}


async def plan_info(session: AsyncSession, user: User) -> PlanInfo:
    at = now()
    pro = billing.is_pro(user, at)
    await billing.grant_monthly(session, user, at)
    b = await billing.balance(session, user.id, at)
    s = get_billing_settings()
    return PlanInfo(plan="pro" if pro else "free", plan_until=user.plan_until if pro else None,
                    credits=Credits(available=b.available, monthly=b.monthly, other=b.other, held=b.held),
                    free_evaluations_left=None if pro else await billing.free_evaluations_left(session, user, at),
                    prices=Prices(evaluate=s.price_evaluate, discover=s.price_discover, package=s.price_package),
                    plan_renews=bool(pro and user.plan_renews), checkout_open=checkout_open(user))


def checkout_open(user: User) -> bool:
    return user.email.lower() in get_email_settings().demo_accounts


def _expiry(grant: CreditGrant) -> str:
    """When a grant's credits expire, as the user's calendar says it (Tbilisi): monthly credits last through the
    month's last day (they expire at midnight on the 1st)."""
    last_day = (grant.expires_at.astimezone(TZ) - timedelta(microseconds=1))
    return f"{'expire after' if grant.source == 'monthly' else 'valid until'} {last_day.day} {last_day:%b %Y}"


def _detail(price: int, charged: int, outcomes: set[str], running: bool, free: bool) -> tuple[str, str]:
    """(detail, status) of one action, from its runs' holds."""
    if running:
        return f"running · up to {price} reserved", "reserved"
    if free:
        return "free plan", "free"
    if outcomes == {"refunded"}:
        return "failed on our side · refunded", "refunded"
    return (f"price {price}, used {charged}" if charged < price else f"price {price}"), "charged"


async def history(session: AsyncSession, user: User) -> list[BillingEntry]:
    entries = [BillingEntry(at=g.created_at, kind="grant", title=GRANT_TITLES[g.source] + (
                   f" · {g.note}" if g.note and g.source != "monthly" else ""),
                            detail=_expiry(g), credits=g.amount, status="granted")
               for g in (await session.execute(select(CreditGrant).where(CreditGrant.user_id == user.id)
                                               .order_by(CreditGrant.created_at.desc()).limit(HISTORY))).scalars()]
    holds = list((await session.execute(select(CreditHold).where(CreditHold.user_id == user.id)
                                        .order_by(CreditHold.created_at))).scalars())
    # an action's runs make one line (an evaluation: the reading, the evaluation, any resumes); each package is one
    actions: dict[tuple, list[CreditHold]] = defaultdict(list)
    for hold in holds:
        key = (hold.investigation_id, hold.action, hold.id if hold.action == "package" else None)
        actions[key].append(hold)
    investigations = {i.id: i for i in (await session.execute(select(Investigation).where(
        Investigation.id.in_({h.investigation_id for h in holds})))).scalars()} if holds else {}
    candidates = {c.id: c.title for c in (await session.execute(select(Candidate).where(
        Candidate.id.in_({h.candidate_id for h in holds if h.candidate_id})))).scalars()} if holds else {}
    for (investigation_id, action, _), runs in actions.items():
        first = runs[0]
        running = any(h.status == "held" for h in runs)
        charged = sum(h.charged or 0 for h in runs if h.status == "settled")
        reserved = sum(h.price for h in runs if h.status == "held")
        price = first.price if action == "package" else billing.full_price(action)
        free = all(h.free for h in runs)
        detail, status = _detail(price, charged, {h.outcome for h in runs if h.outcome}, running, free)
        if action == "package":
            what = candidates.get(first.candidate_id, "an idea")
        else:
            what, _ = await describe(session, investigations[investigation_id])
        entries.append(BillingEntry(at=first.created_at, kind="run", title=f"{ACTION_TITLES[action]} · {what}",
                                    detail=detail, credits=0 if free else -(charged + reserved), status=status,
                                    investigation_id=investigation_id))
    entries.sort(key=lambda e: e.at, reverse=True)
    return entries[:HISTORY]


@router.get("", response_model=Billing, responses=ERRORS)
async def get_billing(session: SessionDep, user: CurrentUser) -> Billing:
    """The plan, credits left, prices, and the credit history (grants, and what each action was charged)."""
    info = await plan_info(session, user)
    return Billing(**info.model_dump(), history=await history(session, user))


# --- checkout and managing billing (step C) ---------------------------------------------------------------------

PRODUCT_NAMES = {"pro_monthly": "Eureka Pro · monthly", "pro_yearly": "Eureka Pro · yearly", "topup": "Top-up"}


def add_months(moment: datetime, months: int) -> datetime:
    month = moment.month - 1 + months
    year, month = moment.year + month // 12, month % 12 + 1
    return moment.replace(year=year, month=month, day=min(moment.day, calendar.monthrange(year, month)[1]))


def price_of(product: str) -> Decimal:
    s = get_billing_settings()
    return Decimal(str({"pro_monthly": s.pro_monthly_usd, "pro_yearly": s.pro_yearly_usd, "topup": s.topup_usd}[product]))


@router.post("/checkout", response_model=PlanInfo, responses=ERRORS)
async def checkout(body: CheckoutRequest, session: SessionDep, user: CurrentUser) -> PlanInfo:
    """Buys Pro (a month or a year, added to a current Pro period) or a top-up, and returns the new plan."""
    if not checkout_open(user):
        raise ApiError(403, "payments_closed", "Payments aren't open yet.")
    at = now()
    s = get_billing_settings()
    if body.product == "topup":
        if not billing.is_pro(user, at):
            raise ApiError(403, "pro_only", "Top-ups are for Pro accounts.")
        await billing.add_grant(session, user, s.topup_credits, source="topup", expires_at=add_months(at, 12),
                                note=f"{s.topup_credits} credits")
    else:
        start = user.plan_until if billing.is_pro(user, at) else at
        user.plan, user.plan_renews = "pro", True
        user.plan_until = add_months(start, 12 if body.product == "pro_yearly" else 1)
        await session.flush()
        await billing.grant_monthly(session, user, at)
    session.add(Payment(user_id=user.id, product=body.product, amount_usd=price_of(body.product),
                        card_brand=body.card_brand, card_last4=body.card_last4, card_expiry=body.card_expiry))
    await session.flush()
    return await plan_info(session, user)


async def _manage(session: AsyncSession, user: User) -> Manage:
    payments = list((await session.execute(select(Payment).where(Payment.user_id == user.id)
                                           .order_by(Payment.created_at.desc()))).scalars())
    pro = billing.is_pro(user)
    last_pro = next((p for p in payments if p.product != "topup"), None)
    card = Card(brand=payments[0].card_brand, last4=payments[0].card_last4, expiry=payments[0].card_expiry) \
        if payments else None
    return Manage(plan="pro" if pro else "free",
                  period=("yearly" if last_pro.product == "pro_yearly" else "monthly") if pro and last_pro else None,
                  plan_until=user.plan_until if pro else None, plan_renews=bool(pro and user.plan_renews), card=card,
                  invoices=[Invoice(id=p.id, at=p.created_at, description=PRODUCT_NAMES[p.product]
                                    + (f" · {get_billing_settings().topup_credits} credits" if p.product == "topup" else ""),
                                    amount_usd=float(p.amount_usd), card=f"{p.card_brand} •••• {p.card_last4}")
                            for p in payments])


@router.get("/manage", response_model=Manage, responses=ERRORS)
async def get_manage(session: SessionDep, user: CurrentUser) -> Manage:
    """The subscription, the card on file and the invoices."""
    return await _manage(session, user)


@router.post("/cancel", response_model=Manage, responses=ERRORS)
async def cancel_plan(session: SessionDep, user: CurrentUser) -> Manage:
    """Stops Pro from renewing: it lasts until its paid date, and credits stay until they expire."""
    if not billing.is_pro(user):
        raise ApiError(409, "not_pro", "There's no Pro plan to cancel.")
    user.plan_renews = False
    await session.flush()
    return await _manage(session, user)


@router.post("/renew", response_model=Manage, responses=ERRORS)
async def renew_plan(session: SessionDep, user: CurrentUser) -> Manage:
    """Undoes Cancel plan: Pro renews again at its paid date."""
    if not billing.is_pro(user):
        raise ApiError(409, "not_pro", "There's no Pro plan to renew.")
    user.plan_renews = True
    await session.flush()
    return await _manage(session, user)


@router.get("/invoices/{invoice_id}", response_class=Response, responses=ERRORS)
async def invoice_text(invoice_id: uuid.UUID, session: SessionDep, user: CurrentUser) -> Response:
    """A receipt as a text file."""
    payment = await session.get(Payment, invoice_id)
    if payment is None or payment.user_id != user.id:
        raise ApiError(404, "not_found", "Invoice not found.")
    paid = payment.created_at.astimezone(TZ)
    lines = ["EUREKA · RECEIPT", "", f"Receipt: {payment.id}", f"Date: {paid.day} {paid:%b %Y, %H:%M}",
             f"Billed to: {user.name or ''} <{user.email}>", "", f"{PRODUCT_NAMES[payment.product]}",
             f"Amount: ${payment.amount_usd:.2f} (tax included)",
             f"Paid with: {payment.card_brand} •••• {payment.card_last4}", "", "Thank you for using Eureka."]
    return Response("\n".join(lines) + "\n", media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="eureka-receipt-{paid:%Y%m%d}.txt"'})
