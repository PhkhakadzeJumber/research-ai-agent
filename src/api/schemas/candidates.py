"""Responses about one idea (a candidate): its detail, facet matrix, claims, Skeptic log and research package
(screens 05 and 06 of the UI design)."""

from __future__ import annotations

import uuid

from src.api.schemas.common import ApiModel, PaperRef, Timestamp, Verdict
from src.api.schemas.investigations import CandidateSummary, Facet


class EvaluationBrief(ApiModel):
    id: uuid.UUID
    depth: str
    """light / full."""
    step: str
    status: str
    started_at: Timestamp
    finished_at: Timestamp | None


class Decision(ApiModel):
    rule_number: int
    action: str
    reason: str


class ClosestWork(ApiModel):
    paper: PaperRef
    what_they_did: str
    how_the_idea_differs: str
    minor_difference_only: bool


class DataCheck(ApiModel):
    name: str
    status: str


class Feasibility(ApiModel):
    data_checks: list[DataCheck]
    compute_needed: str | None
    total_weeks: float | None
    available_weeks: float | None
    skill_gaps: list[str]
    risks: list[str]
    ethics: list[str]
    simplifications: list[str]
    beneficiaries: list[str]


class Coverage(ApiModel):
    retrieved: int
    unique: int
    screened: int
    relevant: int
    read: int
    read_abstract_only: int
    sources: list[str]
    years_from: int | None
    years_to: int | None
    blind_spots: list[str]
    budgets_hit: list[str]
    frozen: bool


class CandidateDetail(CandidateSummary):
    investigation_id: uuid.UUID
    parent_id: uuid.UUID | None
    hypothesis: str | None
    motivation: str | None
    potential_contribution: str | None
    change_note: str | None
    rejection_reason: str | None
    facets: list[Facet]
    evaluation: EvaluationBrief | None
    verdicts: list[Verdict]
    preliminary: bool
    """True if the verdicts are computed from the current evidence because the evaluation hasn't concluded."""
    decision: Decision | None
    closest_works: list[ClosestWork]
    feasibility: Feasibility | None
    coverage: Coverage | None


class MatrixCell(ApiModel):
    facet_position: int
    paper_id: uuid.UUID
    overlap: str
    """same / similar / different / not_addressed / unknown."""
    rationale: str
    quote: str | None
    """The verified passage behind the cell, if any."""


class Matrix(ApiModel):
    facets: list[Facet]
    papers: list[PaperRef]
    cells: list[MatrixCell]


class EvidenceQuote(ApiModel):
    quote_id: uuid.UUID
    text: str
    stance: str
    """supports / contradicts / qualifies."""
    verified: bool
    source_level: str
    """abstract / full_text."""
    paper: PaperRef


class ClaimOut(ApiModel):
    id: uuid.UUID
    kind: str
    text: str
    statement_type: str
    inferred: bool
    status: str
    """Set by code from verified quotes: verified, defended, refuted, ..."""
    author_agent: str
    evidence: list[EvidenceQuote]


class SkepticRound(ApiModel):
    round: int
    new_relevant: int
    challenges: int
    saturated: bool


class ChallengeOut(ApiModel):
    id: uuid.UUID
    round: int
    type: str
    argument: str
    statement_type: str
    resolution: str
    """unresolved / accepted / rebutted."""
    target_claim_id: uuid.UUID | None
    new_papers: list[PaperRef]


class SkepticLog(ApiModel):
    rounds: list[SkepticRound]
    challenges: list[ChallengeOut]


class StatementSource(ApiModel):
    kind: str
    """paper / claim."""
    id: uuid.UUID
    label: str
    """The paper's title and year, or the claim's text (rendered from the database)."""


class Statement(ApiModel):
    type: str
    """evidence / interpretation / hypothesis / to_verify."""
    label: str
    text: str
    retagged: bool
    """True if code changed it to "to verify" because no verified source backs it."""
    sources: list[StatementSource]


class PackageSection(ApiModel):
    number: int
    title: str
    status: str
    """Where the section's content comes from, e.g. "cited evidence" or "verdict engine (rules)"."""
    items: list[str]
    statements: list[Statement] | None
    """For the sections A7 writes: each statement with its type and sources (None for sections code renders)."""


class PackageChecks(ApiModel):
    retagged: int
    dropped_labels: int


class ResearchPackageOut(ApiModel):
    id: uuid.UUID
    candidate_id: uuid.UUID
    evaluation_id: uuid.UUID
    revision: int
    model: str
    prompt_version: str
    created_at: Timestamp
    checks: PackageChecks
    sections: list[PackageSection]
