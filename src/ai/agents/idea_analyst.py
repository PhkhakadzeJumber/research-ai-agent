"""The Idea Analyst (agent A1, doc 04): turns a user's research idea into searchable structure.

Built so far: the Mode 1 tasks "structure idea" and "revise with answers". analyse_idea():
  1. renders the request as a context packet (src/ai/context.py render_idea);
  2. runs one structured call with the Idea Analyst prompt and the StructuredIdeaOutput
     schema (src/llm.py run_structured, effort from ROLE_DEFAULT_EFFORT: medium);
  3. returns an IdeaReading: the reading + versions + call records.

revise_idea() (step 4.3) gives A1 its earlier reading plus the user's answers to its
clarifying questions, and returns the corrected reading in the same shape (prompt
idea_analyst_revise). It's called only when the user answered something.

plan_queries() (step 4.4) writes the searches for one candidate (prompt
idea_analyst_query_plan). Code checks the quotas (src/ai/schemas/query_plan.py); a plan
that misses them is regenerated once with the exact shortfalls. If the second plan still
misses some, it is used anyway and the shortfalls are returned (recorded as a degraded step).

analyse_field() and revise_field() (step 5.1, Mode 2) write and revise the field brief:
4-8 subtopics with landscape searches. Code checks the user's exclusions; a brief that
touches an excluded topic is rewritten once, told exactly what to fix.

The facet rules live in the schema, so a broken answer gets one automatic repair attempt.
Candidate normalization comes with a later step.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.ai.context import (
    CandidateForPlanning,
    FieldRequest,
    IdeaForAnalysis,
    QuestionAnswer,
    render_field_request,
    render_field_revision,
    render_idea,
    render_query_task,
    render_revision,
)
from src.ai.prompts import system_prompt
from src.ai.schemas import field_brief, query_plan
from src.ai.schemas.field_brief import FieldBriefOutput, exclusion_problems
from src.ai.schemas.query_plan import QUERY_COUNTS, FacetRef, QueryPlanOutput, check_quotas, required_variants
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



@dataclass(frozen=True)
class QueryPlanReading:
    output: QueryPlanOutput
    schema_version: str
    prompt_version: str
    records: list[LlmCallRecord]
    problems: list[str]
    """Quota shortfalls left after the regeneration (empty normally)."""
    regenerated: bool = False
    """True if the first plan missed the quotas and a second one was written."""


def quota_task(candidate: CandidateForPlanning, depth: str) -> str:
    """The count and quotas, stated for the model (the same numbers check_quotas() enforces)."""
    low, high = QUERY_COUNTS[depth]
    keys = [facet for facet in candidate.facets if facet.is_key]
    variants = required_variants(len(keys), depth)
    parts = [f"Write {low}-{high} queries.",
             f"At least {query_plan.MIN_WITHOUT_METHOD} must be 'without_method' queries that cover no method facet."]
    if keys:
        numbers = ", ".join(str(facet.number) for facet in keys)
        parts.append(f"For each key facet ({numbers}), at least {variants} 'terminology_variant' queries that "
                     "list that facet.")
    return " ".join(parts)


async def plan_queries(
    candidate: CandidateForPlanning,
    *,
    depth: str,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> QueryPlanReading:
    """A query plan meeting the quotas (regenerated once if needed). Raises LlmError subclasses."""
    system = system_prompt(ROLE, task="query_plan")
    facets = [FacetRef(f.number, f.kind, f.is_key) for f in candidate.facets]
    task = quota_task(candidate, depth)
    message = render_query_task(candidate, task=task)
    records: list[LlmCallRecord] = []

    result = await run_structured(
        ROLE, system=system.text, prompt_version=system.version_id, user_message=message,
        output_schema=QueryPlanOutput, settings=settings, model=model, cache=PROMPT_CACHE,
    )
    records.extend(result.records)
    plan, problems = result.output, check_quotas(result.output, facets, depth)
    regenerated = bool(problems)

    if problems:  # one regeneration, naming exactly what was missing
        retry = (f"{message}\n<previous_plan>\n{plan.model_dump_json(indent=1)}\n</previous_plan>\n"
                 f"<problems>Your previous plan missed: {'; '.join(problems)}. Write a complete new plan that "
                 "fixes these and keeps what was good.</problems>")
        result = await run_structured(
            ROLE, system=system.text, prompt_version=system.version_id, user_message=retry,
            output_schema=QueryPlanOutput, settings=settings, model=model, cache=PROMPT_CACHE,
        )
        records.extend(result.records)
        plan, problems = result.output, check_quotas(result.output, facets, depth)

    return QueryPlanReading(output=plan, schema_version=query_plan.SCHEMA_VERSION, prompt_version=system.version_id,
                            records=records, problems=problems, regenerated=regenerated)



# --- the field brief (Mode 2, step 5.1) -------------------------------------------------------------------

@dataclass(frozen=True)
class FieldReading:
    output: FieldBriefOutput
    schema_version: str
    prompt_version: str
    records: list[LlmCallRecord]
    problems: list[str]
    """Exclusion violations left after the rewrite (empty normally)."""


async def analyse_field(
    request: FieldRequest, *, settings: RoleLlmSettings | None = None, model: Any | None = None
) -> FieldReading:
    """The field brief for a Mode 2 request. Raises ValueError (empty field) or LlmError subclasses."""
    system = system_prompt(ROLE, task="field_brief")
    message = render_field_request(request)
    result = await run_structured(
        ROLE, system=system.text, prompt_version=system.version_id, user_message=message,
        output_schema=FieldBriefOutput, settings=settings, model=model, cache=PROMPT_CACHE,
    )
    records = list(result.records)
    brief, problems = result.output, exclusion_problems(result.output, request.exclusions)
    if problems:  # one rewrite, naming exactly what touched an exclusion
        retry = (f"{message}\n<previous_brief>\n{brief.model_dump_json(indent=1)}\n</previous_brief>\n"
                 f"<problems>Your previous brief touched the user's exclusions: {'; '.join(problems)}. "
                 "Write a complete new brief that avoids them entirely.</problems>")
        result = await run_structured(
            ROLE, system=system.text, prompt_version=system.version_id, user_message=retry,
            output_schema=FieldBriefOutput, settings=settings, model=model, cache=PROMPT_CACHE,
        )
        records.extend(result.records)
        brief, problems = result.output, exclusion_problems(result.output, request.exclusions)
    return FieldReading(brief, field_brief.SCHEMA_VERSION, system.version_id, records, problems)


async def revise_field(
    request: FieldRequest,
    brief: FieldBriefOutput,
    answers: tuple[QuestionAnswer, ...],
    *,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> FieldReading:
    """The brief corrected with the user's answers (no new questions)."""
    system = system_prompt(ROLE, task="field_brief_revise")
    result = await run_structured(
        ROLE, system=system.text, prompt_version=system.version_id,
        user_message=render_field_revision(request, brief.model_dump_json(indent=1), answers),
        output_schema=FieldBriefOutput, settings=settings, model=model, cache=PROMPT_CACHE,
    )
    output = result.output.model_copy(update={"clarifying_questions": []})
    return FieldReading(output, field_brief.SCHEMA_VERSION, system.version_id, list(result.records),
                        exclusion_problems(output, request.exclusions))
