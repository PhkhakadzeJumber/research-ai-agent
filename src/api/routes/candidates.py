"""One idea (doc 11 §3.2): its detail, facet × paper matrix, claims, Skeptic log and research package.

Read-only (step 9a). The evaluation shown is the idea's latest full evaluation, or its latest one if it never had a
full evaluation, the same choice as the report. Verdicts are the frozen ones once the evaluation concluded;
before that they are computed from the current evidence and marked preliminary.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.workflows.concluding import stored_verdicts
from src.ai.workflows.proposal import TYPE_WORDS, WRITTEN, package_sections
from src.ai.workflows.value_feasibility import verdicts_for
from src.api.auth import CurrentUser
from src.api.deps import SessionDep
from src.api.errors import not_found
from src.api.schemas.candidates import (
    CandidateDetail,
    ChallengeOut,
    ClaimOut,
    ClosestWork,
    Coverage,
    DataCheck,
    Decision,
    EvaluationBrief,
    EvidenceQuote,
    Feasibility,
    Matrix,
    MatrixCell,
    PackageChecks,
    PackageSection,
    ResearchPackageOut,
    SkepticLog,
    SkepticRound,
    Statement,
    StatementSource,
)
from src.api.schemas.common import ERRORS, Verdict, labelled, paper_ref
from src.api.schemas.investigations import Facet
from src.db.models import (
    Candidate,
    Challenge,
    Claim,
    ClaimEvidence,
    Evaluation,
    FacetMatch,
    Investigation,
    Paper,
    Quote,
    ResearchPackage,
    TriageDecision,
    User,
    UserDocument,
)
from src.db.models import Facet as FacetRow
from src.db.repositories.evaluations import latest_evaluation

router = APIRouter(prefix="/candidates", tags=["candidates"])

DIMENSIONS = ("novelty", "significance", "feasibility", "recommendation")
# package section number -> the A7-written section it shows (src/ai/workflows/proposal.py, package_sections)
WRITTEN_SECTIONS = {2: "background", 4: "motivation", 5: "contribution", 9: "methodology", 10: "hypotheses",
                    12: "tools", 13: "experiment_design", 15: "limitations"}
assert set(WRITTEN_SECTIONS.values()) == set(WRITTEN)


async def get_candidate(session: AsyncSession, candidate_id: uuid.UUID, user: User) -> Candidate:
    """The idea if its investigation is the user's; otherwise "not found" (step 9d)."""
    candidate = await session.get(Candidate, candidate_id)
    owner = (await session.get(Investigation, candidate.investigation_id)).user_id if candidate else None
    if candidate is None or owner != user.id:
        raise not_found("Idea")
    return candidate


async def shown_evaluation(session: AsyncSession, candidate: Candidate) -> Evaluation | None:
    full = (await session.execute(
        select(Evaluation).where(Evaluation.candidate_id == candidate.id, Evaluation.depth == "full")
        .order_by(Evaluation.started_at.desc()).limit(1))).scalar_one_or_none()
    return full or await latest_evaluation(session, candidate.id)


async def papers_by_id(session: AsyncSession, ids) -> dict[uuid.UUID, Paper]:
    ids = list({uuid.UUID(str(i)) for i in ids})
    if not ids:
        return {}
    return {p.id: p for p in (await session.execute(select(Paper).where(Paper.id.in_(ids)))).scalars()}


async def candidate_facets(session: AsyncSession, candidate: Candidate) -> list[Facet]:
    sources: dict[str, str] = {}
    if candidate.origin == "user":
        document = (await session.execute(select(UserDocument).where(
            UserDocument.investigation_id == candidate.investigation_id))).scalar_one_or_none()
        sources = ((document.idea_extras or {}).get("facet_sources") or {}) if document else {}
    rows = (await session.execute(select(FacetRow).where(FacetRow.candidate_id == candidate.id)
                                  .order_by(FacetRow.position))).scalars()
    return [Facet(position=f.position, kind=f.kind, text=f.text, is_key=f.is_key, stated_by_user=f.stated_by_user,
                  from_section=sources.get(f.text)) for f in rows]


async def _verdicts(session: AsyncSession, evaluation: Evaluation) -> tuple[list[Verdict], bool, dict | None]:
    """(verdicts, preliminary, the current coverage if not frozen)."""
    frozen = await stored_verdicts(session, evaluation.id)
    if frozen:
        return [Verdict(dimension=name, category=v.category, label=labelled(v.category).label, rule=v.rule,
                        rules_version=v.rules_version, trace=list(v.rule_trace), limits=list(v.uncertainties),
                        frozen=True) for name in DIMENSIONS if (v := frozen.get(name))], False, None
    if evaluation.novelty_assessment is None:  # nothing to compute verdicts from yet
        return [], True, None
    live = await verdicts_for(session, evaluation.id)
    out = []
    for name in DIMENSIONS:
        v = live.recommendation if name == "recommendation" else getattr(live, name)
        limits = list(getattr(v, "weaknesses", []) or [])
        if name == "recommendation" and live.recommendation.needs_further_investigation:
            limits.append("needs further investigation")
        out.append(Verdict(dimension=name, category=v.category, label=labelled(v.category).label, rule=v.rule,
                           rules_version=v.rules_version, trace=list(v.trace), limits=limits, frozen=False))
    return out, True, live.coverage


def _coverage(coverage: dict | None, frozen: bool) -> Coverage | None:
    if not coverage:
        return None
    funnel, years = coverage.get("funnel", {}), coverage.get("years") or {}
    return Coverage(retrieved=funnel.get("retrieved", 0), unique=funnel.get("unique", 0),
                    screened=funnel.get("screened", 0), relevant=funnel.get("relevant", 0),
                    read=funnel.get("read", 0), read_abstract_only=coverage.get("read_abstract_only", 0),
                    sources=sorted(coverage.get("sources") or []), years_from=years.get("from"),
                    years_to=years.get("to"), blind_spots=list(coverage.get("blind_spots", [])),
                    budgets_hit=list(coverage.get("budgets_hit", [])), frozen=frozen)


def _feasibility(value: dict | None) -> Feasibility | None:
    if not value or "error" in value:
        return None
    feasibility, timeline = value.get("feasibility") or {}, value.get("timeline") or {}
    return Feasibility(
        data_checks=[DataCheck(name=d["name"], status=d["status"]) for d in value.get("data_checks", [])],
        compute_needed=feasibility.get("compute_needed"), total_weeks=timeline.get("total_weeks"),
        available_weeks=timeline.get("available_weeks"), skill_gaps=list(feasibility.get("skill_gaps", [])),
        risks=list(feasibility.get("risks", [])), ethics=list(feasibility.get("ethics", [])),
        simplifications=list(feasibility.get("simplifications", [])),
        beneficiaries=list((value.get("significance") or {}).get("beneficiaries", [])))


@router.get("/{candidate_id}", response_model=CandidateDetail, responses=ERRORS)
async def get_candidate_detail(candidate_id: uuid.UUID, session: SessionDep, user: CurrentUser) -> CandidateDetail:
    candidate = await get_candidate(session, candidate_id, user)
    evaluation = await shown_evaluation(session, candidate)
    verdicts, preliminary, live_coverage, decision, works, brief = [], True, None, None, [], None
    if evaluation is not None:
        brief = EvaluationBrief(id=evaluation.id, depth=evaluation.depth, step=evaluation.step,
                                status=evaluation.status, started_at=evaluation.started_at,
                                finished_at=evaluation.finished_at)
        verdicts, preliminary, live_coverage = await _verdicts(session, evaluation)
        row = (await session.execute(select(TriageDecision).where(TriageDecision.evaluation_id == evaluation.id)
                                     .order_by(TriageDecision.created_at.desc()))).scalars().first()
        if row is not None:
            decision = Decision(rule_number=row.rule_number, action=row.action, reason=row.reason)
        closest = (evaluation.novelty_assessment or {}).get("closest_works", [])
        papers = await papers_by_id(session, [w["paper_id"] for w in closest])
        works = [ClosestWork(paper=paper_ref(papers[uuid.UUID(w["paper_id"])]), what_they_did=w["what_they_did"],
                             how_the_idea_differs=w["how_the_idea_differs"],
                             minor_difference_only=bool(w.get("minor_difference_only")))
                 for w in closest if uuid.UUID(w["paper_id"]) in papers]
    coverage = _coverage(evaluation.coverage, True) if evaluation is not None and evaluation.coverage \
        else _coverage(live_coverage, False)
    return CandidateDetail(
        id=candidate.id, title=candidate.title, research_question=candidate.research_question,
        status=candidate.status, origin=candidate.origin, version=candidate.version,
        investigation_id=candidate.investigation_id, parent_id=candidate.parent_id, hypothesis=candidate.hypothesis,
        motivation=candidate.motivation, potential_contribution=candidate.potential_contribution,
        change_note=candidate.change_note, rejection_reason=candidate.rejection_reason,
        facets=await candidate_facets(session, candidate), evaluation=brief, verdicts=verdicts,
        preliminary=preliminary, decision=decision, closest_works=works,
        feasibility=_feasibility(evaluation.value_feasibility if evaluation else None), coverage=coverage)


@router.get("/{candidate_id}/matrix", response_model=Matrix, responses=ERRORS)
async def get_matrix(candidate_id: uuid.UUID, session: SessionDep, user: CurrentUser) -> Matrix:
    candidate = await get_candidate(session, candidate_id, user)
    facets = await candidate_facets(session, candidate)
    evaluation = await shown_evaluation(session, candidate)
    if evaluation is None:
        return Matrix(facets=facets, papers=[], cells=[])
    position = {f.id: f.position for f in (await session.execute(
        select(FacetRow).where(FacetRow.candidate_id == candidate.id))).scalars()}
    rows = (await session.execute(
        select(FacetMatch, Quote).outerjoin(Quote, (Quote.id == FacetMatch.quote_id) & Quote.verified.is_(True))
        .where(FacetMatch.evaluation_id == evaluation.id).order_by(FacetMatch.created_at))).all()
    papers = await papers_by_id(session, [m.paper_id for m, _ in rows])
    order = list(dict.fromkeys(m.paper_id for m, _ in rows if m.paper_id in papers))
    return Matrix(facets=facets, papers=[paper_ref(papers[p]) for p in order],
                  cells=[MatrixCell(facet_position=position.get(m.facet_id, -1), paper_id=m.paper_id,
                                    overlap=m.overlap, rationale=m.rationale, quote=q.text if q else None)
                         for m, q in rows if m.paper_id in papers])


@router.get("/{candidate_id}/claims", response_model=list[ClaimOut], responses=ERRORS)
async def get_claims(candidate_id: uuid.UUID, session: SessionDep, user: CurrentUser) -> list[ClaimOut]:
    candidate = await get_candidate(session, candidate_id, user)
    evaluation = await shown_evaluation(session, candidate)
    if evaluation is None:
        return []
    claims = list((await session.execute(select(Claim).where(Claim.evaluation_id == evaluation.id)
                                         .order_by(Claim.created_at))).scalars())
    evidence = (await session.execute(
        select(ClaimEvidence, Quote, Paper).join(Quote, Quote.id == ClaimEvidence.quote_id)
        .join(Paper, Paper.id == Quote.paper_id)
        .where(ClaimEvidence.claim_id.in_([c.id for c in claims])).order_by(ClaimEvidence.created_at))).all() \
        if claims else []
    by_claim: dict[uuid.UUID, list[EvidenceQuote]] = {}
    for ce, quote, paper in evidence:
        by_claim.setdefault(ce.claim_id, []).append(EvidenceQuote(
            quote_id=quote.id, text=quote.text, stance=ce.stance, verified=quote.verified,
            source_level=quote.source_level, paper=paper_ref(paper)))
    return [ClaimOut(id=c.id, kind=c.kind, text=c.text, statement_type=c.statement_type, inferred=c.inferred,
                     status=c.status, author_agent=c.author_agent, evidence=by_claim.get(c.id, []))
            for c in claims]


@router.get("/{candidate_id}/challenges", response_model=SkepticLog, responses=ERRORS)
async def get_challenges(candidate_id: uuid.UUID, session: SessionDep, user: CurrentUser) -> SkepticLog:
    candidate = await get_candidate(session, candidate_id, user)
    evaluation = await shown_evaluation(session, candidate)
    if evaluation is None:
        return SkepticLog(rounds=[], challenges=[])
    challenges = list((await session.execute(select(Challenge).where(Challenge.evaluation_id == evaluation.id)
                                             .order_by(Challenge.round, Challenge.created_at))).scalars())
    papers = await papers_by_id(session, [p for c in challenges for p in c.new_paper_ids or []])
    rounds = [SkepticRound(round=r["round"], new_relevant=r.get("new_relevant", 0), challenges=r.get("challenges", 0),
                           saturated=bool(r.get("saturated")))
              for r in (evaluation.counters or {}).get("skeptic_rounds", [])]
    return SkepticLog(rounds=rounds, challenges=[
        ChallengeOut(id=c.id, round=c.round, type=c.type, argument=c.argument, statement_type=c.statement_type,
                     resolution=c.resolution, target_claim_id=c.target_claim_id,
                     new_papers=[paper_ref(papers[p]) for p in c.new_paper_ids or [] if p in papers])
        for c in challenges])


async def _statements(session: AsyncSession, items: list[dict], evaluation_id: uuid.UUID) -> list[Statement]:
    ids: dict[str, list[uuid.UUID]] = {"paper": [], "claim": []}
    for item in items:
        for src in item.get("sources", []):
            ids.setdefault(src["kind"], []).append(uuid.UUID(src["id"]))
    papers = await papers_by_id(session, ids["paper"])
    claims = {c.id: c for c in (await session.execute(select(Claim).where(
        Claim.evaluation_id == evaluation_id, Claim.id.in_(ids.get("claim", []))))).scalars()} \
        if ids.get("claim") else {}
    out = []
    for item in items:
        sources = []
        for src in item.get("sources", []):
            key = uuid.UUID(src["id"])
            if src["kind"] == "paper" and key in papers:
                p = papers[key]
                sources.append(StatementSource(kind="paper", id=key, label=f"{p.title} ({p.year or 'n.d.'})"))
            elif src["kind"] == "claim" and key in claims:
                sources.append(StatementSource(kind="claim", id=key, label=claims[key].text))
        out.append(Statement(type=item["type"], label=TYPE_WORDS.get(item["type"], item["type"]), text=item["text"],
                             retagged=bool(item.get("retagged")), sources=sources))
    return out


@router.get("/{candidate_id}/proposal", response_model=ResearchPackageOut, responses=ERRORS)
async def get_proposal(candidate_id: uuid.UUID, session: SessionDep, user: CurrentUser) -> ResearchPackageOut:
    await get_candidate(session, candidate_id, user)
    package = (await session.execute(select(ResearchPackage).where(ResearchPackage.candidate_id == candidate_id)
                                     .order_by(ResearchPackage.revision.desc()).limit(1))).scalar_one_or_none()
    if package is None:
        raise not_found("Research package")
    sections = []
    for section in await package_sections(session, package):
        written = WRITTEN_SECTIONS.get(section["number"])
        statements = await _statements(session, package.sections.get(written, []), package.evaluation_id) \
            if written else None
        sections.append(PackageSection(number=section["number"], title=section["title"], status=section["status"],
                                       items=[item for item in section["items"] if item], statements=statements))
    checks = package.sections.get("checks") or {}
    return ResearchPackageOut(id=package.id, candidate_id=package.candidate_id, evaluation_id=package.evaluation_id,
                              revision=package.revision, model=package.model, prompt_version=package.prompt_version,
                              created_at=package.created_at,
                              checks=PackageChecks(retagged=checks.get("retagged", 0),
                                                   dropped_labels=checks.get("dropped_labels", 0)),
                              sections=sections)
