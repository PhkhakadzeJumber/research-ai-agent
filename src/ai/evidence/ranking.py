"""Ordering candidates by their verdicts (doc 05 §3, "Ranking finalists"). Pure code, no Claude.

No single numeric score is shown. Candidates are ordered by these rules, in this order:
  1. recommendation: Pursue before Refine (before Reconsider);
  2. novelty: Supported opportunity before Potential opportunity (before the rest);
  3. feasibility: Feasible before Feasible with adjustments (before the rest);
  4. significance: Strong, Moderate, Unclear, Weak;
  ties: more verified supporting quotes first (evidence strength).

Used to choose which advanced candidates get a full evaluation (step 5.6a, on their light verdicts) and to
rank the finalists (step 5.6b). `reasons()` says, in words, why one candidate comes before the next.
"""

from __future__ import annotations

from dataclasses import dataclass

RECOMMENDATION_ORDER = ("pursue", "refine", "reconsider")
NOVELTY_ORDER = ("supported_opportunity", "potential_opportunity", "incremental_variation", "insufficient_evidence",
                 "previously_investigated")
FEASIBILITY_ORDER = ("feasible", "feasible_with_adjustments", "not_feasible")
SIGNIFICANCE_ORDER = ("strong", "moderate", "unclear", "weak")
RULES = (("recommendation", RECOMMENDATION_ORDER), ("novelty", NOVELTY_ORDER), ("feasibility", FEASIBILITY_ORDER),
         ("significance", SIGNIFICANCE_ORDER))


@dataclass(frozen=True)
class Standing:
    """One candidate's verdict categories (as stored) and its evidence strength."""

    recommendation: str | None
    novelty: str | None
    feasibility: str | None
    significance: str | None
    supporting_quotes: int = 0


def _position(order: tuple[str, ...], value: str | None) -> int:
    return order.index(value) if value in order else len(order)


def ranking_key(standing: Standing) -> tuple[int, ...]:
    """Smaller sorts first."""
    return (*(_position(order, getattr(standing, name)) for name, order in RULES), -standing.supporting_quotes)


def reasons(better: Standing, worse: Standing) -> str:
    """The first rule that puts `better` before `worse`, in words (for the report)."""
    for name, order in RULES:
        a, b = _position(order, getattr(better, name)), _position(order, getattr(worse, name))
        if a != b:
            return (f"{name}: {getattr(better, name) or 'none'} before {getattr(worse, name) or 'none'}"
                    .replace("_", " "))
    if better.supporting_quotes != worse.supporting_quotes:
        return f"evidence: {better.supporting_quotes} verified supporting quotes before {worse.supporting_quotes}"
    return "tied on every rule"
