"""The Research Proposal Designer (agent A7; doc 04; role proposal; step 7).

design_proposal() makes one structured call (effort `medium`, up to 20k output tokens): everything the evaluation
found in, the written sections of the research package out (src/ai/schemas/proposal.py). Checking the statements'
sources and evidence status, rendering the other sections and storing the package are code
(src/ai/workflows/proposal.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.ai.context import ProposalRequest, render_proposal
from src.ai.prompts import system_prompt
from src.ai.schemas.proposal import ProposalOutput
from src.config import RoleLlmSettings
from src.llm import LlmCallRecord, run_structured

ROLE = "proposal"
PROMPT_CACHE = False  # one call per package


@dataclass(frozen=True)
class ProposalReading:
    output: ProposalOutput
    records: list[LlmCallRecord]
    prompt_version: str


async def design_proposal(
    request: ProposalRequest,
    *,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> ProposalReading:
    """The research package's written sections. Raises LlmError subclasses."""
    system = system_prompt(ROLE)
    result = await run_structured(
        ROLE, system=system.text, prompt_version=system.version_id, user_message=render_proposal(request),
        output_schema=ProposalOutput, settings=settings, model=model, cache=PROMPT_CACHE,
    )
    return ProposalReading(output=result.output, records=result.records, prompt_version=system.version_id)
