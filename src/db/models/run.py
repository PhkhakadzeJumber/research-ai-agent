"""Request & run control (ER region 1; field meanings in doc 10 §2–§3). Minimal for now.

Only the fields retrieval needs today. Intake, ProgressEvent, LlmCall and the
remaining investigation columns arrive with the code that fills them.

The exact status and stage names of the formal state graph (doc 05 §11) are defined
here and enforced by CHECK constraints, so an investigation can never be in a state
the graph doesn't know. src/ai/workflows (step 4) imports these values from here
(imports only go downward: ai -> db).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, Timestamps, UUIDPrimaryKey

MODES = ("evaluate", "discover")
COMPLEXITY_LEVELS = ("beginner", "intermediate", "advanced")

# doc 05 §11.1: investigation status values
INVESTIGATION_STATUSES = ("created", "running", "clarifying", "completed", "failed", "cancelled")

# doc 05 §11.1: the exact investigation stage names (both workflows share this vocabulary)
INVESTIGATION_STAGES = (
    "intake",
    "understanding",
    "awaiting_clarification",
    "landscape_retrieval",
    "landscape_reading",
    "opportunity_mining",
    "generating_candidates",
    "prescreening",
    "light_evaluation",
    "refining",
    "full_evaluation",
    "triage",
    "ranking",
    "reporting",
    "results_ready",
    "writing_proposal",
)


def one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class User(UUIDPrimaryKey, Timestamps, Base):
    """A person using Eureka, with their constraints profile (doc 10 §2.1)."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
    )

    email: Mapped[str] = mapped_column(String(320))
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=sql_text("'{}'::jsonb"))


class Investigation(UUIDPrimaryKey, Timestamps, Base):
    """One run of Eureka for one request (doc 10 §3.1)."""

    __tablename__ = "investigations"
    __table_args__ = (
        CheckConstraint(one_of("mode", MODES), name="mode_valid"),
        CheckConstraint(one_of("complexity", COMPLEXITY_LEVELS), name="complexity_valid"),
        CheckConstraint(one_of("status", INVESTIGATION_STATUSES), name="status_valid"),
        CheckConstraint(one_of("stage", INVESTIGATION_STAGES), name="stage_valid"),
        CheckConstraint("cost_usd >= 0", name="cost_not_negative"),
    )

    # Empty for runs started from the command line (e.g. scripts/search.py --save);
    # accounts arrive with the API (step 6).
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    mode: Mapped[str] = mapped_column(String(16))
    input: Mapped[dict[str, Any]] = mapped_column(JSONB)
    complexity: Mapped[str] = mapped_column(String(16))
    constraints_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(String(16), default="created", server_default="created")
    stage: Mapped[str] = mapped_column(String(32), default="intake", server_default="intake")
    budget: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=sql_text("'{}'::jsonb"))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=Decimal("0"), server_default="0")
    assumptions: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default=sql_text("'[]'::jsonb"))
    config_version: Mapped[str | None] = mapped_column(String(64))
