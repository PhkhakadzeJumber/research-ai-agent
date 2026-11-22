"""The Novelty Investigator's output (agent A4; doc 04, doc 07 §4.1, doc 10 §7.2-7.3).

What the model fills, for one candidate and the papers it was shown (labelled P1, P2, ...):
  - the facet matrix: how each paper relates to each facet (same / similar / different /
    not_addressed / unknown), with a short rationale and, for same/similar, a verbatim quote;
  - the closest works: what each did, how the idea differs, and whether the difference
    is only a minor facet (the "incremental variation" signal);
  - claims (prior work, gap, limitation, contradiction) with their quoted evidence;
  - a PROPOSED verdict and the remaining uncertainties.

What the model never fills: paper IDs, quote IDs, quote verification, claim status or the
verdict. Code verifies every quote against the stored paper text, sets claim statuses,
and the verdict engine (src/ai/evidence/verdicts.py) decides the verdict.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

SCHEMA_VERSION = "novelty-v1"

Overlap = Literal["same", "similar", "different", "not_addressed", "unknown"]
ClaimKind = Literal["prior_work", "gap", "limitation", "contradiction"]
NoveltyCategory = Literal["previously_investigated", "incremental_variation", "potential_opportunity",
                          "supported_opportunity", "insufficient_evidence"]
Label = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^P\d{1,3}$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
QuoteText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]
# A claim (also a rebuttal) states something precise and may need a few clauses: 1000, like the Skeptic's arguments.
# The step 5.6a live run had a rebuttal over 300 characters, which forced a paid rewrite.
ClaimText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]

QUOTE_DESCRIPTION = (
    "A passage copied EXACTLY, word for word, from this paper's text in the input (its abstract or a "
    "chunk). A code check compares it with the stored text; a quote that doesn't match is discarded "
    "and the statement it supports no longer counts."
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FacetMatchOut(_Strict):
    paper: Label = Field(description="The paper's label, e.g. 'P3'.")
    facet: Annotated[int, Field(ge=1)] = Field(description="The facet's number as listed in the candidate.")
    overlap: Overlap = Field(
        description=("same: the paper has this facet in substance. similar: close but clearly different in a "
                     "detail. different: the paper addresses this aspect differently. not_addressed: the paper "
                     "doesn't touch it. unknown: the text you have can't tell.")
    )
    quote: QuoteText | None = Field(default=None, description=QUOTE_DESCRIPTION + " Required for same and similar.")
    rationale: Text = Field(description="One short line: why this label.")

    @model_validator(mode="after")
    def _quote_for_overlap(self) -> FacetMatchOut:
        if self.overlap in ("same", "similar") and not self.quote:
            raise ValueError(f"{self.paper} facet {self.facet}: 'same' and 'similar' need a verbatim quote")
        return self


class ClosestWork(_Strict):
    paper: Label
    what_they_did: Text
    how_the_idea_differs: Text
    minor_difference_only: bool = Field(
        description=("True only if the idea differs from this paper in a single minor facet (e.g. another dataset "
                     "of the same kind, or another model of the same family).")
    )


class EvidenceOut(_Strict):
    paper: Label
    quote: QuoteText = Field(description=QUOTE_DESCRIPTION)
    stance: Literal["supports", "contradicts", "qualifies"]


class ClaimOut(_Strict):
    kind: ClaimKind
    text: ClaimText = Field(description="One statement. Never 'not studied': say 'not found among the papers examined'.")
    statement_type: Literal["evidence", "interpretation"] = Field(
        description="evidence: a paper states it. interpretation: your reasoning from the evidence (e.g. a gap)."
    )
    inferred: bool = Field(description="True if you concluded it rather than a source stating it (gap claims usually).")
    evidence: list[EvidenceOut] = Field(default_factory=list, max_length=4)


class NoveltyAssessmentOutput(_Strict):
    """How much of the idea already exists in the papers examined."""

    facet_matches: list[FacetMatchOut] = Field(max_length=150)
    closest_works: list[ClosestWork] = Field(default_factory=list, max_length=5)
    claims: list[ClaimOut] = Field(default_factory=list, max_length=8)
    proposed_verdict: NoveltyCategory = Field(
        description="Your proposal only: code decides the verdict from the matrix and the coverage."
    )
    uncertainties: list[Text] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def _one_cell_per_pair(self) -> NoveltyAssessmentOutput:
        pairs = [(m.paper, m.facet) for m in self.facet_matches]
        repeated = sorted({f"{p}/facet {f}" for p, f in pairs if pairs.count((p, f)) > 1})
        if repeated:
            raise ValueError(f"facet_matches: one cell per (paper, facet); repeated: {', '.join(repeated)}")
        return self


# --- the revision after a Skeptic round (step 4.7b) -------------------------------------------------------

class ChallengeResponse(_Strict):
    challenge: Annotated[str, StringConstraints(pattern=r"^K\d{1,2}$")] = Field(description="e.g. 'K1'")
    response: Literal["accept", "rebut"]
    rebuttal: ClaimOut | None = Field(
        default=None,
        description=("For rebut: a claim that answers the challenge, backed by quotes from the papers in the input. "
                     "An opinion without a verified quote doesn't count: the challenge then stays unresolved."),
    )

    @model_validator(mode="after")
    def _rebuttal_for_rebut(self) -> ChallengeResponse:
        if self.response == "rebut" and (self.rebuttal is None or not self.rebuttal.evidence):
            raise ValueError(f"{self.challenge}: a rebuttal needs a claim with quoted evidence")
        if self.response == "accept" and self.rebuttal is not None:
            raise ValueError(f"{self.challenge}: an accepted challenge has no rebuttal")
        return self


class NoveltyRevisionOutput(_Strict):
    """The Novelty Investigator's answer to a Skeptic round."""

    facet_matches: list[FacetMatchOut] = Field(
        default_factory=list, max_length=100,
        description="Cells for the papers marked new in the input (the ones the Skeptic found).",
    )
    responses: list[ChallengeResponse] = Field(default_factory=list, max_length=20)
    closest_works: list[ClosestWork] = Field(default_factory=list, max_length=5)
    proposed_verdict: NoveltyCategory
    uncertainties: list[Text] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def _one_each(self) -> NoveltyRevisionOutput:
        pairs = [(m.paper, m.facet) for m in self.facet_matches]
        if len(set(pairs)) != len(pairs):
            raise ValueError("facet_matches: one cell per (paper, facet)")
        answered = [r.challenge for r in self.responses]
        if len(set(answered)) != len(answered):
            raise ValueError("responses: one response per challenge")
        return self
