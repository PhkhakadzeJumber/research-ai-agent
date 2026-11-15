"""Ranking: Eureka's own judgment of how relevant each paper is to the query or idea.

score = similarity + small priors (doc 06 §2.2):
  - similarity: cosine similarity between the paper's embedding and the query's (0..1 in practice);
  - citations: log-scaled, so 10 vs 1,000 citations matters a little and 1,000 vs 100,000 barely;
  - recency: newer papers get a small boost that fades over ~10 years.
The priors are deliberately small (at most 0.05 each), so an obscure paper that matches
the idea exactly is never buried under a famous one that only roughly matches.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

CITATIONS_SATURATION = 10_000  # citation counts beyond this add no further boost
RECENCY_SPAN_YEARS = 10


@dataclass(frozen=True)
class RankWeights:
    citations: float = 0.05
    recency: float = 0.05


def citation_prior(citation_count: int | None) -> float:
    """0 for no/unknown citations, 1 at CITATIONS_SATURATION or more (log scale)."""
    if not citation_count or citation_count < 0:
        return 0.0
    return min(math.log1p(citation_count) / math.log1p(CITATIONS_SATURATION), 1.0)


def recency_prior(year: int | None, current_year: int) -> float:
    """1 for this year's papers, falling linearly to 0 at RECENCY_SPAN_YEARS old; 0 if unknown."""
    if year is None:
        return 0.0
    age = max(current_year - year, 0)
    return max(1.0 - age / RECENCY_SPAN_YEARS, 0.0)


def relevance_score(
    similarity: float,
    *,
    citation_count: int | None,
    year: int | None,
    current_year: int,
    weights: RankWeights = RankWeights(),
) -> float:
    return (
        similarity
        + weights.citations * citation_prior(citation_count)
        + weights.recency * recency_prior(year, current_year)
    )
