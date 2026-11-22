"""Investigations (doc 11 §3.1): the list, one investigation, its clarification, results, landscape and cost.

Read-only (step 9a): everything comes from stored data through the same functions the command-line report uses
(ranked_results, confirmed_brief, ...), so the API and the report can't disagree. Creating and running
investigations comes in step 9b.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.workflows.budget import cost_cap
from src.ai.workflows.landscape import confirmed_brief
from src.ai.workflows.ranking import FINALIST_STATUSES, Ranked, ranked_results
from src.ai.workflows.transitions import StaleState
from src.api.deps import RunsDep, SessionDep
from src.api.errors import ApiError, not_found
from src.api.runs import Runs
from src.api.schemas.common import ERRORS, Page, labelled
from src.api.schemas.investigations import (
    CandidateSummary,
    Clarification,
    Contradiction,
    Cost,
    CostLine,
    DocumentCard,
    Facet,
    FieldBriefReading,
    IdeaReading,
    InvestigationDetail,
    InvestigationSummary,
    Landscape,
    LandscapeSubtopic,
    NoveltyClaim,
    OpenProblem,
    Question,
    RankedIdea,
    Results,
    SetAside,
    Subtopic,
    VerdictBrief,
)
from src.db.models import (
    Candidate,
    Investigation,
    LlmCall,
    OpportunityItem,
    ResearchPackage,
    RetrievalHit,
    UserDocument,
)
from src.db.repositories.intakes import find_intake

router = APIRouter(prefix="/investigations", tags=["investigations"])


async def get_investigation(session: AsyncSession, investigation_id: uuid.UUID) -> Investigation:
    investigation = await session.get(Investigation, investigation_id)
    if investigation is None:
        raise not_found("Investigation")
    return investigation


async def find_document(session: AsyncSession, investigation_id: uuid.UUID) -> UserDocument | None:
    return (await session.execute(select(UserDocument).where(
        UserDocument.investigation_id == investigation_id))).scalar_one_or_none()


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


async def describe(session: AsyncSession, investigation: Investigation) -> tuple[str, str]:
    """(title, one-line summary) for the list and headers."""
    data = investigation.input or {}
    statuses = list((await session.execute(select(Candidate.status, Candidate.title).where(
        Candidate.investigation_id == investigation.id).order_by(Candidate.created_at))).all())
    if investigation.mode == "discover":
        finalists = sum(1 for status, _ in statuses if status in FINALIST_STATUSES)
        parts = [_plural(len(statuses), "idea"), _plural(finalists, "finalist")]
        if any(status == "proposal_ready" for status, _ in statuses):
            parts.append("package ready")
        return f"Ideas in {data.get('field') or 'your field'}", " · ".join(parts) if statuses else "Mapping the field"
    document = await find_document(session, investigation.id)
    idea = (data.get("idea") or "").strip()
    title = statuses[0][1] if statuses else (idea[:120] or (document.file_name if document else "Your idea"))
    if document is not None:
        return title, f"Your document · {document.file_name}"
    return title, statuses[0][0].replace("_", " ").capitalize() if statuses else "Waiting for confirmation"


async def summary_of(session: AsyncSession, investigation: Investigation, runs: Runs) -> dict:
    title, summary = await describe(session, investigation)
    run = runs.state(investigation.id)
    waiting = "clarification" if investigation.stage == "awaiting_clarification" and run == "idle" else None
    return dict(id=investigation.id, mode=investigation.mode, title=title, summary=summary,
                status=investigation.status, stage=investigation.stage, run=run, waiting_for=waiting,
                cost_usd=float(investigation.cost_usd or 0),
                budget_usd=float(cost_cap(investigation)), created_at=investigation.created_at,
                updated_at=investigation.updated_at)


@router.get("", response_model=Page[InvestigationSummary])
async def list_investigations(
    session: SessionDep,
    runs: RunsDep,
    status: str | None = None,
    mode: Literal["evaluate", "discover"] | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Page[InvestigationSummary]:
    where = [c for c in (Investigation.status == status if status else None,
                         Investigation.mode == mode if mode else None) if c is not None]
    total = (await session.execute(select(func.count()).select_from(Investigation).where(*where))).scalar_one()
    rows = (await session.execute(select(Investigation).where(*where).order_by(Investigation.updated_at.desc())
                                  .limit(limit).offset(offset))).scalars().all()
    return Page(items=[InvestigationSummary(**await summary_of(session, i, runs)) for i in rows], total=total)


@router.get("/{investigation_id}", response_model=InvestigationDetail, responses=ERRORS)
async def get_investigation_detail(investigation_id: uuid.UUID, session: SessionDep,
                                   runs: RunsDep) -> InvestigationDetail:
    investigation = await get_investigation(session, investigation_id)
    document = await find_document(session, investigation_id)
    card = None
    if document is not None:
        reading = document.reading or {}
        card = DocumentCard(file_name=document.file_name, media_type=document.media_type,
                            sections=len(document.sections or []), quotes=reading.get("quotes", 0),
                            verified_quotes=reading.get("verified_quotes", 0), published=document.published)
    return InvestigationDetail(**await summary_of(session, investigation, runs), input=investigation.input or {},
                               complexity=investigation.complexity,
                               constraints=investigation.constraints_snapshot or {},
                               assumptions=[str(a) for a in investigation.assumptions or []], document=card)


@router.get("/{investigation_id}/clarification", response_model=Clarification, responses=ERRORS)
async def get_clarification(investigation_id: uuid.UUID, session: SessionDep) -> Clarification:
    investigation = await get_investigation(session, investigation_id)
    intake = await find_intake(session, investigation_id)
    if intake is None:
        raise ApiError(409, "not_ready", "Eureka hasn't read this request yet.")
    final_key = "final_brief" if investigation.mode == "discover" else "final_reading"
    reading = (intake.user_edits or {}).get(final_key) or intake.agent_reading or {}
    questions = [Question(question=q["question"], if_skipped=q["if_skipped"])
                 for q in reading.get("clarifying_questions", [])]
    idea = brief = None
    if investigation.mode == "discover":
        brief = FieldBriefReading(subtopics=[Subtopic(name=s["name"], why_it_fits_you=s["why_it_fits_you"])
                                             for s in reading.get("subtopics", [])])
    else:
        document = await find_document(session, investigation_id)
        extras = (document.idea_extras or {}) if document else {}
        sources = extras.get("facet_sources") or {}
        idea = IdeaReading(
            title=reading.get("title", ""), research_question=reading.get("research_question", ""),
            facets=[Facet(position=n, kind=f["kind"], text=f["text"], is_key=f.get("is_key", False),
                          stated_by_user=f.get("stated_by_user", True), from_section=sources.get(f["text"]))
                    for n, f in enumerate(reading.get("facets", []))],
            vagueness_flags=reading.get("vagueness_flags", []),
            novelty_claims=[NoveltyClaim(claim=c["claim"], section=c["section"])
                            for c in extras.get("novelty_claims") or []])
    return Clarification(mode=investigation.mode, confirmed=intake.confirmed_at is not None,
                         confirmed_at=intake.confirmed_at, assumptions=reading.get("assumptions", []),
                         questions=questions, idea=idea, field_brief=brief)


def _candidate(c: Candidate) -> CandidateSummary:
    return CandidateSummary(id=c.id, title=c.title, research_question=c.research_question, status=c.status,
                            origin=c.origin, version=c.version)


def _ranked(rows: list[Ranked], depth: str, with_package: set[uuid.UUID]) -> list[RankedIdea]:
    return [RankedIdea(rank=n, candidate=_candidate(r.candidate), depth=depth,
                       verdicts=VerdictBrief(recommendation=labelled(r.standing.recommendation),
                                             novelty=labelled(r.standing.novelty),
                                             feasibility=labelled(r.standing.feasibility),
                                             significance=labelled(r.standing.significance)),
                       supporting_quotes=r.standing.supporting_quotes, ahead_because=r.ahead_because,
                       has_package=r.candidate.id in with_package)
            for n, r in enumerate(rows, start=1)]


@router.get("/{investigation_id}/results", response_model=Results, responses=ERRORS)
async def get_results(investigation_id: uuid.UUID, session: SessionDep) -> Results:
    await get_investigation(session, investigation_id)
    results = await ranked_results(session, investigation_id)
    with_package = set((await session.execute(
        select(ResearchPackage.candidate_id).join(Candidate, Candidate.id == ResearchPackage.candidate_id)
        .where(Candidate.investigation_id == investigation_id))).scalars())
    return Results(finalists=_ranked(results.finalists, "full", with_package),
                   advanced=_ranked(results.advanced, "light", with_package),
                   others=[SetAside(candidate=_candidate(c), why=why) for c, why in results.others])


@router.get("/{investigation_id}/landscape", response_model=Landscape, responses=ERRORS)
async def get_landscape(investigation_id: uuid.UUID, session: SessionDep) -> Landscape:
    investigation = await get_investigation(session, investigation_id)
    if investigation.mode != "discover":
        raise ApiError(409, "wrong_mode", "Only 'find me an idea' investigations have a landscape.")
    try:
        brief = await confirmed_brief(session, investigation_id)
    except StaleState:
        brief = None
    items = list((await session.execute(select(OpportunityItem).where(
        OpportunityItem.investigation_id == investigation_id))).scalars())
    counts = {i.label: i.counts or {} for i in items if i.type == "subtopic"}

    async def hits(*extra) -> int:
        return (await session.execute(select(func.count()).select_from(RetrievalHit).where(
            RetrievalHit.investigation_id == investigation_id, RetrievalHit.evaluation_id.is_(None), *extra))
        ).scalar_one()

    problems = sorted((i for i in items if i.type == "limitation_cluster"),
                      key=lambda i: -(i.counts or {}).get("papers", 0))
    return Landscape(
        field=(investigation.input or {}).get("field"),
        subtopics=[LandscapeSubtopic(name=s.name, why_it_fits_you=s.why_it_fits_you,
                                     papers_found=counts.get(s.name, {}).get("landscape_papers", 0),
                                     papers_read=counts.get(s.name, {}).get("read", 0))
                   for s in (brief.subtopics if brief else [])],
        papers_found=await hits(), papers_read=await hits(RetrievalHit.selected_for_reading.is_(True)),
        open_problems=[OpenProblem(id=i.id, label=i.label, papers=(i.counts or {}).get("papers", 0),
                                   author_groups=(i.counts or {}).get("author_groups", 0)) for i in problems],
        contradictions=[Contradiction(id=i.id, label=i.label) for i in items if i.type == "contradiction"])


@router.get("/{investigation_id}/cost", response_model=Cost, responses=ERRORS)
async def get_cost(investigation_id: uuid.UUID, session: SessionDep) -> Cost:
    investigation = await get_investigation(session, investigation_id)
    rows = (await session.execute(
        select(LlmCall.role, func.count(), func.coalesce(func.sum(LlmCall.cost_usd), Decimal(0)))
        .where(LlmCall.investigation_id == investigation_id).group_by(LlmCall.role).order_by(LlmCall.role))).all()
    return Cost(total_usd=float(investigation.cost_usd or 0), budget_usd=float(cost_cap(investigation)),
                by_role=[CostLine(role=role, calls=calls, cost_usd=float(total)) for role, calls, total in rows])
