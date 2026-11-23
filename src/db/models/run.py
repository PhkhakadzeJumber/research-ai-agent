"""Request & run control (ER region 1; field meanings in doc 10 §2–§3). Minimal for now.

Only the fields needed so far. ProgressEvent and the remaining investigation columns
arrive with the code that fills them. LlmCall arrived with step 3.4, Intake with 4.2.

The exact status and stage names of the formal state graph (doc 05 §11) are defined
here and enforced by CHECK constraints, so an investigation can never be in a state
the graph doesn't know. src/ai/workflows (step 4) imports these values from here
(imports only go downward: ai -> db).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric, SmallInteger, String, UniqueConstraint
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import AwareDateTime, Base, Timestamps, UUIDPrimaryKey

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

# doc 05 §11.4: evaluation steps and how an evaluation ended. doc 05 §11.5: candidate
# statuses. The Evaluation and Candidate tables arrive later in step 4; their CHECK
# constraints will use these same tuples.
EVALUATION_STEPS = (
    "normalizing",
    "planning_queries",
    "retrieving",
    "screening",
    "reading",
    "assessing_novelty",
    "skeptic_review",
    "revising_novelty",
    "assessing_value_feasibility",
    "concluding",
    "done",
)
EVALUATION_STATUSES = ("running", "done", "partial", "failed")
CANDIDATE_STATUSES = (
    "proposed",
    "screened_out",
    "light_evaluating",
    "advanced",
    "full_evaluating",
    "needs_refinement",
    "refined",
    "finalist",
    "rejected",
    "selected",
    "proposal_ready",
)


def one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class User(UUIDPrimaryKey, Timestamps, Base):
    """A person using Eureka, with their constraints profile (doc 10 §2.1)."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email"),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint("plan IN ('free', 'pro')", name="plan_valid"),
    )

    email: Mapped[str] = mapped_column(String(320))
    name: Mapped[str | None] = mapped_column(String(120))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    """An Argon2 hash (src/api/auth.py), never the password. Empty for accounts without a password (e.g. a future
    Google sign-in)."""
    last_login_at: Mapped[datetime | None] = mapped_column(AwareDateTime())
    email_verified_at: Mapped[datetime | None] = mapped_column(AwareDateTime())
    """When the user opened the verification link (step B). A free account needs it before its free evaluation."""
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=sql_text("'{}'::jsonb"))
    """The research limits (doc 10 §2.1; edited on the profile page, step 9d); copied into each new investigation."""
    plan: Mapped[str] = mapped_column(String(8), default="free", server_default="free")
    """free or pro (docs/18). Pro counts only while plan_until is in the future."""
    plan_until: Mapped[datetime | None] = mapped_column(AwareDateTime())
    """When the paid Pro period ends (renewed by the payment provider, step P5, or set by scripts/users.py)."""


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
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"), server_default="0")
    """6 decimals, like each call's cost (step 9c; at 4 the total was rounded on every addition)."""
    assumptions: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default=sql_text("'[]'::jsonb"))
    config_version: Mapped[str | None] = mapped_column(String(64))



class Intake(UUIDPrimaryKey, Timestamps, Base):
    """A1's reading of the request, side by side with the user's corrections (doc 10 §3.2).

    Mode 1: a StructuredIdeaOutput (src/ai/schemas/structured_idea.py). Mode 2: the field
    brief (step 5). agent_reading is never changed after it is stored; what the user
    corrects goes into user_edits and answers (step 4.3), so the two can be compared.
    """

    __tablename__ = "intakes"
    __table_args__ = (
        UniqueConstraint("investigation_id"),  # exactly one Intake per investigation
        CheckConstraint(one_of("mode", MODES), name="mode_valid"),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"))
    mode: Mapped[str] = mapped_column(String(16))
    agent_reading: Mapped[dict[str, Any]] = mapped_column(JSONB)
    schema_version: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(128))
    user_edits: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=sql_text("'{}'::jsonb"))
    answers: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default=sql_text("'[]'::jsonb"))
    confirmed_at: Mapped[datetime | None] = mapped_column(AwareDateTime())  # empty until the user confirms or skips

class LlmCall(UUIDPrimaryKey, Timestamps, Base):
    """One call to Claude (doc 10 §8): cost accounting, budgets, debugging, and tying
    evaluation results to prompt versions. Written from src/llm.py's LlmCallRecord."""

    __tablename__ = "llm_calls"
    __table_args__ = (
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND thinking_tokens >= 0 "
            "AND cache_read_tokens >= 0 AND cache_creation_tokens >= 0",
            name="tokens_not_negative",
        ),
        CheckConstraint("cost_usd IS NULL OR cost_usd >= 0", name="cost_not_negative"),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
    )

    # Empty for calls outside an investigation (e.g. a paper analysed for the global library).
    investigation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), index=True
    )
    evaluation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evaluations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(32), index=True)
    model: Mapped[str] = mapped_column(String(64))
    effort: Mapped[str] = mapped_column(String(16))
    prompt_version: Mapped[str] = mapped_column(String(128))
    attempt: Mapped[int] = mapped_column(SmallInteger)  # 2 = the repair attempt after invalid output
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    thinking_tokens: Mapped[int] = mapped_column(Integer)
    cache_read_tokens: Mapped[int] = mapped_column(Integer)
    cache_creation_tokens: Mapped[int] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
    stop_reason: Mapped[str] = mapped_column(String(32))
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))  # estimate; NULL if the price is unknown
