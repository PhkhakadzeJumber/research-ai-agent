"""Response pieces shared by several routes (step 9a).

API response models are their own types (CLAUDE.md): not the agent output schemas (src/ai/schemas) and not the
database tables (src/db/models). Rules every response follows:
  - timestamps are ISO 8601 with the Asia/Tbilisi offset (Timestamp);
  - every category comes with its label ({"category": "potential_opportunity", "label": "Potential opportunity"}),
    so the frontend never translates codes;
  - paper titles and links are rendered from the database (PaperRef), never written by a model;
  - lists are {"items": [...], "total": N}.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, PlainSerializer

from src.ai.citations import paper_link
from src.ai.evidence.verdicts import LABELS
from src.db.models import Paper
from src.timeutil import to_iso

T = TypeVar("T")
Timestamp = Annotated[datetime, PlainSerializer(to_iso, return_type=str)]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Page(ApiModel, Generic[T]):
    items: list[T]
    total: int


class ErrorBody(ApiModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(ApiModel):
    error: ErrorBody


class Labelled(ApiModel):
    category: str
    label: str


def labelled(category: str | None) -> Labelled | None:
    if category is None:
        return None
    return Labelled(category=category, label=LABELS.get(category, category.replace("_", " ").capitalize()))


class Verdict(ApiModel):
    dimension: str
    """novelty / significance / feasibility / recommendation."""
    category: str
    label: str
    rule: str
    rules_version: str
    trace: list[str]
    limits: list[str]
    """Uncertainties and weaknesses the verdict carries (e.g. "sources: 2 (at least 3 needed)")."""
    frozen: bool
    """True once the evaluation concluded (stored); False means preliminary (computed from the current evidence)."""


class PaperRef(ApiModel):
    id: uuid.UUID
    title: str
    year: int | None
    venue: str | None
    link: str | None
    peer_reviewed: bool


def paper_ref(paper: Paper) -> PaperRef:
    return PaperRef(id=paper.id, title=paper.title, year=paper.year, venue=paper.venue,
                    link=paper_link(paper) or None, peer_reviewed=paper.tier == 1)


ERRORS = {404: {"model": ErrorResponse, "description": "Not found"}}
