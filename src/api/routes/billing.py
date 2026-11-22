"""The account's plan and credits (docs/18; step P2): GET /billing for the Plan & billing page. What the plan
allows and what runs are charged is decided in src/api/billing.py; buying Pro and top-ups comes with payments
(step P5)."""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api import billing
from src.api.auth import CurrentUser
from src.api.deps import SessionDep
from src.api.routes.investigations import describe
from src.api.schemas.billing import Billing, BillingEntry, Credits, PlanInfo, Prices
from src.api.schemas.common import ERRORS
from src.config import get_billing_settings
from src.db.models import Candidate, CreditGrant, CreditHold, Investigation, User
from src.timeutil import now

router = APIRouter(prefix="/billing", tags=["billing"])

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
                    prices=Prices(evaluate=s.price_evaluate, discover=s.price_discover, package=s.price_package))


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
                            detail=f"valid until {g.expires_at:%d %b %Y}", credits=g.amount, status="granted")
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
