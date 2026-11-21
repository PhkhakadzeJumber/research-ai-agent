"""Value and feasibility (doc 05 §11.4 step `assessing_value_feasibility`, step 4.8), and every
verdict of an evaluation in one place.

value_step(sessions, evaluation_id):
  1. the Value & Feasibility Evaluator judges significance and feasibility for this user
     (it sees the claims, the Skeptic's challenges with their resolution, the read papers);
  2. code verifies its significance quotes (claims kind `significance`, author
     `value_feasibility`), and checks what a model can't be trusted with:
       - data: a dataset counts as available only if a paper Eureka read names it
         (until a dataset lookup exists, doc 04 A6); everything else is unconfirmed
       - timeline: A6's phases are added up and compared with the user's available time
         from the constraints; going over becomes a blocking requirement
  3. stores the assessment (evaluations.value_feasibility) and moves to `concluding`.
A failed call degrades the step; significance and feasibility are then Unclear.

verdicts_for(session, evaluation_id) -> Verdicts: novelty, significance, feasibility, the
recommendation and the triage rule, all from stored evidence by the rules in
src/ai/evidence/verdicts.py. Frozen as Verdict rows at `concluding` (step 4.9).
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.agents.value_feasibility import ValueReading, assess_value
from src.ai.context import ChallengeSummary, ChunkForReading, ClaimForSkeptic, PaperForNovelty, name_papers
from src.ai.evidence.verdicts import (
    FeasibilityInputs,
    Recommendation,
    SignificanceInputs,
    TriageRule,
    VerdictResult,
    feasibility_verdict,
    recommendation,
    significance_verdict,
    triage_rule,
)
from src.ai.workflows.evaluation import Sessions, at_step, candidate_for_planning, novelty_verdict_for, papers_for_novelty
from src.ai.workflows.states import EvaluationStep
from src.ai.workflows.transitions import advance_evaluation, note_degraded
from src.config import RoleLlmSettings
from src.db.models import (
    Candidate,
    Challenge,
    Claim,
    ClaimEvidence,
    Evaluation,
    Facet,
    FacetMatch,
    Investigation,
    PaperAnalysis,
    Quote,
)
from src.db.repositories.analyses import QuoteToStore, store_quotes
from src.db.repositories.llm_calls import record_llm_calls
from src.llm import LlmError

logger = logging.getLogger(__name__)

VALUE_PAPERS = 15  # the most relevant read papers shown to A6
WEEKS_PER_MONTH = 4.3
_DURATION = re.compile(r"(\d+(?:[.,]\d+)?)\s*(week|wk|month|mo)", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def available_weeks(constraints: dict[str, Any]) -> float | None:
    """The user's available time in weeks, from a constraint whose name mentions time."""
    for name, value in (constraints or {}).items():
        if "time" not in str(name).lower():
            continue
        match = _DURATION.search(str(value))
        if match:
            number = float(match.group(1).replace(",", "."))
            return round(number * WEEKS_PER_MONTH if match.group(2).lower().startswith("mo") else number, 1)
    return None


def _norm(name: str) -> str:
    return _NON_ALNUM.sub(" ", name.lower()).strip()


def dataset_confirmed(name: str, known: set[str]) -> bool:
    """True if a dataset named in a read paper matches `name` (either contains the other)."""
    wanted = _norm(name)
    return bool(wanted) and any(wanted == k or (len(k) >= 3 and (k in wanted or wanted in k)) for k in known)


@dataclass
class ValueOutcome:
    reading: ValueReading | None = None
    data_checks: list[dict] = field(default_factory=list)
    timeline: dict = field(default_factory=dict)
    code_blocking: list[str] = field(default_factory=list)
    error: str | None = None


async def _read_papers_views(session: AsyncSession, evaluation_id: uuid.UUID, limit: int):
    chosen = await papers_for_novelty(session, evaluation_id, limit=limit)
    papers, views, known = {}, [], set()
    for n, (paper, analysis, chunks) in enumerate(chosen, start=1):
        label = f"P{n}"
        papers[label] = paper
        views.append(PaperForNovelty(label, paper.title, paper.tier, paper.year, paper.abstract,
                                     tuple(ChunkForReading(str(c.id), c.section_title, c.text) for c in chunks),
                                     analysis.content))
    # datasets named by ANY read paper of the evaluation, not only the ones shown
    for paper, analysis, _ in await papers_for_novelty(session, evaluation_id, limit=1000):
        known.update(_norm(name) for name in (analysis.content or {}).get("datasets", []) if _norm(name))
    return papers, tuple(views), known


async def value_step(
    sessions: Sessions,
    evaluation_id: uuid.UUID,
    *,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> ValueOutcome:
    outcome = ValueOutcome()
    async with sessions() as session:
        evaluation = await at_step(session, evaluation_id, EvaluationStep.ASSESSING_VALUE_FEASIBILITY)
        candidate = await session.get(Candidate, evaluation.candidate_id)
        investigation = await session.get(Investigation, candidate.investigation_id)
        view = await candidate_for_planning(session, candidate)
        constraints_dict = dict(investigation.constraints_snapshot or {})
        constraints = tuple((str(k).replace("_", " "), str(v)) for k, v in constraints_dict.items()
                            if v not in (None, "", []))
        claims = list((await session.execute(
            select(Claim).where(Claim.evaluation_id == evaluation_id, Claim.author_agent == "novelty")
            .order_by(Claim.created_at)
        )).scalars())
        challenges = list((await session.execute(
            select(Challenge).where(Challenge.evaluation_id == evaluation_id).order_by(Challenge.round)
        )).scalars())
        papers, views, known = await _read_papers_views(session, evaluation_id, VALUE_PAPERS)

    try:
        outcome.reading = await assess_value(
            view, complexity=investigation.complexity, constraints=constraints,
            claims=tuple(ClaimForSkeptic(f"C{n}", c.kind, c.text, c.status) for n, c in enumerate(claims, start=1)),
            challenges=tuple(ChallengeSummary(c.type, c.argument, c.resolution) for c in challenges),
            papers=views, settings=settings, model=model)
    except LlmError as error:
        outcome.error = f"the Value & Feasibility Evaluator failed ({type(error).__name__}: {error})"
        async with sessions() as session:
            await record_llm_calls(session, getattr(error, "records", []), investigation_id=investigation.id,
                                   evaluation_id=evaluation_id)
            await note_degraded(session, evaluation_id, "assessing_value_feasibility", outcome.error)
            evaluation = await advance_evaluation(session, evaluation_id, to=EvaluationStep.CONCLUDING,
                                                  expected_from=EvaluationStep.ASSESSING_VALUE_FEASIBILITY)
            evaluation.value_feasibility = {"error": outcome.error}
        return outcome

    feasibility = outcome.reading.output.feasibility
    for need in feasibility.data_needed:
        status = ("to_collect" if need.source == "collect"
                  else "confirmed" if dataset_confirmed(need.name, known) else "unconfirmed")
        outcome.data_checks.append({"name": need.name, "use": need.use, "source": need.source, "status": status})
    total = round(sum(phase.weeks for phase in feasibility.timeline), 1)
    available = available_weeks(constraints_dict)
    outcome.timeline = {"total_weeks": total, "available_weeks": available,
                        "over": available is not None and total > available}
    if outcome.timeline["over"]:
        outcome.code_blocking.append(f"the timeline needs {total:g} weeks; {available:g} are available")

    async with sessions() as session:
        await record_llm_calls(session, outcome.reading.records, investigation_id=investigation.id,
                               evaluation_id=evaluation_id)
        for n, claim in enumerate(outcome.reading.claims):
            row = Claim(evaluation_id=evaluation_id, kind="significance", text=name_papers(claim.text, papers),
                        statement_type="evidence",
                        inferred=False, status=claim.status, author_agent="value_feasibility")
            session.add(row)
            await session.flush()
            linked = set()
            for m, (label, quote) in enumerate(claim.evidence):
                stored = await store_quotes(session, papers[label].id, [QuoteToStore(
                    f"significance:{n}:{m}", quote.text, quote.chunk_id, quote.verified, quote.score)])
                stored_quote = stored[f"significance:{n}:{m}"]
                if stored_quote is not None and stored_quote.id not in linked:
                    linked.add(stored_quote.id)
                    session.add(ClaimEvidence(claim_id=row.id, quote_id=stored_quote.id, stance="supports"))
        await session.flush()
        evaluation = await advance_evaluation(session, evaluation_id, to=EvaluationStep.CONCLUDING,
                                              expected_from=EvaluationStep.ASSESSING_VALUE_FEASIBILITY)
        significance = outcome.reading.output.significance
        evaluation.value_feasibility = {
            "prompt_version": outcome.reading.prompt_version,
            "significance": {"beneficiaries": name_papers(significance.beneficiaries, papers),
                             "contribution_vs_closest": name_papers(significance.contribution_vs_closest, papers),
                             "proposed_level": significance.proposed_level},
            "feasibility": name_papers(feasibility.model_dump(mode="json"), papers),
            "data_checks": outcome.data_checks,
            "timeline": outcome.timeline,
            "code_blocking": outcome.code_blocking,
            "dropped": outcome.reading.dropped,
        }
    return outcome


# --- all verdicts -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Verdicts:
    novelty: VerdictResult
    significance: VerdictResult
    feasibility: VerdictResult
    recommendation: Recommendation
    triage: TriageRule
    coverage: dict


async def verdicts_for(session: AsyncSession, evaluation_id: uuid.UUID) -> Verdicts:
    novelty, coverage = await novelty_verdict_for(session, evaluation_id)
    evaluation = await session.get(Evaluation, evaluation_id)
    value = evaluation.value_feasibility or {}

    if not value or "error" in value:
        reason = value.get("error", "not assessed yet")
        significance = VerdictResult("unclear", "S4", [f"S4: significance not assessed ({reason})"],
                                     rules_version="significance-rules-v1")
        feasibility = VerdictResult("unclear", "F3", [f"F3: feasibility not assessed ({reason})"],
                                    rules_version="feasibility-rules-v1")
        simplifications: list[str] = []
        blocking: list[str] = []
    else:
        # significance: the papers behind each verified significance claim
        claim_papers = []
        for claim in (await session.execute(
            select(Claim).where(Claim.evaluation_id == evaluation_id, Claim.kind == "significance",
                                Claim.status == "verified")
        )).scalars():
            papers = (await session.execute(
                select(Quote.paper_id).join(ClaimEvidence, ClaimEvidence.quote_id == Quote.id)
                .where(ClaimEvidence.claim_id == claim.id, Quote.verified.is_(True))
            )).scalars().all()
            if papers:
                claim_papers.append(frozenset(str(p) for p in papers))
        cited = {p for group in claim_papers for p in group}
        surveys = frozenset(str(p) for p in (await session.execute(
            select(PaperAnalysis.paper_id).where(PaperAnalysis.paper_id.in_([uuid.UUID(p) for p in cited]),
                                                 PaperAnalysis.content["contribution_type"].astext == "survey")
        )).scalars()) if cited else frozenset()
        overstated = len((await session.execute(
            select(Challenge.id).where(Challenge.evaluation_id == evaluation_id,
                                       Challenge.type == "overstated_contribution",
                                       Challenge.statement_type == "evidence", Challenge.resolution != "rebutted")
        )).all())
        significance = significance_verdict(SignificanceInputs(tuple(claim_papers), surveys, novelty.category,
                                                               overstated))
        stated = value["feasibility"]
        simplifications = list(stated.get("simplifications", []))
        blocking = [*stated.get("blocking_requirements", []), *value.get("code_blocking", [])]
        feasibility = feasibility_verdict(FeasibilityInputs(
            blocking=tuple(blocking),
            simplifications=tuple(simplifications), skill_gaps=tuple(stated.get("skill_gaps", [])),
            unconfirmed_data=tuple(d["name"] for d in value.get("data_checks", []) if d["status"] == "unconfirmed"),
        ))

    # open facets: for a previously investigated idea, facets the matching paper doesn't share
    open_facets = False
    if novelty.category == "previously_investigated" and novelty.papers:
        facets = list((await session.execute(
            select(Facet).where(Facet.candidate_id == evaluation.candidate_id))).scalars())
        for paper in novelty.papers:
            rows = (await session.execute(
                select(FacetMatch.facet_id, FacetMatch.overlap).where(
                    FacetMatch.evaluation_id == evaluation_id, FacetMatch.paper_id == uuid.UUID(paper))
            )).all()
            same = {facet_id for facet_id, overlap in rows if overlap == "same"}
            if any(f.id not in same for f in facets):
                open_facets = True
    rec = recommendation(novelty, significance, feasibility,
                         refinement_available=bool(simplifications) or open_facets)
    triage = triage_rule(novelty, significance, feasibility, simplifications=bool(simplifications),
                         open_facets=open_facets, depth=evaluation.depth, blocking=bool(blocking))
    return Verdicts(novelty, significance, feasibility, rec, triage, coverage)
