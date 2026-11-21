"""The Value & Feasibility Evaluator's output (agent A6, full task; doc 04, doc 07 §4.2-4.3).

Two separate judgements of one candidate:
  significance  is the problem important (claims backed by quotes from the papers shown,
                labelled P1, P2, ...), who benefits, and how big the contribution is
                compared with the closest works
  feasibility   can THIS user do it: the data needed and where it comes from, compute,
                skills and gaps, a timeline per phase, ethics, risks, blocking
                requirements and concrete simplifications

Both levels the model gives are only proposals: code verifies the quotes, checks the data
against the papers read and the timeline against the user's available time, and decides
the verdicts (src/ai/evidence/verdicts.py).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from src.ai.schemas.novelty import QUOTE_DESCRIPTION

SCHEMA_VERSION = "value-feasibility-v1"

Label = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^P\d{1,3}$")]
# Sanity caps, not style limits: a rejected answer costs a whole repeat call, a long text costs
# nothing (step 4.8 live run: 300/800 rejected A6's detailed answers twice and the step failed).
Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=800)]
Long = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuotedEvidence(_Strict):
    paper: Label
    quote: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)] = Field(
        description=QUOTE_DESCRIPTION
    )


class SignificanceClaim(_Strict):
    text: Short = Field(description="Why the problem matters, e.g. 'hallucination of small LMs is an open problem'.")
    evidence: list[QuotedEvidence] = Field(min_length=1, max_length=3)


class Significance(_Strict):
    claims: list[SignificanceClaim] = Field(
        default_factory=list, max_length=6,
        description=("Evidence that the problem matters: papers calling it open or important, or showing a practical "
                     "need. 'It is an interesting area' isn't evidence. Empty if you found none."),
    )
    beneficiaries: list[Short] = Field(default_factory=list, max_length=5)
    contribution_vs_closest: Long = Field(description="How much the idea would add beyond the closest works.")
    proposed_level: Literal["strong", "moderate", "weak", "unclear"]


class DataNeed(_Strict):
    name: Short = Field(description="A dataset or corpus, named exactly as in the papers where possible.")
    use: Short
    source: Literal["from_literature", "public", "collect", "unknown"] = Field(
        description=("from_literature: a paper in the input uses it. public: publicly available, as far as you know. "
                     "collect: must be created. unknown: you can't tell.")
    )


class Phase(_Strict):
    phase: Short
    weeks: Annotated[float, Field(gt=0, le=104)]


class Feasibility(_Strict):
    data_needed: list[DataNeed] = Field(default_factory=list, max_length=8)
    compute_needed: Long
    skills_needed: list[Short] = Field(default_factory=list, max_length=8)
    skill_gaps: list[Short] = Field(
        default_factory=list, max_length=8,
        description="Skills needed that the user's constraints and level don't show.",
    )
    timeline: list[Phase] = Field(default_factory=list, max_length=10)
    ethics: list[Short] = Field(default_factory=list, max_length=5)
    risks: list[Short] = Field(default_factory=list, max_length=8)
    blocking_requirements: list[Short] = Field(
        default_factory=list, max_length=6,
        description="What makes the idea impossible as stated under the user's constraints. Empty if nothing blocks.",
    )
    simplifications: list[Short] = Field(
        default_factory=list, max_length=6,
        description="Concrete changes that would remove the blocks or the risks: smaller model, public data, narrower scope.",
    )
    proposed_level: Literal["feasible", "feasible_with_adjustments", "not_feasible", "unclear"]


class ValueFeasibilityOutput(_Strict):
    """Is the idea worth doing, and can this user do it?"""

    significance: Significance
    feasibility: Feasibility
