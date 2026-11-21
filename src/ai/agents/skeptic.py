"""The Skeptic (agent A5, doc 04): tries to disprove the idea's novelty and significance.

Code-driven rounds (step 4.7, a deliberate change from the tool-using agent first sketched
in doc 04): the Skeptic decides, code acts.

  plan_attack()       one call: 4-8 adversarial searches (other terminology, older work,
                      adjacent fields, other methods, follow-ups of the closest works)
  write_challenges()  one call: challenges against the analysis's claims, quoting the new
                      papers the round found

Its input is deliberately limited for independence: the candidate, the Novelty
Investigator's claims and verdict, and the titles already examined, never its reasoning.
Code checks every challenge: unknown claim or paper labels are dropped, every quote is
verified against the stored text (the same check as the Novelty Investigator's), and a
challenge counts as evidence only with at least one verified quote; otherwise it is the
Skeptic's interpretation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.ai.agents.novelty_investigator import CheckedQuote, check_quote
from src.ai.context import (
    CandidateForPlanning,
    ClaimForSkeptic,
    PaperForNovelty,
    render_skeptic_challenges,
    render_skeptic_queries,
)
from src.ai.prompts import system_prompt
from src.ai.schemas.skeptic import ChallengeSetOutput, SkepticQueriesOutput
from src.config import RoleLlmSettings
from src.llm import LlmCallRecord, run_structured

ROLE = "skeptic"
PROMPT_CACHE = False


@dataclass(frozen=True)
class AttackPlan:
    output: SkepticQueriesOutput
    records: list[LlmCallRecord]
    prompt_version: str


@dataclass(frozen=True)
class CheckedChallenge:
    target_claim: str | None
    type: str
    argument: str
    evidence: list[tuple[str, CheckedQuote]]
    """(paper label, checked quote)."""
    same_on_facets: list[int]
    statement_type: str
    """evidence (>= 1 verified quote) or interpretation."""

    @property
    def papers(self) -> list[str]:
        return list(dict.fromkeys(label for label, _ in self.evidence))


@dataclass(frozen=True)
class ChallengeReading:
    challenges: list[CheckedChallenge]
    dropped: list[str] = field(default_factory=list)
    records: list[LlmCallRecord] = field(default_factory=list)
    prompt_version: str = ""


async def plan_attack(
    candidate: CandidateForPlanning,
    claims: tuple[ClaimForSkeptic, ...],
    verdict: str,
    examined: tuple[str, ...],
    *,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> AttackPlan:
    system = system_prompt(ROLE, task="queries")
    result = await run_structured(
        ROLE, system=system.text, prompt_version=system.version_id,
        user_message=render_skeptic_queries(candidate, claims, verdict, examined),
        output_schema=SkepticQueriesOutput, settings=settings, model=model, cache=PROMPT_CACHE,
    )
    return AttackPlan(result.output, result.records, system.version_id)


def check_challenges(
    output: ChallengeSetOutput,
    candidate: CandidateForPlanning,
    claims: tuple[ClaimForSkeptic, ...],
    papers: tuple[PaperForNovelty, ...],
) -> tuple[list[CheckedChallenge], list[str]]:
    by_label = {paper.label: paper for paper in papers}
    claim_labels = {claim.label for claim in claims}
    facet_numbers = {facet.number for facet in candidate.facets}
    checked, dropped = [], []
    for challenge in output.challenges:
        target = challenge.target_claim
        if target is not None and target not in claim_labels:
            dropped.append(f"unknown claim {target}")
            target = None
        evidence = []
        for item in challenge.evidence:
            if item.paper not in by_label:
                dropped.append(f"evidence from unknown paper {item.paper}")
                continue
            evidence.append((item.paper, check_quote(item.quote, by_label[item.paper])))
        facets = sorted(n for n in set(challenge.same_on_facets) if n in facet_numbers)
        verified = any(quote.verified for _, quote in evidence)
        checked.append(CheckedChallenge(target, challenge.type, challenge.argument, evidence, facets,
                                        "evidence" if verified else "interpretation"))
    return checked, dropped


async def write_challenges(
    candidate: CandidateForPlanning,
    claims: tuple[ClaimForSkeptic, ...],
    verdict: str,
    papers: tuple[PaperForNovelty, ...],
    *,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> ChallengeReading:
    """Challenges from the round's new papers. Raises ValueError (no papers) or LlmError subclasses."""
    system = system_prompt(ROLE, task="challenges")
    result = await run_structured(
        ROLE, system=system.text, prompt_version=system.version_id,
        user_message=render_skeptic_challenges(candidate, claims, verdict, papers),
        output_schema=ChallengeSetOutput, settings=settings, model=model, cache=PROMPT_CACHE,
    )
    checked, dropped = check_challenges(result.output, candidate, claims, papers)
    return ChallengeReading(checked, dropped, result.records, system.version_id)
