"""The Research Opportunity Scout's output (agent A2, task discover; doc 04, doc 10 §6.3-6.4).

ScoutOutput (task discover): a CandidateSet of 8-12 research ideas grounded in the mined landscape.
RefineOutput (task refine, step 5.5a): the next version of one candidate, with a change note.
ReplenishOutput (Mode 2) and AlternativesOutput (Mode 1), step 5.5b: 3-6 / 3-5 more candidates. The input labels
the material: opportunity items O1, O2, ... (recurring problems, contradictions,
subtopics, datasets), verified statements Q1, Q2, ... and papers P1, P2, ... Every
candidate links to what inspired it by those labels; code maps them back to IDs, so the
model never writes an ID, a title or a quote. Papers named in free text as P3 are turned
into '"Title" (year)' before storing.

What the schema enforces (a broken answer goes back to the model once): the facet rules
of Mode 1 (every kind, at most 2 per kind, at least one key facet, no duplicates), at
least two inspirations per candidate, at least three different origin strategies across
the set, and no two candidates with the same title.

What code checks afterwards (the grounding rule, doc 04): a candidate needs at least two
inspirations that resolve to verified quotes; others are stored as screened out.
Everything the Scout says about value and fit is a hypothesis that the evaluation checks.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from src.ai.schemas.structured_idea import FACET_KINDS, MAX_FACETS_PER_KIND, FacetKind, check_facet_rules

SCHEMA_VERSION = "scout-v1"
MIN_CANDIDATES, MAX_CANDIDATES = 8, 12  # doc 04: discover
MIN_STRATEGIES = 3  # doc 04: diversity
MIN_INSPIRATIONS = 2
MAX_RISKS = 5

OriginStrategy = Literal["limitation", "future_work", "contradiction", "combination", "practical_problem"]
InspirationRole = Literal["motivation", "gap_hint", "practical_need"]
ComputeTier = Literal["T0", "T1", "T2", "T3"]


def _text(max_length: int):
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScoutFacet(_Strict):
    kind: FacetKind = Field(
        description=(
            "problem: what is wrong or unknown. method: the approach or technique. data_setting: the data, "
            "domain, population or language(s). evaluation: how success is measured (metrics, baselines). "
            "contribution: what the work would add to the field."
        )
    )
    text: _text(300) = Field(description="The facet, specific enough to search for and compare with papers.")
    is_key: bool = Field(
        description=("True if this facet is essential to what would make the idea new: changing it would make it a "
                     "different idea. Usually the problem, the method and the data/setting.")
    )


class Inspiration(_Strict):
    source: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[QO]\d{1,4}$")] = Field(
        description="A statement label (Q12) or an opportunity item label (O3), exactly as in the input."
    )
    role: InspirationRole = Field(
        description=("motivation: why the problem matters; gap_hint: evidence that something is missing or "
                     "failing; practical_need: a need of practitioners or users.")
    )
    note: _text(200) = Field(description="One line on how it inspired the idea.")


class Fit(_Strict):
    compute_tier: ComputeTier = Field(
        description="T0 laptop CPU; T1 free or cheap notebook GPU; T2 one modern GPU for days; T3 multi-GPU/cluster."
    )
    data_source: _text(300) = Field(
        description="The data the study needs and where it comes from (a named public dataset, or how to build it)."
    )
    timeline_weeks: Annotated[int, Field(ge=1, le=104)] = Field(description="A rough estimate of the work in weeks.")
    complexity_note: _text(400) = Field(description="Why this fits the user's complexity level and constraints.")


class _CandidateBody(_Strict):
    """The fields every Scout task writes for a candidate."""

    title: _text(150) = Field(description="A short, specific name for the idea.")
    research_question: _text(400) = Field(
        description="ONE research question a concrete study could answer. No title-only ideas."
    )
    hypothesis: _text(400) | None = Field(default=None, description="A testable expectation, if there is one.")
    motivation: _text(1500) = Field(
        description="Why it is worth doing, citing the evidence. Refer to papers as P3, never by title."
    )
    potential_contribution: _text(800) = Field(
        description="What this would add if it works. A claim for the evaluation to check, not a verdict."
    )
    facets: list[ScoutFacet] = Field(
        max_length=len(FACET_KINDS) * MAX_FACETS_PER_KIND,
        description=(f"The idea broken into components: every kind at least once, at most {MAX_FACETS_PER_KIND} per "
                     "kind, at least one key facet."),
    )
    fit: Fit
    risks: list[_text(300)] = Field(default_factory=list, max_length=MAX_RISKS,
                                    description="What could make it fail or not be new (your own guess).")

    @model_validator(mode="after")
    def _facet_rules(self):
        check_facet_rules(self.facets, where=f"facets of '{self.title}'")
        return self


class ScoutCandidate(_CandidateBody):
    """A new idea (task discover)."""

    origin_strategy: OriginStrategy = Field(
        description=("How you found it: limitation (a recurring problem), future_work (a direction papers call for), "
                     "contradiction (findings that disagree), combination (a method not yet applied to a problem: "
                     "phrase it as 'possibly unexplored'), practical_problem (a need of practitioners).")
    )
    inspirations: list[Inspiration] = Field(
        min_length=MIN_INSPIRATIONS, max_length=8,
        description="What inspired it: at least two, and at least two of them statements (Q labels).",
    )


class RefinedCandidate(_CandidateBody):
    """The next version of one candidate (task refine, step 5.5a). Its origin strategy is `refinement` (set by
    code), and it keeps its parent's inspirations; these are extra ones."""

    inspirations: list[Inspiration] = Field(
        default_factory=list, max_length=6,
        description="New inspirations, if the change rests on further evidence (Q labels); the parent's are kept.",
    )
    change_note: _text(600) = Field(
        description="What changed compared with the parent, and why: how it answers the triage reason."
    )
    addresses: _text(300) = Field(description="The problem this version fixes, in a few words (the triage reason).")


class RefineOutput(_Strict):
    candidate: RefinedCandidate


def _set_rules(candidates, min_strategies: int) -> None:
    strategies = {candidate.origin_strategy for candidate in candidates}
    if len(strategies) < min_strategies:
        raise ValueError(f"candidates: use at least {min_strategies} different origin strategies; "
                         f"only {', '.join(sorted(strategies))} used")
    titles = [candidate.title.casefold() for candidate in candidates]
    if len(set(titles)) != len(titles):
        raise ValueError("candidates: each candidate needs a different title")


class ScoutOutput(_Strict):
    candidates: list[ScoutCandidate] = Field(
        min_length=MIN_CANDIDATES, max_length=MAX_CANDIDATES,
        description=(f"{MIN_CANDIDATES}-{MAX_CANDIDATES} different research ideas, covering at least "
                     f"{MIN_STRATEGIES} different origin strategies."),
    )

    @model_validator(mode="after")
    def _rules(self) -> ScoutOutput:
        _set_rules(self.candidates, MIN_STRATEGIES)
        return self


class ReplenishOutput(_Strict):
    """Task replenish (Mode 2, step 5.5b): a few more ideas when too few survived."""

    candidates: list[ScoutCandidate] = Field(
        min_length=3, max_length=6,
        description="3-6 new research ideas, different from those already tried, covering at least 2 strategies.",
    )

    @model_validator(mode="after")
    def _rules(self) -> ReplenishOutput:
        _set_rules(self.candidates, 2)
        return self


class AlternativesOutput(_Strict):
    """Task alternatives (Mode 1, step 5.5b): other ideas when the user's own idea was rejected."""

    candidates: list[ScoutCandidate] = Field(
        min_length=3, max_length=5,
        description="3-5 alternative research ideas grounded in the evidence gathered for the user's idea.",
    )

    @model_validator(mode="after")
    def _rules(self) -> AlternativesOutput:
        _set_rules(self.candidates, 1)
        return self
