"""The relevance screener's output (role relevance_screening; doc 05 §11.4 step `screening`).

One batch of papers (labelled P1, P2, ... in the input) is judged against one research
idea: each paper gets a score and a one-line reason.

    3  direct prior work: the same problem with the same method or setting
    2  shares a key facet substantially (useful to read)
    1  tangential: related field, little to learn for this idea
    0  unrelated

The labels are local to the batch; code maps them back to paper IDs, so the model never
writes a paper ID or a title. Code also checks that every paper in the batch was judged
exactly once (missing ones are asked for again; extra or repeated labels are ignored).
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SCHEMA_VERSION = "screening-v1"
SCORES = (0, 1, 2, 3)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Judgement(_Strict):
    label: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^P\d{1,3}$")] = Field(
        description="The paper's label exactly as in the input, e.g. 'P7'."
    )
    score: Annotated[int, Field(ge=0, le=3)] = Field(
        description=("3 = same problem with the same method or setting (direct prior work); 2 = shares a key facet "
                     "substantially; 1 = tangential; 0 = unrelated.")
    )
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] = Field(
        description="One short line naming what the paper shares with the idea, or why it doesn't."
    )


class ScreeningOutput(_Strict):
    """A relevance judgement for every paper in the batch."""

    judgements: list[Judgement] = Field(min_length=1, max_length=60)
