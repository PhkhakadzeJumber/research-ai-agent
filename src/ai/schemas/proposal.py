"""The Proposal Designer's output (agent A7; doc 04, doc 02 §5, doc 10 §7.7; step 7).

A7 writes the sections of the research package that need writing: the title, problem and background, motivation,
potential contribution, methodology, hypotheses, tools, experiment design, limitations and risks, and the one
paragraph that explains the recommendation. The other sections (research question, novelty, closest works,
evidence, datasets, feasibility, references, the recommendation itself) are rendered by code from the evaluation.

Every statement says what it is (evidence / interpretation / hypothesis / to_verify) and cites its sources only by
the labels in the input (C3 for a claim, P2 for a paper). What the schema can't check, code does: unknown labels are
dropped, and an "evidence" statement without a source backed by a verified quote becomes "to_verify".
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SCHEMA_VERSION = "proposal-v1"
MAX_STATEMENTS = 12

StatementType = Literal["evidence", "interpretation", "hypothesis", "to_verify"]
SourceLabel = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[CP]\d{1,3}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Statement(_Strict):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)] = Field(
        description="One statement. Refer to papers only by their labels (P2), never by title or author."
    )
    type: StatementType = Field(
        description=("evidence: a claim or paper in the input states it (cite it); interpretation: your reasoning from "
                     "the evidence; hypothesis: an expectation the study tests; to_verify: needed, but not in the "
                     "input: it must be checked before it is relied on.")
    )
    sources: list[SourceLabel] = Field(default_factory=list, max_length=6,
                                       description="Claim (C3) and paper (P2) labels from the input. Required for evidence.")


Statements = Annotated[list[Statement], Field(max_length=MAX_STATEMENTS)]


class ProposalOutput(_Strict):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
    background: Statements = Field(description="The research problem and its background, from the evidence.")
    motivation: Statements = Field(description="Why it matters, and for whom.")
    contribution: Statements = Field(description="What it would add, bounded by the novelty verdict.")
    methodology: Statements = Field(
        description="How the study is done. Reuse methods from the cited papers and say where they come from.")
    hypotheses: Statements = Field(description="Testable expectations, each typed 'hypothesis'. Empty if none fit.")
    tools: Statements = Field(description="Tools, libraries, models and compute the study uses.")
    experiment_design: Statements = Field(
        description=("Baselines, comparisons and metrics, reusing those of the cited papers wherever possible "
                     "(cite them)."))
    limitations: Statements = Field(
        description="Limitations and risks, including the Skeptic's unresolved challenges and the feasibility risks.")
    # 2000: a sanity cap, not a style limit (the step 7 live run wrote a careful 800+ character paragraph, and the
    # 800 cap forced a paid rewrite)
    recommendation_explanation: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                                                 max_length=2000)] = Field(
        description="One paragraph explaining the recommendation the verdict engine gave. Don't change it."
    )
