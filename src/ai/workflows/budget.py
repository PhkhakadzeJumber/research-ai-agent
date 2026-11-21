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
