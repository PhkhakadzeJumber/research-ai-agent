"""The relevance screener (role relevance_screening; doc 05 §11.4 step `screening`).

screen_batch() judges one batch of papers (up to ~25) against one candidate in a single
structured call. The model and effort come from the role's settings: by default the
project model at effort `low`, normally overridden with a small model
(EUREKA_LLM_MODEL_RELEVANCE_SCREENING, e.g. claude-haiku-5-5), because screening is a
simple judgement made hundreds of times per evaluation.

Code checks the answer: every label judged exactly once. Papers the model skipped are
asked for once more in a second, smaller call; anything still missing is returned as
`missing` (the caller leaves those papers unscreened). Unknown or repeated labels are
ignored; the first judgement of a label wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.ai.context import CandidateForPlanning, PaperForScreening, render_screening_batch
from src.ai.prompts import system_prompt
from src.ai.schemas.screening import Judgement, ScreeningOutput
from src.config import RoleLlmSettings
from src.llm import LlmCallRecord, run_structured

ROLE = "relevance_screening"
BATCH_SIZE = 25

# Each batch is a different list of papers; only the short system prompt repeats, below
# the size Claude caches (same reasoning as the Paper Reader, step 4.0).
PROMPT_CACHE = False


@dataclass(frozen=True)
class BatchScreening:
    judgements: dict[str, Judgement]
    """label -> judgement, for every paper that was judged."""
    missing: list[str]
    """Labels still unjudged after the second call."""
    records: list[LlmCallRecord]
    prompt_version: str

    @property
    def cost_usd(self) -> Decimal | None:
        costs = [record.cost_usd for record in self.records]
        return None if any(cost is None for cost in costs) else sum(costs, Decimal(0))


async def _judge(candidate, papers, system, settings, model) -> tuple[dict[str, Judgement], list[LlmCallRecord]]:
    result = await run_structured(
        ROLE, system=system.text, prompt_version=system.version_id,
        user_message=render_screening_batch(candidate, tuple(papers)),
        output_schema=ScreeningOutput, settings=settings, model=model, cache=PROMPT_CACHE,
    )
    asked = {paper.label for paper in papers}
    judged: dict[str, Judgement] = {}
    for judgement in result.output.judgements:
        if judgement.label in asked and judgement.label not in judged:
            judged[judgement.label] = judgement
    return judged, result.records


async def screen_batch(
    candidate: CandidateForPlanning,
    papers: list[PaperForScreening],
    *,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> BatchScreening:
    """Scores every paper in the batch (0-3, with a reason). Raises LlmError subclasses."""
    labels = [paper.label for paper in papers]
    if len(set(labels)) != len(labels):
        raise ValueError("Paper labels in a screening batch must be unique.")
    system = system_prompt(ROLE)
    judged, records = await _judge(candidate, papers, system, settings, model)

    skipped = [paper for paper in papers if paper.label not in judged]
    if skipped:  # one more call for the papers the model left out
        more, more_records = await _judge(candidate, skipped, system, settings, model)
        judged.update(more)
        records = [*records, *more_records]

    missing = [label for label in labels if label not in judged]
    return BatchScreening(judgements=judged, missing=missing, records=records, prompt_version=system.version_id)
