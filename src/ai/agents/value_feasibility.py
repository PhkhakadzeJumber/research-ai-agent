"""The Value & Feasibility Evaluator (agent A6, full task; doc 04): is the idea worth doing,
and can this user do it?

assess_value() makes one structured call (role `value_feasibility`, effort high) with the
candidate, the user's complexity and constraints, the analysis's claims, the Skeptic's
challenges with their resolution, and the relevant papers (P1, ...). Code then verifies
every significance quote against the paper it is attributed to (the same check as the
Novelty Investigator's); a significance claim is verified only with a verified quote.

Dataset availability and the timeline are checked by the workflow, which knows what was
read and what the user's constraints say (src/ai/workflows/value_feasibility.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.ai.agents.novelty_investigator import CheckedQuote, check_quote
from src.ai.context import (
    CandidateForPlanning,
    ChallengeSummary,
    ClaimForSkeptic,
    PaperForNovelty,
    render_value_feasibility,
)
from src.ai.prompts import system_prompt
from src.ai.schemas.value_feasibility import ValueFeasibilityOutput
from src.config import RoleLlmSettings
from src.llm import LlmCallRecord, run_structured

ROLE = "value_feasibility"
PROMPT_CACHE = False


@dataclass(frozen=True)
class CheckedSignificanceClaim:
    text: str
    evidence: list[tuple[str, CheckedQuote]]
    """(paper label, checked quote)."""

    @property
    def status(self) -> str:
        if any(quote.verified for _, quote in self.evidence):
            return "verified"
        return "unverified_quote" if self.evidence else "no_evidence"

    @property
    def verified_papers(self) -> list[str]:
        return list(dict.fromkeys(label for label, quote in self.evidence if quote.verified))


@dataclass(frozen=True)
class ValueReading:
    output: ValueFeasibilityOutput
    claims: list[CheckedSignificanceClaim]
    dropped: list[str] = field(default_factory=list)
    records: list[LlmCallRecord] = field(default_factory=list)
    prompt_version: str = ""


async def assess_value(
    candidate: CandidateForPlanning,
    *,
    complexity: str,
    constraints: tuple[tuple[str, str], ...],
    claims: tuple[ClaimForSkeptic, ...],
    challenges: tuple[ChallengeSummary, ...],
    papers: tuple[PaperForNovelty, ...],
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> ValueReading:
    """Significance and feasibility for this user. Raises LlmError subclasses."""
    system = system_prompt(ROLE)
    result = await run_structured(
        ROLE, system=system.text, prompt_version=system.version_id,
        user_message=render_value_feasibility(candidate, complexity=complexity, constraints=constraints,
                                              claims=claims, challenges=challenges, papers=papers),
        output_schema=ValueFeasibilityOutput, settings=settings, model=model, cache=PROMPT_CACHE,
    )
    by_label = {paper.label: paper for paper in papers}
    checked, dropped = [], []
    for claim in result.output.significance.claims:
        evidence = []
        for item in claim.evidence:
            if item.paper not in by_label:
                dropped.append(f"evidence from unknown paper {item.paper}")
                continue
            evidence.append((item.paper, check_quote(item.quote, by_label[item.paper])))
        checked.append(CheckedSignificanceClaim(claim.text, evidence))
    return ValueReading(result.output, checked, dropped, result.records, system.version_id)
