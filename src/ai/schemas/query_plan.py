"""The Idea Analyst's query plan (agent A1, task "query plan"; doc 04): the searches that
look for prior work on one candidate.

What the model fills: 8-20 short keyword queries, each with its kind and the facets
(by number) it covers, and optionally a first year.

The schema enforces what one plan can check on its own (unique queries, sensible
lengths). The quotas that depend on the candidate and the depth (how many queries, how
many terminology variants per key facet, queries without the method) are checked by
check_quotas() in code; a plan that misses them is regenerated once (doc 04 A1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

SCHEMA_VERSION = "query-plan-v1"

QueryKind = Literal["core", "terminology_variant", "facet_only", "facet_pair", "without_method",
                    "method_other_domain"]

QUERY_COUNTS = {"light": (8, 10), "full": (15, 20)}  # doc 05 §11.4: (minimum, maximum) per depth
MIN_WITHOUT_METHOD = 2
MAX_VARIANTS_PER_KEY_FACET = 3


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlannedQuery(_Strict):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=120)] = Field(
        description=(
            "3-8 keywords as a paper would phrase them, e.g. 'retrieval augmented generation low-resource "
            "question answering'. No boolean operators, quotes or parentheses: some sources require every word "
            "to appear, so extra words lose results."
        )
    )
    kind: QueryKind = Field(
        description=(
            "core: the idea's main combination. terminology_variant: the same concept in other words (synonyms, "
            "older or field-specific terms). facet_only: one facet alone. facet_pair: two facets together. "
            "without_method: the problem and setting WITHOUT the method, to find the same problem solved "
            "differently. method_other_domain: the method applied in other domains."
        )
    )
    facets: list[Annotated[int, Field(ge=1)]] = Field(
        min_length=1, max_length=5, description="The numbers of the facets this query covers, as listed in the input."
    )
    year_from: Annotated[int, Field(ge=1950, le=2100)] | None = Field(
        default=None, description="Only papers from this year on; null for no limit (the usual choice)."
    )


class QueryPlanOutput(_Strict):
    """Searches for prior work on one research idea."""

    queries: list[PlannedQuery] = Field(min_length=1, max_length=25)

    @model_validator(mode="after")
    def _unique(self) -> QueryPlanOutput:
        texts = [" ".join(query.text.casefold().split()) for query in self.queries]
        if len(set(texts)) != len(texts):
            raise ValueError("queries: each query must be different")
        return self


@dataclass(frozen=True)
class FacetRef:
    """A candidate facet as the plan refers to it: number = position + 1."""

    number: int
    kind: str
    is_key: bool


def required_variants(key_facets: int, depth: str) -> int:
    """Terminology variants needed per key facet: 3 (doc 04), fewer when the plan has no room."""
    if key_facets == 0:
        return 0
    room = QUERY_COUNTS[depth][1] - MIN_WITHOUT_METHOD
    return max(1, min(MAX_VARIANTS_PER_KEY_FACET, room // key_facets))


def check_quotas(plan: QueryPlanOutput, facets: list[FacetRef], depth: str) -> list[str]:
    """What the plan misses, as messages A1 can act on. Empty: the plan is fine."""
    problems: list[str] = []
    known = {facet.number for facet in facets}
    unknown = sorted({n for query in plan.queries for n in query.facets if n not in known})
    if unknown:
        problems.append(f"queries refer to facet numbers that don't exist: {unknown}")

    low, high = QUERY_COUNTS[depth]
    if not low <= len(plan.queries) <= high:
        problems.append(f"write {low}-{high} queries (you wrote {len(plan.queries)})")

    methods = {facet.number for facet in facets if facet.kind == "method"}
    without_method = [q for q in plan.queries if q.kind == "without_method" and not methods & set(q.facets)]
    if len(without_method) < MIN_WITHOUT_METHOD:
        problems.append(f"at least {MIN_WITHOUT_METHOD} 'without_method' queries that leave out every method facet "
                        f"(found {len(without_method)})")

    keys = [facet for facet in facets if facet.is_key]
    needed = required_variants(len(keys), depth)
    for facet in keys:
        variants = sum(1 for q in plan.queries if q.kind == "terminology_variant" and facet.number in q.facets)
        if variants < needed:
            problems.append(f"at least {needed} 'terminology_variant' queries for key facet {facet.number} "
                            f"({facet.kind}) (found {variants})")
    return problems
