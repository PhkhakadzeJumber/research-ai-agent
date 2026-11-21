"""The Research Opportunity Scout (agent A2; doc 04). Task discover (Mode 2, step 5.3).

discover() makes one structured call (role scout, effort `high`, up to 20k output tokens):
the field request plus the mined material in, a CandidateSet (src/ai/schemas/scout.py) out.
The schema enforces the facet rules, at least two inspirations per candidate and at least
three origin strategies; a broken answer goes back to the model once (run_structured).

The Scout only proposes. Mapping its labels to quotes and opportunity items, the grounding
rule and storing the candidates are code (src/ai/workflows/scouting.py). refine() writes the next
version of one candidate that triage sent back (step 5.5a; stored by src/ai/workflows/refining.py).
replenish() (Mode 2) and alternatives() (Mode 1) write a few more candidates (step 5.5b; stored
by src/ai/workflows/scouting.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.ai.context import (
    FieldRequest,
    ItemForScout,
    PaperForScout,
    RefineRequest,
    StatementForScout,
    render_scout_discover,
    render_scout_refine,
)
from src.ai.prompts import system_prompt
from src.ai.schemas.scout import AlternativesOutput, RefineOutput, ReplenishOutput, ScoutOutput
from src.config import RoleLlmSettings
from src.llm import LlmCallRecord, run_structured

ROLE = "scout"


@dataclass(frozen=True)
class ScoutResult:
    output: ScoutOutput
    records: list[LlmCallRecord]
    prompt_version: str


async def discover(
    request: FieldRequest,
    items: list[ItemForScout],
    statements: list[StatementForScout],
    papers: list[PaperForScout],
    *,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> ScoutResult:
    """Proposes 8-12 grounded candidates. Raises LlmError subclasses."""
    system = system_prompt(ROLE, task="discover")
    result = await run_structured(
        ROLE, system=system.text, prompt_version=system.version_id,
        user_message=render_scout_discover(request, tuple(items), tuple(statements), tuple(papers)),
        output_schema=ScoutOutput, settings=settings, model=model,
    )
    return ScoutResult(output=result.output, records=result.records, prompt_version=system.version_id)


@dataclass(frozen=True)
class RefineResult:
    output: RefineOutput
    records: list[LlmCallRecord]
    prompt_version: str


async def refine(
    request: RefineRequest,
    *,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> RefineResult:
    """The next version of one candidate, answering its triage reason (task refine, step 5.5a).
    Raises LlmError subclasses."""
    system = system_prompt(ROLE, task="refine")
    result = await run_structured(
        ROLE, system=system.text, prompt_version=system.version_id,
        user_message=render_scout_refine(request), output_schema=RefineOutput, settings=settings, model=model,
    )
    return RefineResult(output=result.output, records=result.records, prompt_version=system.version_id)


@dataclass(frozen=True)
class SetResult:
    output: ReplenishOutput | AlternativesOutput
    records: list[LlmCallRecord]
    prompt_version: str


async def replenish(
    request: FieldRequest,
    items: list[ItemForScout],
    statements: list[StatementForScout],
    papers: list[PaperForScout],
    tried: list[tuple[str, str, str]],
    *,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> SetResult:
    """3-6 new ideas from the same material, avoiding what was tried (Mode 2, step 5.5b). Raises LlmError."""
    system = system_prompt(ROLE, task="replenish")
    result = await run_structured(
        ROLE, system=system.text, prompt_version=system.version_id,
        user_message=render_scout_discover(request, tuple(items), tuple(statements), tuple(papers), tuple(tried)),
        output_schema=ReplenishOutput, settings=settings, model=model,
    )
    return SetResult(output=result.output, records=result.records, prompt_version=system.version_id)


async def alternatives(
    request: RefineRequest,
    *,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> SetResult:
    """3-5 alternatives to the user's rejected idea, from its evaluation's evidence (Mode 1, step 5.5b).
    The packet has the refine task's layout. Raises LlmError."""
    system = system_prompt(ROLE, task="alternatives")
    result = await run_structured(
        ROLE, system=system.text, prompt_version=system.version_id,
        user_message=render_scout_refine(request), output_schema=AlternativesOutput, settings=settings, model=model,
    )
    return SetResult(output=result.output, records=result.records, prompt_version=system.version_id)
