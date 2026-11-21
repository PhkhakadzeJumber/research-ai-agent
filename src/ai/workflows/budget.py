"""Cost budgets (doc 05 §6): how much an investigation may spend on Claude.

The cap is stored on the investigation (investigations.budget = {"cost_usd": 5.0}) and
compared with its running cost (investigations.cost_usd, which every recorded Claude call
adds to). Checked:
  - before each paid step of the `scripts.evaluate` command: when the cap is reached, the
    command stops and says so (resume with a higher --budget);
  - before each further Skeptic round: the loop stops, and the coverage snapshot records
    that the budget, not saturation, ended it.
"""

from __future__ import annotations

from decimal import Decimal

from src.db.models import Investigation

DEFAULT_COST_CAP_USD = Decimal("5")  # doc 05 §6, Mode 1


def cost_cap(investigation: Investigation) -> Decimal:
    value = (investigation.budget or {}).get("cost_usd")
    return Decimal(str(value)) if value is not None else DEFAULT_COST_CAP_USD


def budget_left(investigation: Investigation) -> bool:
    return Decimal(investigation.cost_usd or 0) < cost_cap(investigation)


def set_cost_cap(investigation: Investigation, cap_usd: float | Decimal) -> None:
    if Decimal(str(cap_usd)) <= 0:
        raise ValueError("The budget must be more than $0.")
    investigation.budget = {**(investigation.budget or {}), "cost_usd": float(cap_usd)}


DEFAULT_LIGHT_EVALUATIONS = 5  # Mode 2 allows up to 8 (doc 05 §6); 5 leaves budget for refinements and finalists
MAX_LIGHT_EVALUATIONS = 8


def light_cap(investigation: Investigation) -> int:
    """How many candidates pre-screening may send to light evaluation (Mode 2)."""
    value = (investigation.budget or {}).get("light_evaluations")
    return int(value) if value is not None else DEFAULT_LIGHT_EVALUATIONS


def set_light_cap(investigation: Investigation, count: int) -> None:
    if not 1 <= count <= MAX_LIGHT_EVALUATIONS:
        raise ValueError(f"The number of light evaluations must be 1-{MAX_LIGHT_EVALUATIONS}.")
    investigation.budget = {**(investigation.budget or {}), "light_evaluations": int(count)}
