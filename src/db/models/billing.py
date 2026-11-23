"""Plans and credits (docs/18; step P2). ER region 1, next to User.

Credits arrive as grants (the monthly Pro allowance, a top-up, or one given by an admin), each with an expiry; what is
left of a grant is `remaining`. Every run takes a hold: its price, reserved when the run starts. When the run ends the
hold is settled: charged ceil(cost / credit value), at most the price, taken from the grants that expire first; the
rest of the hold is released. A failure on our side charges nothing. So:

    available = remaining of unexpired grants - prices of unsettled holds

A free account's evaluation also takes a hold (free=True), with the same price for the run's cost cap, but nothing is
taken from grants: it counts the free evaluations per month and the free plan's total cost (usd_used).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import AwareDateTime, Base, Timestamps, UUIDPrimaryKey

GRANT_SOURCES = ("monthly", "topup", "admin")
HOLD_ACTIONS = ("evaluate", "discover", "package")
HOLD_STATUSES = ("held", "settled")
HOLD_OUTCOMES = ("charged", "refunded")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class CreditGrant(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "credit_grants"
    __table_args__ = (
        CheckConstraint(_one_of("source", GRANT_SOURCES), name="source_valid"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("remaining >= 0 AND remaining <= amount", name="remaining_valid"),
        # the monthly allowance is granted once per month, however many requests race to grant it
        UniqueConstraint("user_id", "source", "period"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(16))
    period: Mapped[str | None] = mapped_column(String(7))
    """The month a monthly grant is for, e.g. 2026-11; empty for other grants."""
    amount: Mapped[int] = mapped_column(Integer)
    remaining: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(AwareDateTime())
    note: Mapped[str | None] = mapped_column(String(200))
    """Why it was granted (an admin grant), or the payment it came from (step P5)."""


class CreditHold(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "credit_holds"
    __table_args__ = (
        CheckConstraint(_one_of("action", HOLD_ACTIONS), name="action_valid"),
        CheckConstraint(_one_of("status", HOLD_STATUSES), name="status_valid"),
        CheckConstraint("price >= 0", name="price_not_negative"),
        CheckConstraint("charged IS NULL OR (charged >= 0 AND charged <= price)", name="charged_valid"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("candidates.id", ondelete="SET NULL"))
    """The idea a research package is for."""
    action: Mapped[str] = mapped_column(String(16))
    price: Mapped[int] = mapped_column(Integer)
    """Credits reserved for this run: the action's price, or what is left of it when a run continues the action."""
    free: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    """A free account's evaluation: nothing is taken from grants."""
    cost_at_start: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    """The investigation's cost when the run started: the run cost what it added."""
    status: Mapped[str] = mapped_column(String(16), default="held", server_default="held")
    outcome: Mapped[str | None] = mapped_column(String(16))
    """charged (what it used, at most the price) or refunded (a failure on our side)."""
    charged: Mapped[int | None] = mapped_column(Integer)
    usd_used: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    """What the run cost us, for the free plan's monthly cap and for admins."""
    settled_at: Mapped[datetime | None] = mapped_column(AwareDateTime())


PRODUCTS = ("pro_monthly", "pro_yearly", "topup")


class Payment(UUIDPrimaryKey, Timestamps, Base):
    """A completed checkout (step C): what was bought, for how much, with which card. Only the card's brand, last
    four digits and expiry are kept (as real shops do); the full number never leaves the browser. Shown as invoices
    on Manage billing."""

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(_one_of("product", PRODUCTS), name="product_valid"),
        CheckConstraint("amount_usd > 0", name="amount_positive"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    product: Mapped[str] = mapped_column(String(16))
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    card_brand: Mapped[str] = mapped_column(String(16))
    card_last4: Mapped[str] = mapped_column(String(4))
    card_expiry: Mapped[str] = mapped_column(String(7))
    """MM / YY."""
