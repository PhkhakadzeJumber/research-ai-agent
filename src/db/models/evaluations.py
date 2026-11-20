"""Judging a candidate (ER region 5; field meanings in doc 10 §7.1). Minimal for now.

An Evaluation is one pass over one candidate version, at a depth (light or full). It
moves through the evaluation steps of the state graph (doc 05 §11.4) and records how it
ended (status). Its searches, hits and Claude calls point at it (evaluation_id).

Only the fields needed so far. The coverage snapshot is computed and frozen at the
`concluding` step (step 4.6), and arrives with that code.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src import timeutil
from src.db.base import AwareDateTime, Base, Timestamps, UUIDPrimaryKey
from src.db.models.run import EVALUATION_STATUSES, EVALUATION_STEPS, one_of

EVALUATION_DEPTHS = ("light", "full")


class Evaluation(UUIDPrimaryKey, Timestamps, Base):
    """One evaluation pass of a candidate version (doc 10 §7.1)."""

    __tablename__ = "evaluations"
    __table_args__ = (
        CheckConstraint(one_of("depth", EVALUATION_DEPTHS), name="depth_valid"),
        CheckConstraint(one_of("step", EVALUATION_STEPS), name="step_valid"),
        CheckConstraint(one_of("status", EVALUATION_STATUSES), name="status_valid"),
        CheckConstraint("finished_at IS NULL OR finished_at >= started_at", name="finished_after_start"),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    # The light evaluation this full evaluation upgrades (it reuses the light pass's hits).
    extends_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evaluations.id"))
    depth: Mapped[str] = mapped_column(String(8))
    step: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="running", server_default="running")
    started_at: Mapped[datetime] = mapped_column(AwareDateTime(), default=timeutil.now, server_default=sql_text("now()"))
    finished_at: Mapped[datetime | None] = mapped_column(AwareDateTime())
    # Queries, papers retrieved/unique, failed sources, ...: budgets and the coverage snapshot read these.
    counters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=sql_text("'{}'::jsonb"))
    # The Idea Analyst's query plan (src/ai/schemas/query_plan.py), with the facets each query covers.
    query_plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
