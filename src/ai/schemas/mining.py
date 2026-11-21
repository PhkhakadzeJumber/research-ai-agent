"""The opportunity miner's outputs (role opportunity_mining; Mode 2, step 5.2b).

Two small judgements over the landscape's verified statements; code does everything else.

GroupingOutput (task: grouping). The input lists statements labelled M1, M2, ...: future
work the papers propose, and findings where something failed or was mixed. The model
groups statements that point at the same open problem and names each group. Code then
counts papers and author groups and keeps only recurring problems (doc 06 §4).

ContradictionOutput (task: contradictions). The input lists pairs X1, X2, ... of findings
from different papers that code suspects disagree (same group, opposite directions). The
model says whether each pair is a real contradiction on the same question.

Labels are local to one call; code maps them back to quote and paper IDs, so the model never
writes an ID, a title or a quote. Unknown or repeated labels are ignored.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SCHEMA_VERSION = "mining-v1"
MAX_GROUPS = 30


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]


class ProblemGroup(_Strict):
    label: Label = Field(
        description=("The open problem the statements share, as a short phrase, e.g. 'Tool calling degrades in "
                     "non-English prompts'. Name the problem, not the papers.")
    )
    items: list[Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^M\d{1,4}$")]] = Field(
        min_length=2, description="The labels of the statements in the group, exactly as in the input, e.g. 'M7'."
    )


class GroupingOutput(_Strict):
    groups: list[ProblemGroup] = Field(
        max_length=MAX_GROUPS,
        description="The groups. A statement belongs to at most one group; vague statements can be left out.",
    )


class PairCheck(_Strict):
    pair: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^X\d{1,3}$")] = Field(
        description="The pair's label exactly as in the input, e.g. 'X2'."
    )
    contradiction: bool = Field(
        description=("True only if both findings answer the same question in a comparable setting and their "
                     "results disagree. Different settings, models or languages explain most apparent conflicts.")
    )
    shared_question: Label = Field(description="The question both findings answer, as one short phrase.")
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)] = Field(
        description="Why they do or don't contradict each other, in one or two sentences."
    )


class ContradictionOutput(_Strict):
    checks: list[PairCheck] = Field(description="One check per pair in the input.")
