"""The opportunity miner (role opportunity_mining; Mode 2, step 5.2b; doc 06 §4).

Two small structured calls, normally on a small model (EUREKA_LLM_MODEL_OPPORTUNITY_MINING,
e.g. claude-haiku-5-5; effort `low`):
  group_statements()      groups future-work statements and findings by the open problem
                          they point at (prompt opportunity_mining_grouping);
  check_contradictions()  says whether each suspected pair of findings really disagrees
                          (prompt opportunity_mining_contradictions).

Code checks every answer: labels are mapped back to the input, unknown labels are ignored,
a statement counts in its first group only, and groups left with fewer than two statements
are dropped. Everything that makes an item trustworthy (paper and author-group counts,
which pairs are checked) is decided by code in src/ai/workflows/mining.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.ai.context import (
    StatementForMining,
    render_contradiction_pairs,
    render_mining_items,
)
from src.ai.prompts import system_prompt
from src.ai.schemas.mining import ContradictionOutput, GroupingOutput, PairCheck
from src.config import RoleLlmSettings
from src.llm import LlmCallRecord, run_structured

ROLE = "opportunity_mining"
PROMPT_CACHE = False  # one call per task per investigation: nothing to reuse


@dataclass(frozen=True)
class Grouping:
    groups: list[tuple[str, list[str]]]
    """(label, statement labels), in the model's order; each statement in one group at most."""
    records: list[LlmCallRecord]
    prompt_version: str


@dataclass(frozen=True)
class PairChecks:
    checks: dict[str, PairCheck]
    """pair label -> check, for every pair that was checked."""
    records: list[LlmCallRecord]
    prompt_version: str


async def group_statements(
    subtopic_names: tuple[str, ...],
    items: list[StatementForMining],
    *,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> Grouping:
    """Groups the statements by shared open problem. Raises LlmError subclasses."""
    system = system_prompt(ROLE, task="grouping")
    result = await run_structured(
        ROLE, system=system.text, prompt_version=system.version_id,
        user_message=render_mining_items(subtopic_names, tuple(items)),
        output_schema=GroupingOutput, settings=settings, model=model, cache=PROMPT_CACHE,
    )
    known = {item.label for item in items}
    used: set[str] = set()
    groups = []
    for group in result.output.groups:
        members = []
        for label in group.items:
            if label in known and label not in used:
                members.append(label)
                used.add(label)
        if len(members) >= 2:
            groups.append((group.label, members))
    return Grouping(groups=groups, records=result.records, prompt_version=system.version_id)


async def check_contradictions(
    pairs: list[tuple[str, StatementForMining, StatementForMining]],
    *,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> PairChecks:
    """Checks each (label, finding, finding) pair. Raises LlmError subclasses."""
    system = system_prompt(ROLE, task="contradictions")
    result = await run_structured(
        ROLE, system=system.text, prompt_version=system.version_id,
        user_message=render_contradiction_pairs(tuple(pairs)),
        output_schema=ContradictionOutput, settings=settings, model=model, cache=PROMPT_CACHE,
    )
    known = {label for label, _, _ in pairs}
    checks: dict[str, PairCheck] = {}
    for check in result.output.checks:
        if check.pair in known and check.pair not in checks:
            checks[check.pair] = check
    return PairChecks(checks=checks, records=result.records, prompt_version=system.version_id)
