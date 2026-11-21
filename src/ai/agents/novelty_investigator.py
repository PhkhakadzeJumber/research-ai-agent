"""The Novelty Investigator (agent A4, doc 04): how much of the idea already exists.

assess_novelty() makes one structured call (role `novelty`, effort high) with the
candidate, the closest papers (labelled P1, P2, ...) and a coverage note, then checks the
answer in code:
  - every quote is verified against the stored text of the paper it is attributed to
    (abstract first, then full-text chunks; src/ai/evidence/verifier.py match_quote), and
    the chunk it was found in is recorded;
  - matrix cells and evidence that name an unknown paper label or facet number are
    dropped (and counted);
  - claim status is computed here, never taken from the model: verified if at least one
    supporting or qualifying quote is verified; unverified_quote if quotes were given but
    none verified; no_evidence if none were given.

revise_novelty() (step 4.7b) answers a Skeptic round: cells for the papers the Skeptic
found, and per challenge accept or rebut. A rebuttal is a claim checked like any other:
it only counts (code makes the challenge `rebutted`) if it has a verified quote.

The verdict is not decided here: src/ai/evidence/verdicts.py does that from the matrix
and the coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from src.ai.context import (
    CandidateForPlanning,
    ChallengeForRevision,
    ClaimForSkeptic,
    PaperForNovelty,
    render_novelty_revision,
    render_novelty_task,
)
from src.ai.evidence.verifier import QuoteMatch, match_quote
from src.ai.prompts import system_prompt
from src.ai.schemas.novelty import ClaimOut, FacetMatchOut, NoveltyAssessmentOutput, NoveltyRevisionOutput
from src.config import RoleLlmSettings
from src.llm import LlmCallRecord, run_structured

ROLE = "novelty"
PROMPT_CACHE = False  # one call per evaluation; nothing repeats


@dataclass(frozen=True)
class CheckedQuote:
    text: str
    verified: bool
    score: float
    chunk_id: str | None
    """The chunk the quote was found in; None = the abstract (or not found)."""
    reason: str | None


@dataclass(frozen=True)
class CheckedMatch:
    paper: str
    facet: int
    overlap: str
    rationale: str
    quote: CheckedQuote | None


@dataclass(frozen=True)
class CheckedEvidence:
    paper: str
    quote: CheckedQuote
    stance: str


@dataclass(frozen=True)
class CheckedClaim:
    kind: str
    text: str
    statement_type: str
    inferred: bool
    evidence: list[CheckedEvidence]
    status: str
    """Set by code: verified / unverified_quote / no_evidence."""


@dataclass(frozen=True)
class NoveltyReading:
    output: NoveltyAssessmentOutput
    matches: list[CheckedMatch]
    claims: list[CheckedClaim]
    dropped: list[str] = field(default_factory=list)
    """Cells and evidence that named an unknown paper or facet (the model's mistakes)."""
    records: list[LlmCallRecord] = field(default_factory=list)
    prompt_version: str = ""

    @property
    def cost_usd(self) -> Decimal | None:
        costs = [record.cost_usd for record in self.records]
        return None if any(cost is None for cost in costs) else sum(costs, Decimal(0))


def check_quote(text: str, paper: PaperForNovelty) -> CheckedQuote:
    """Verifies a quote against the paper's abstract, then each of its chunks."""
    best: tuple[QuoteMatch, str | None] = (QuoteMatch(False, 0.0, "none", "the paper has no text"), None)
    sources = [(paper.abstract, None), *((chunk.text, chunk.chunk_id) for chunk in paper.chunks)]
    for source, chunk_id in sources:
        if not source:
            continue
        match = match_quote(text, source)
        if match.verified:
            return CheckedQuote(text, True, match.score, chunk_id, None)
        if match.score >= best[0].score:
            best = (match, chunk_id)
    match, _ = best
    return CheckedQuote(text, False, match.score, None, match.reason)


def claim_status(evidence: list[CheckedEvidence]) -> str:
    if any(e.quote.verified and e.stance in ("supports", "qualifies") for e in evidence):
        return "verified"
    return "unverified_quote" if evidence else "no_evidence"


def check_cells(cells: list[FacetMatchOut], candidate: CandidateForPlanning, by_label: dict[str, PaperForNovelty],
                dropped: list[str]) -> list[CheckedMatch]:
    facet_numbers = {facet.number for facet in candidate.facets}
    matches = []
    for cell in cells:
        if cell.paper not in by_label or cell.facet not in facet_numbers:
            dropped.append(f"matrix cell {cell.paper}/facet {cell.facet}")
            continue
        quote = check_quote(cell.quote, by_label[cell.paper]) if cell.quote else None
        matches.append(CheckedMatch(cell.paper, cell.facet, cell.overlap, cell.rationale, quote))
    return matches


def check_claim(claim: ClaimOut, by_label: dict[str, PaperForNovelty], dropped: list[str]) -> CheckedClaim:
    evidence = []
    for item in claim.evidence:
        if item.paper not in by_label:
            dropped.append(f"evidence from unknown paper {item.paper}")
            continue
        evidence.append(CheckedEvidence(item.paper, check_quote(item.quote, by_label[item.paper]), item.stance))
    return CheckedClaim(claim.kind, claim.text, claim.statement_type, claim.inferred, evidence, claim_status(evidence))


def check_assessment(
    output: NoveltyAssessmentOutput, candidate: CandidateForPlanning, papers: tuple[PaperForNovelty, ...]
) -> tuple[list[CheckedMatch], list[CheckedClaim], list[str]]:
    by_label = {paper.label: paper for paper in papers}
    dropped: list[str] = []
    matches = check_cells(output.facet_matches, candidate, by_label, dropped)
    claims = [check_claim(claim, by_label, dropped) for claim in output.claims]
    return matches, claims, dropped


async def assess_novelty(
    candidate: CandidateForPlanning,
    papers: tuple[PaperForNovelty, ...],
    *,
    coverage: str,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> NoveltyReading:
    """Compares the candidate with the papers. Raises ValueError (no papers) or LlmError subclasses."""
    labels = [paper.label for paper in papers]
    if len(set(labels)) != len(labels):
        raise ValueError("Paper labels must be unique.")
    system = system_prompt(ROLE)
    result = await run_structured(
        ROLE, system=system.text, prompt_version=system.version_id,
        user_message=render_novelty_task(candidate, papers, coverage=coverage),
        output_schema=NoveltyAssessmentOutput, settings=settings, model=model, cache=PROMPT_CACHE,
    )
    matches, claims, dropped = check_assessment(result.output, candidate, papers)
    return NoveltyReading(output=result.output, matches=matches, claims=claims, dropped=dropped,
                          records=result.records, prompt_version=system.version_id)



# --- revision after a Skeptic round (step 4.7b) -----------------------------------------------------------

@dataclass(frozen=True)
class CheckedResponse:
    challenge: str
    response: str
    """accept / rebut, as the model said."""
    rebuttal: CheckedClaim | None

    @property
    def rebuttal_holds(self) -> bool:
        return self.response == "rebut" and self.rebuttal is not None and self.rebuttal.status == "verified"


@dataclass(frozen=True)
class RevisionReading:
    output: NoveltyRevisionOutput
    matches: list[CheckedMatch]
    responses: list[CheckedResponse]
    dropped: list[str] = field(default_factory=list)
    records: list[LlmCallRecord] = field(default_factory=list)
    prompt_version: str = ""


def check_revision(output: NoveltyRevisionOutput, candidate: CandidateForPlanning,
                   challenges: tuple[ChallengeForRevision, ...],
                   papers: tuple[PaperForNovelty, ...]) -> tuple[list[CheckedMatch], list[CheckedResponse], list[str]]:
    by_label = {paper.label: paper for paper in papers}
    known = {challenge.label for challenge in challenges}
    dropped: list[str] = []
    matches = check_cells(output.facet_matches, candidate, by_label, dropped)
    responses = []
    for response in output.responses:
        if response.challenge not in known:
            dropped.append(f"response to unknown challenge {response.challenge}")
            continue
        rebuttal = check_claim(response.rebuttal, by_label, dropped) if response.rebuttal else None
        responses.append(CheckedResponse(response.challenge, response.response, rebuttal))
    return matches, responses, dropped


async def revise_novelty(
    candidate: CandidateForPlanning,
    claims: tuple[ClaimForSkeptic, ...],
    challenges: tuple[ChallengeForRevision, ...],
    papers: tuple[PaperForNovelty, ...],
    *,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> RevisionReading:
    """Answers a Skeptic round. Raises ValueError (nothing to revise) or LlmError subclasses."""
    system = system_prompt(ROLE, task="revise")
    result = await run_structured(
        ROLE, system=system.text, prompt_version=system.version_id,
        user_message=render_novelty_revision(candidate, claims, challenges, papers),
        output_schema=NoveltyRevisionOutput, settings=settings, model=model, cache=PROMPT_CACHE,
    )
    matches, responses, dropped = check_revision(result.output, candidate, challenges, papers)
    return RevisionReading(result.output, matches, responses, dropped, result.records, system.version_id)
