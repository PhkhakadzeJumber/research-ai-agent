"""The quick feasibility check (agent A6, task quick feasibility; doc 04; role feasibility_quick).

check_candidates() judges every candidate waiting for pre-screening in ONE structured call
(effort `medium`, doc 04): cheaper than one call per candidate, and the candidates are
short. Code checks the answer: unknown labels are ignored, the first check of a label wins,
and a candidate the model skipped is reported as missing (the caller treats it as unclear).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.ai.context import CandidateForQuickCheck, FieldRequest, render_quick_feasibility
from src.ai.prompts import system_prompt
from src.ai.schemas.quick_feasibility import QuickCheck, QuickFeasibilityOutput
from src.config import RoleLlmSettings
from src.llm import LlmCallRecord, run_structured

ROLE = "feasibility_quick"
PROMPT_CACHE = False  # one call per pre-screening


@dataclass(frozen=True)
class QuickChecks:
    checks: dict[str, QuickCheck]
    """candidate label -> its check."""
    missing: list[str]
    records: list[LlmCallRecord]
    prompt_version: str


async def check_candidates(
    request: FieldRequest,
    candidates: list[CandidateForQuickCheck],
    *,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> QuickChecks:
    """Raises LlmError subclasses."""
    system = system_prompt(ROLE)
    result = await run_structured(
        ROLE, system=system.text, prompt_version=system.version_id,
        user_message=render_quick_feasibility(request, tuple(candidates)),
        output_schema=QuickFeasibilityOutput, settings=settings, model=model, cache=PROMPT_CACHE,
    )
    labels = [candidate.label for candidate in candidates]
    checks: dict[str, QuickCheck] = {}
    for check in result.output.checks:
        if check.candidate in labels and check.candidate not in checks:
            checks[check.candidate] = check
    return QuickChecks(checks=checks, missing=[label for label in labels if label not in checks],
                       records=result.records, prompt_version=system.version_id)
