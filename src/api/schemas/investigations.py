"""Responses about investigations: the list, one investigation, its clarification, results, landscape and cost
(screens 02, 04 and 07 of the UI design)."""

from __future__ import annotations

import uuid
from typing import Literal

from src.api.schemas.common import ApiModel, Labelled, Timestamp
from src.api.schemas.progress import LastRun

Mode = Literal["evaluate", "discover"]


class InvestigationSummary(ApiModel):
    id: uuid.UUID
    mode: Mode
    title: str
    summary: str
    """One line for the list, e.g. "9 ideas · 1 finalist" or "your document · draft.pdf"."""
    status: str
    stage: str
    run: Literal["running", "stopping", "idle"]
    """Whether a background run is working on it now (stopping: asked to stop at the next step boundary). An
    unfinished investigation that is idle was stopped or interrupted: it can be resumed."""
    waiting_for: Literal["clarification"] | None
    """What the user must do before it can go on (answer the clarification), if anything."""
    cost_usd: float
    budget_usd: float
    created_at: Timestamp
    updated_at: Timestamp


class DocumentCard(ApiModel):
    """The user's own document, if the idea came from one (private: only its card, never its text)."""

    file_name: str
    media_type: str
    sections: int
    quotes: int
    verified_quotes: int
    published: bool | None


class InvestigationDetail(InvestigationSummary):
    input: dict
    complexity: str
    constraints: dict
    assumptions: list[str]
    document: DocumentCard | None
    last_run: LastRun | None
    """How the last run ended (done, stopped, budget, account refused, failed, or waiting for you), if any."""


class Facet(ApiModel):
    position: int
    kind: str
    """problem / method / data_setting / evaluation / contribution."""
    text: str
    is_key: bool
    stated_by_user: bool
    """False: Eureka proposed it (the UI marks it "proposed")."""
    from_section: str | None
    """The section of the user's document it was read from, e.g. "D3"."""


class Question(ApiModel):
    question: str
    if_skipped: str


class NoveltyClaim(ApiModel):
    claim: str
    section: str


class IdeaReading(ApiModel):
    title: str
    research_question: str
    facets: list[Facet]
    vagueness_flags: list[str]
    novelty_claims: list[NoveltyClaim]
    """What the user's document says is new: the Skeptic tests each one."""


class Subtopic(ApiModel):
    name: str
    why_it_fits_you: str


class FieldBriefReading(ApiModel):
    subtopics: list[Subtopic]


class Clarification(ApiModel):
    mode: Mode
    confirmed: bool
    confirmed_at: Timestamp | None
    assumptions: list[str]
    questions: list[Question]
    idea: IdeaReading | None
    """Mode 1 (evaluate): Eureka's reading of the idea."""
    field_brief: FieldBriefReading | None
    """Mode 2 (discover): the subtopics Eureka proposes to explore."""


class CandidateSummary(ApiModel):
    id: uuid.UUID
    title: str
    research_question: str
    status: str
    origin: str
    version: int


class VerdictBrief(ApiModel):
    recommendation: Labelled | None
    novelty: Labelled | None
    feasibility: Labelled | None
    significance: Labelled | None


class RankedIdea(ApiModel):
    rank: int
    candidate: CandidateSummary
    depth: Literal["full", "light"]
    verdicts: VerdictBrief
    supporting_quotes: int
    ahead_because: str | None
    """Why it comes before the next one in its list (None for the last)."""
    has_package: bool


class SetAside(ApiModel):
    candidate: CandidateSummary
    why: str


class Results(ApiModel):
    finalists: list[RankedIdea]
    advanced: list[RankedIdea]
    """Passed screening, light evaluation only."""
    others: list[SetAside]


class LandscapeSubtopic(ApiModel):
    name: str
    why_it_fits_you: str
    papers_found: int
    papers_read: int


class OpenProblem(ApiModel):
    id: uuid.UUID
    label: str
    papers: int
    author_groups: int


class Contradiction(ApiModel):
    id: uuid.UUID
    label: str


class Landscape(ApiModel):
    field: str | None
    subtopics: list[LandscapeSubtopic]
    papers_found: int
    papers_read: int
    open_problems: list[OpenProblem]
    contradictions: list[Contradiction]


class CostLine(ApiModel):
    role: str
    calls: int
    cost_usd: float


class Cost(ApiModel):
    total_usd: float
    budget_usd: float
    by_role: list[CostLine]
