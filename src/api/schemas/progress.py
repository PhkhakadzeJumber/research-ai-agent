"""Responses for the progress screen (screen 03 of the UI design; step 9c): progress events, the activity list,
the progress summary (stage rail, counters, cost) and how the last run ended."""

from __future__ import annotations

import uuid
from typing import Literal

from src.api.schemas.common import ApiModel, Timestamp
from src.db.models import ProgressEvent


class ProgressEventOut(ApiModel):
    seq: int
    """1, 2, 3... within the investigation: ask for events after the last one you saw."""
    kind: str
    """stage / step_started / step_done / candidate / warning / error / ended."""
    stage: str | None
    step: str | None
    message: str
    data: dict
    candidate_id: uuid.UUID | None
    cost_usd: float | None
    """The investigation's cost when it happened."""
    at: Timestamp


def event_out(row: ProgressEvent) -> ProgressEventOut:
    return ProgressEventOut(seq=row.seq, kind=row.kind, stage=row.stage, step=row.step, message=row.message,
                            data=row.data or {}, candidate_id=row.candidate_id,
                            cost_usd=float(row.cost_usd) if row.cost_usd is not None else None, at=row.created_at)


class Activity(ApiModel):
    items: list[ProgressEventOut]
    last_seq: int
    """The newest event's number (0 if none): pass it as `after` to get only what is new."""


class LastRun(ApiModel):
    """How the investigation's last run ended (its last "ended" event)."""

    reason: str
    """done / stopped / budget / account_refused / failed / nothing (waiting for the user)."""
    where: str | None
    message: str
    at: Timestamp


StageStatus = Literal["done", "active", "paused", "failed", "todo", "skipped"]


class RailStage(ApiModel):
    key: str
    label: str
    status: StageStatus
    """paused: the run stopped here and can be resumed; failed: the investigation failed here."""


class Counters(ApiModel):
    papers_found: int
    relevant: int
    """Scored 2-3 (shares a key part of the idea, or is direct prior work)."""
    read: int
    quotes_verified: int
    """Quotes from the papers read that matched the text word for word."""
    ideas: int
    finalists: int


class Progress(ApiModel):
    mode: Literal["evaluate", "discover"]
    stage: str
    step: str | None
    """The evaluation step under way, if an evaluation is running."""
    run: Literal["running", "stopping", "idle"]
    rail: list[RailStage]
    counters: Counters
    cost_usd: float
    budget_usd: float
    last_seq: int
    last_run: LastRun | None
