"""Progress events: what a run did, step by step, for the progress screen (doc 11 §3.4; step 9c).

One row per event, numbered 1, 2, 3... within its investigation (`seq`), so a page that reconnects asks for
everything after the last number it saw. The driver's events (src/ai/workflows/driver.py) and the understanding
and clarification steps are turned into these rows by src/ai/progress.py; runs started from the API and from the
command line both record them. The message is plain text written by code from stored outcomes (never by a model);
`data` holds the numbers behind it.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, Timestamps, UUIDPrimaryKey
from src.db.models.run import one_of

EVENT_KINDS = ("stage", "step_started", "step_done", "candidate", "warning", "error", "ended")


class ProgressEvent(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "progress_events"
    __table_args__ = (
        UniqueConstraint("investigation_id", "seq"),
        CheckConstraint(one_of("kind", EVENT_KINDS), name="kind_valid"),
        CheckConstraint("seq >= 1", name="seq_positive"),
    )

    # no separate index: the unique (investigation_id, seq) index serves "this investigation's events after N"
    investigation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    stage: Mapped[str | None] = mapped_column(String(32))
    step: Mapped[str | None] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=sql_text("'{}'::jsonb"))
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("candidates.id", ondelete="SET NULL"))
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    """The investigation's cost when the event happened."""
