"""What the routes that start and control runs accept (step 9b). Validated here, before anything is stored or paid
for; the limits match the command line's (scripts/evaluate.py, scripts/discover.py)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from src.ai.workflows.budget import MAX_LIGHT_EVALUATIONS

MAX_IDEA_CHARS = 2000  # doc 02 §2.1, as the command line
Complexity = Literal["beginner", "intermediate", "advanced"]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
Constraints = Annotated[dict[ShortText, ShortText], Field(max_length=10)]
"""The user's limits, e.g. {"available time": "8 weeks", "compute": "one GPU"}."""


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvaluateRequest(_Request):
    """Mode 1: evaluate my idea (typed). A document goes through POST /investigations/document instead."""

    mode: Literal["evaluate"]
    idea: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_IDEA_CHARS)]
    field: ShortText | None = None
    seed_papers: Annotated[list[ShortText], Field(max_length=10)] = []
    """Papers the user already knows (DOIs, arXiv IDs or titles, doc 02): the Idea Analyst reads them as context."""
    complexity: Complexity = "beginner"
    constraints: Constraints = {}
    budget_usd: Annotated[float, Field(gt=0)] | None = None
    """The most this investigation may spend on Claude (default $5, at most EUREKA_MAX_BUDGET_USD)."""


class DiscoverRequest(_Request):
    """Mode 2: find me an idea."""

    mode: Literal["discover"]
    field: ShortText
    interests: ShortText | None = None
    technologies: ShortText | None = None
    domain: ShortText | None = None
    exclusions: Annotated[list[ShortText], Field(max_length=10)] = []
    count: Annotated[int, Field(ge=1, le=10)] = 3
    """How many ideas the user wants."""
    complexity: Complexity = "beginner"
    constraints: Constraints = {}
    budget_usd: Annotated[float, Field(gt=0)] | None = None
    """Default $12, at most EUREKA_MAX_BUDGET_USD."""
    max_light: Annotated[int, Field(ge=1, le=MAX_LIGHT_EVALUATIONS)] | None = None
    """How many light evaluations in all (default 5)."""


NewInvestigation = Annotated[EvaluateRequest | DiscoverRequest, Field(discriminator="mode")]


class ClarificationAnswer(_Request):
    answers: Annotated[list[Annotated[str, StringConstraints(max_length=1000)] | None], Field(max_length=10)] = []
    """One per question, in order; null or "" skips it (Eureka then uses the assumption shown). Fewer answers than
    questions skip the rest."""
    edits: Annotated[dict[int, Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                                                 max_length=1000)]], Field(max_length=15)] = {}
    """Mode 1: facet position (as in the clarification's facets) -> the corrected text."""
    remove_subtopics: Annotated[list[Annotated[int, Field(ge=1)]], Field(max_length=10)] = []
    """Mode 2: numbers of the subtopics to drop (1 = the first one shown)."""
    confirm: Literal[True]
    """The user confirms Eureka's reading (with these changes) and starts the evaluation or discovery."""


class ResumeRequest(_Request):
    budget_usd: Annotated[float, Field(gt=0)] | None = None
    """A new cap, e.g. after the budget ran out (at most EUREKA_MAX_BUDGET_USD)."""
