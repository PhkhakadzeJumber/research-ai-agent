"""Responses about the shared paper library: the list and one paper with Eureka's reading of it (screens 09 and
10 of the UI design)."""

from __future__ import annotations

import uuid

from src.api.schemas.common import ApiModel, PaperRef, Timestamp


class LibraryPaper(PaperRef):
    citation_count: int | None
    read: bool
    """True if Eureka read it closely (it has an analysis)."""
    source_level: str | None
    """What the analysis was made from: abstract / full_text (None if not read)."""
    verified_quotes: int
    investigations: int
    """How many investigations found it."""


class QuotedPoint(ApiModel):
    text: str
    quote: str
    verified: bool
    """True if the quote matched the paper's text word for word."""


class Finding(QuotedPoint):
    setting: str | None
    direction: str


class Analysis(ApiModel):
    source_level: str
    created_at: Timestamp
    contribution_type: str | None
    setting: str | None
    tasks: list[str]
    methods: list[str]
    datasets: list[str]
    metrics: list[str]
    baselines: list[str]
    findings: list[Finding]
    limitations: list[QuotedPoint]
    future_work: list[QuotedPoint]


class PaperQuote(ApiModel):
    id: uuid.UUID
    text: str
    verified: bool
    source_level: str


class PaperUse(ApiModel):
    investigation_id: uuid.UUID
    mode: str
    title: str
    roles: list[str]
    """found / read / evidence for a claim / closest work."""


class PaperLinks(ApiModel):
    doi: str | None
    arxiv: str | None
    openalex: str | None
    open_access_pdf: str | None


class PaperDetail(LibraryPaper):
    authors: list[str]
    abstract: str | None
    links: PaperLinks
    analysis: Analysis | None
    quotes: list[PaperQuote]
    used_in: list[PaperUse]
