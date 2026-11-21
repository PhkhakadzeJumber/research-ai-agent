"""The quick feasibility check's output (agent A6, task quick feasibility; doc 04; step 5.4).

One batched call judges every candidate waiting for pre-screening (labelled C1, C2, ... in
the input) against the user's complexity level, constraints and exclusions: a proposed
level, what would block it, and any conflict with an exclusion. Short and cheap.

The levels are only proposals: code decides (src/ai/workflows/prescreening.py). Not
feasible or an exclusion conflict screens the candidate out; feasible with adjustments and
unclear pass, because the light evaluation's full Value & Feasibility check decides. A
candidate the model skips counts as unclear.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SCHEMA_VERSION = "quick-feasibility-v1"
QUICK_LEVELS = ("feasible", "feasible_with_adjustments", "not_feasible", "unclear")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuickCheck(_Strict):
    candidate: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^C\d{1,2}$")] = Field(
        description="The candidate's label exactly as in the input, e.g. 'C3'."
    )
    proposed_level: Literal["feasible", "feasible_with_adjustments", "not_feasible", "unclear"] = Field(
        description=("feasible: doable as described within the user's constraints; feasible_with_adjustments: "
                     "doable if narrowed or simplified; not_feasible: a hard constraint (compute, time, data, "
                     "skills) rules it out even simplified; unclear: the description doesn't allow a judgement.")
    )
    blocking_requirements: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                                                 max_length=300)]] = Field(
        default_factory=list, max_length=4,
        description="What the user doesn't have that the study needs as described (e.g. 'a T2 GPU for days').",
    )
    exclusion_conflict: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                                         max_length=300)] | None = Field(
        default=None, description="Only if the idea touches one of the user's exclusions: which one, and how.",
    )
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400)] = Field(
        description="The reason for the level, in one or two sentences."
    )


class QuickFeasibilityOutput(_Strict):
    checks: list[QuickCheck] = Field(description="One check per candidate in the input.")
