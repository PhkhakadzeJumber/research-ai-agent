"""Plans and credits as the API shows them (docs/18; step P2): the account's plan, its credits, what each action
costs, and the credit history. Dollar amounts never appear here: users see credits (admins see costs in the
database and the scripts)."""

from __future__ import annotations

import uuid
from typing import Literal

from src.api.schemas.common import ApiModel, Timestamp

Plan = Literal["free", "pro"]


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


class Billing(PlanInfo):
    history: list[BillingEntry]
    """Newest first: the last 100."""
