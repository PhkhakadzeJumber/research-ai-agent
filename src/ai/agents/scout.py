"""The Research Opportunity Scout (agent A2; doc 04). Task discover (Mode 2, step 5.3).

discover() makes one structured call (role scout, effort `high`, up to 20k output tokens):
the field request plus the mined material in, a CandidateSet (src/ai/schemas/scout.py) out.
The schema enforces the facet rules, at least two inspirations per candidate and at least
three origin strategies; a broken answer goes back to the model once (run_structured).

The Scout only proposes. Mapping its labels to quotes and opportunity items, the grounding
rule and storing the candidates are code (src/ai/workflows/scouting.py). The tasks
alternatives, refine and replenish come with step 5.5.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.ai.context import (
    FieldRequest,
    ItemForScout,
    PaperForScout,
    StatementForScout,
    render_scout_discover,
)
from src.ai.prompts import system_prompt
from src.ai.schemas.scout import ScoutOutput
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
