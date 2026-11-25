"""The step pages (Figma pages 3 and 4; doc 12 §3.4b): a run shown one step per page.

GET /investigations/{id}/steps is the stepper: every step of the mode with its state, time and credits. GET
/investigations/{id}/steps/{key} is one step's page: what the step does (fixed text), what Eureka did (a paragraph
written by code from the stored numbers: no AI call), the step's numbers, its own log, and its output. Outputs that
other pages already show in full (the novelty table, the Skeptic's challenges, the verdicts, the landscape, the
ranking, the research package) are references: the page loads them from their existing endpoints.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal, Union

from pydantic import Field

from src.api.schemas.common import ApiModel, PaperRef, Timestamp

StepState = Literal["done", "running", "waiting", "paused", "failed", "upcoming"]
"""done: finished (open it to read). running: working now. waiting: needs the user (the clarification). paused: the
run stopped here (resume continues). failed: it failed here. upcoming: not reached yet (or optional, not started)."""


class StepSummary(ApiModel):
    key: str
    number: int
    label: str
    """Short, for the stepper (e.g. "Pre-screen")."""
    name: str
    """In full, for the page (e.g. "Pre-screening")."""
    state: StepState
    seconds: int | None
    """How long it took (or has taken so far); empty if this run didn't record it."""
    credits: int | None
    """About how many credits its AI calls cost (rounded per step, so the steps may not add up to the total)."""
    optional: bool = False
    """The research package: only if the user asks for it."""


class Steps(ApiModel):
    mode: Literal["evaluate", "discover"]
    run: Literal["running", "stopping", "idle"]
    current: str | None
    """The step working (or waiting) now; empty when nothing is in progress."""
    steps: list[StepSummary]


class Stat(ApiModel):
    label: str
    value: str


class LogLine(ApiModel):
    at: Timestamp
    message: str


class Query(ApiModel):
    text: str
    source: str
    round: int | None
    """Skeptic rounds search again: 1, 2, 3…; empty for the first search."""
    found: int
    new: int
    """Papers this search added that no earlier search of the step had found."""


class SearchOutput(ApiModel):
    kind: Literal["search"] = "search"
    queries: list[Query]
    papers: int


class ScreenedPaper(ApiModel):
    paper: PaperRef
    score: int | None
    """0 (unrelated) to 3 (directly relevant)."""
    reason: str | None
    read: bool


class ScreeningOutput(ApiModel):
    kind: Literal["screening"] = "screening"
    screened: int
    relevant: int
    """Scored 2 or 3."""
    selected: int
    """Chosen to read closely."""
    scores: dict[str, int]
    """How many papers got each score, "0".."3"."""
    kept: list[ScreenedPaper]
    """The most relevant (at most 40)."""
    dropped: list[ScreenedPaper]
    """Examples of papers judged not relevant, with why (at most 40)."""


class Finding(ApiModel):
    text: str
    quote: str
    verified: bool


class ReadPaper(ApiModel):
    paper: PaperRef
    source_level: str
    """full_text or abstract."""
    findings: list[Finding]


class ReadingOutput(ApiModel):
    kind: Literal["reading"] = "reading"
    read: int
    full_text: int
    findings: int
    quotes: int
    quotes_verified: int
    papers: list[ReadPaper]
    """At most 20, the most informative first."""


class Problem(ApiModel):
    label: str
    papers: int
    quote: str | None
    quote_verified: bool


class ProblemsOutput(ApiModel):
    kind: Literal["problems"] = "problems"
    problems: list[Problem]


class IdeaRow(ApiModel):
    candidate_id: uuid.UUID
    title: str
    research_question: str
    status: str
    detail: str | None
    """Ideas: how it was made. Pre-screening: why it was dropped or kept. Evaluations: its verdict."""
    decision: str | None = None
    """Pre-screening: light_evaluation, reserve or screened_out. Evaluations: the recommendation."""
    novelty: str | None = None


class IdeasOutput(ApiModel):
    kind: Literal["ideas", "prescreen", "light", "full"]
    ideas: list[IdeaRow]


class RefOutput(ApiModel):
    """The step's output is shown by an existing endpoint (see the module docstring)."""

    kind: Literal["ref"] = "ref"
    view: Literal["clarification", "matrix", "challenges", "feasibility", "verdict", "package", "landscape", "ranking"]
    candidate_id: uuid.UUID | None


StepOutput = Annotated[Union[SearchOutput, ScreeningOutput, ReadingOutput, ProblemsOutput, IdeasOutput, RefOutput],
                       Field(discriminator="kind")]


class StepPage(ApiModel):
    step: StepSummary
    mode: Literal["evaluate", "discover"]
    candidate_id: uuid.UUID | None
    """The idea whose evaluation this step shows (Mode 1: yours; Mode 2's finalist pages: that finalist)."""
    does: str
    did: str
    """What Eureka did in this run, in full sentences, written by code from the stored numbers."""
    stats: list[Stat]
    log: list[LogLine]
    output: StepOutput
    previous: str | None
    next: str | None
