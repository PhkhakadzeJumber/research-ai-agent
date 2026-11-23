"""Plans and credits as the API shows them (docs/18; step P2): the account's plan, its credits, what each action
costs, and the credit history. Dollar amounts never appear here: users see credits (admins see costs in the
database and the scripts)."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import ConfigDict, Field

from src.api.schemas.common import ApiModel, Timestamp

Plan = Literal["free", "pro"]


class InvestigationCredits(ApiModel):
    """What the investigation's runs used, in credits (step P4; docs/18)."""

    used: int
    """Charged so far (with a running run's estimate)."""
    limit: int
    """The most it may use so far: what was charged, plus a running run's reserved price."""
    free: bool
    """A free plan evaluation: no credits are charged."""


class Credits(ApiModel):
    available: int
    """What a new run may use: monthly + other, minus what running ones reserved."""
    monthly: int
    """Left of this month's Pro credits (they expire at the end of the month)."""
    other: int
    """Left of top-ups and other grants."""
    held: int
    """Reserved by runs in progress (what they don't use comes back when they end)."""


class Prices(ApiModel):
    evaluate: int
    discover: int
    package: int


class PlanInfo(ApiModel):
    plan: Plan
    """Pro only while its paid period lasts; free otherwise."""
    plan_until: Timestamp | None
    """When the paid Pro period ends (Pro only)."""
    credits: Credits
    free_evaluations_left: int | None
    """Free plan: evaluations the account may still start this month. Empty on Pro."""
    prices: Prices
    plan_renews: bool
    """Pro: whether it renews at plan_until (Cancel plan turns this off)."""
    checkout_open: bool
    """Whether this account can buy Pro and top-ups here (step C)."""


class BillingEntry(ApiModel):
    at: Timestamp
    kind: Literal["grant", "run"]
    title: str
    """What it was: "Monthly credits · Pro", "Evaluate · <the idea>", "Research package · <the idea>"…"""
    detail: str
    """E.g. "price 25, used 22", "stopped by you", "failed on our side · refunded", "free plan"."""
    credits: int
    """+ granted, − charged (or reserved, while running)."""
    status: Literal["granted", "reserved", "charged", "refunded", "free"]
    investigation_id: uuid.UUID | None = None


class Plans(ApiModel):
    """What the plans offer and cost: the public Pricing page (no login)."""

    prices: Prices
    """Credits per action."""
    pro_monthly_credits: int
    free_evaluations: int
    """Full evaluations a free account may start each month."""
    pro_monthly_usd: float
    pro_yearly_usd: float
    topup_credits: int
    topup_usd: float


Product = Literal["pro_monthly", "pro_yearly", "topup"]


class CheckoutRequest(ApiModel):
    """What the card form sends: never the card number, only what a receipt shows. Extra fields are refused, so a
    full card number can't be sent here even by mistake."""

    model_config = ConfigDict(extra="forbid")
    product: Product
    card_brand: Literal["Visa", "Mastercard", "Amex", "Card"]
    card_last4: str = Field(pattern=r"^[0-9]{4}$")
    card_expiry: str = Field(pattern=r"^(0[1-9]|1[0-2]) / [0-9]{2}$")
    """MM / YY."""


class Invoice(ApiModel):
    id: uuid.UUID
    at: Timestamp
    description: str
    amount_usd: float
    card: str
    """E.g. "Visa •••• 4242"."""


class Card(ApiModel):
    brand: str
    last4: str
    expiry: str


class Manage(ApiModel):
    """Manage billing (step C): the subscription, the card on file and the invoices."""

    plan: Plan
    period: Literal["monthly", "yearly"] | None
    """Pro: how it is billed (from its last payment); empty if it was granted without one."""
    plan_until: Timestamp | None
    plan_renews: bool
    card: Card | None
    invoices: list[Invoice]


class Billing(PlanInfo):
    history: list[BillingEntry]
    """Newest first: the last 100."""
