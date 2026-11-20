"""The Idea Analyst (agent A1, doc 04): turns a user's research idea into searchable structure.

Built so far: the Mode 1 tasks "structure idea" and "revise with answers". analyse_idea():
  1. renders the request as a context packet (src/ai/context.py render_idea);
  2. runs one structured call with the Idea Analyst prompt and the StructuredIdeaOutput
     schema (src/llm.py run_structured, effort from ROLE_DEFAULT_EFFORT: medium);
  3. returns an IdeaReading: the reading + versions + call records.

revise_idea() (step 4.3) gives A1 its earlier reading plus the user's answers to its
clarifying questions, and returns the corrected reading in the same shape (prompt
idea_analyst_revise). It's called only when the user answered something.

The facet rules live in the schema, so a broken answer gets one automatic repair attempt.
Field brief (Mode 2), query plan and candidate normalization come with later steps.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.ai.context import IdeaForAnalysis, QuestionAnswer, render_idea, render_revision
from src.ai.prompts import system_prompt
from src.ai.schemas.structured_idea import SCHEMA_VERSION, StructuredIdeaOutput
from src.config import RoleLlmSettings
from src.llm import LlmCallRecord, run_structured

ROLE = "idea_analyst"

# One call per idea, never repeated with the same prefix, so a cache entry would only add
# the write premium (same reasoning as the Paper Reader, step 4.0).
PROMPT_CACHE = False


@dataclass(frozen=True)
class IdeaReading:
    output: StructuredIdeaOutput
    schema_version: str
    prompt_version: str
    records: list[LlmCallRecord]

    @property
    def cost_usd(self) -> Decimal | None:
        costs = [record.cost_usd for record in self.records]
        return None if any(cost is None for cost in costs) else sum(costs, Decimal(0))


async def analyse_idea(
    request: IdeaForAnalysis, *, settings: RoleLlmSettings | None = None, model: Any | None = None
) -> IdeaReading:
    """Reads one idea. Raises ValueError (empty idea) or LlmError subclasses."""
    user_message = render_idea(request)
    system = system_prompt(ROLE)
    result = await run_structured(
        ROLE, system=system.text, prompt_version=system.version_id, user_message=user_message,
        output_schema=StructuredIdeaOutput, settings=settings, model=model, cache=PROMPT_CACHE,
    )
    return IdeaReading(output=result.output, schema_version=SCHEMA_VERSION, prompt_version=system.version_id,
                       records=result.records)


async def revise_idea(
    request: IdeaForAnalysis,
    reading: StructuredIdeaOutput,
    answers: tuple[QuestionAnswer, ...],
    *,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> IdeaReading:
    """The reading corrected with the user's answers. Raises ValueError (no answers) or LlmError."""
    user_message = render_revision(request, reading.model_dump_json(indent=1), answers)
    system = system_prompt(ROLE, task="revise")
    result = await run_structured(
        ROLE, system=system.text, prompt_version=system.version_id, user_message=user_message,
        output_schema=StructuredIdeaOutput, settings=settings, model=model, cache=PROMPT_CACHE,
    )
    # The user has had their chance to answer: a revised reading never asks again.
    output = result.output.model_copy(update={"clarifying_questions": []})
    return IdeaReading(output=output, schema_version=SCHEMA_VERSION, prompt_version=system.version_id,
                       records=result.records)
