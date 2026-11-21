"""The Skeptic's outputs (agent A5; doc 04, doc 10 §7.4). One round has two calls:

  SkepticQueriesOutput  4-8 adversarial searches from angles the first analysis would not
                        have used; code runs them (code-driven rounds, step 4.7)
  ChallengeSetOutput    up to 6 challenges, each aimed at one of the Novelty Investigator's
                        claims (C1, C2, ...) or at the idea itself, with an argument and
                        quotes from the papers it was shown (P1, P2, ...)

The model never writes paper or claim IDs, never verifies its quotes and never resolves
a challenge: code maps labels to IDs, verifies every quote against the stored text, and
the resolution comes from the Novelty Investigator's response (step 4.7b).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from src.ai.schemas.novelty import QUOTE_DESCRIPTION

SCHEMA_VERSION = "skeptic-v1"

Angle = Literal["other_terminology", "older_work", "adjacent_field", "different_method", "closest_work_followup"]
ChallengeType = Literal["prior_work_found", "weak_assumption", "methodological_flaw", "overstated_contribution",
                        "alternative_explanation", "feasibility_risk"]
EVIDENCE_REQUIRED = ("prior_work_found", "overstated_contribution")
Label = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^P\d{1,3}$")]
# 1000: the Skeptic's precise, quoted arguments run long (step 4.7b live run: 600 forced a rewrite
# in all 3 rounds, each a paid repeat call)
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
# Up to 8 words: since step 4.4b an arXiv query that finds nothing is retried with its first 4 words,
# so a longer attack query loses nothing (step 4.7b live run: a 6-word limit forced a rewrite each round)
MAX_ATTACK_QUERY_WORDS = 8


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AttackQuery(_Strict):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=120)] = Field(
        description=("2-6 keywords, most important first, as a paper would phrase them (never more than 8). "
                     "No boolean operators or quotes.")
    )
    angle: Angle
    why: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class SkepticQueriesOutput(_Strict):
    """Searches aimed at finding the paper that already did this idea."""

    queries: list[AttackQuery] = Field(min_length=4, max_length=8)

    @model_validator(mode="after")
    def _distinct(self) -> SkepticQueriesOutput:
        texts = [" ".join(q.text.casefold().split()) for q in self.queries]
        if len(set(texts)) != len(texts):
            raise ValueError("queries: each query must be different")
        too_long = [q.text for q in self.queries if len(q.text.split()) > MAX_ATTACK_QUERY_WORDS]
        if too_long:
            raise ValueError(f"queries must have at most {MAX_ATTACK_QUERY_WORDS} words; shorten: {too_long}")
        return self


class ChallengeEvidence(_Strict):
    paper: Label
    quote: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)] = Field(
        description=QUOTE_DESCRIPTION
    )


class ChallengeOut(_Strict):
    target_claim: Annotated[str, StringConstraints(pattern=r"^C\d{1,2}$")] | None = Field(
        default=None, description="The claim attacked, e.g. 'C2'; null if the challenge is about the idea itself."
    )
    type: ChallengeType
    argument: Text
    evidence: list[ChallengeEvidence] = Field(default_factory=list, max_length=4)
    same_on_facets: list[Annotated[int, Field(ge=1)]] = Field(
        default_factory=list, max_length=10,
        description=("For prior_work_found: the facet numbers on which the cited paper is the SAME as the idea, "
                     "each backed by a quote in evidence. Empty otherwise."),
    )

    @model_validator(mode="after")
    def _evidence_where_needed(self) -> ChallengeOut:
        if self.type in EVIDENCE_REQUIRED and not self.evidence:
            raise ValueError(f"a {self.type} challenge needs quoted evidence")
        if self.same_on_facets and self.type != "prior_work_found":
            raise ValueError("same_on_facets is only for prior_work_found challenges")
        return self


class ChallengeSetOutput(_Strict):
    """The round's challenges. An empty list is a valid answer when nothing holds up."""

    challenges: list[ChallengeOut] = Field(default_factory=list, max_length=6)
