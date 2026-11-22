"""The shared paper library (doc 11 §3.3): papers Eureka found or read, with its reading of each one.

Papers are shared across investigations (a paper is read and paid for once). The user's own documents are never in
here: they live in user_documents, private to their investigation. Search is a simple case-insensitive match on
titles and abstracts for now; ranking by embeddings can come with the retrieval upgrades (step 8).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from sqlalchemy import Select, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.citations import paper_links
from src.api.deps import SessionDep
from src.api.errors import not_found
from src.api.routes.investigations import describe
from src.api.schemas.common import ERRORS, Page, paper_ref
from src.api.schemas.papers import (
    Analysis,
    Finding,
    LibraryPaper,
    PaperDetail,
    PaperLinks,
    PaperQuote,
    PaperUse,
    QuotedPoint,
)
from src.db.models import (
    Candidate,
    Claim,
    ClaimEvidence,
    Evaluation,
    Investigation,
    Paper,
    PaperAnalysis,
    Quote,
    RetrievalHit,
)

router = APIRouter(prefix="/papers", tags=["papers"])


def _library_query() -> tuple[Select, object, object, object]:
    """Papers with their verified-quote count, investigation count and analysis level (None if not read)."""
    verified = (select(Quote.paper_id, func.count().label("n")).where(Quote.verified.is_(True))
                .group_by(Quote.paper_id).subquery())
    found_in = (select(RetrievalHit.paper_id, func.count(distinct(RetrievalHit.investigation_id)).label("n"))
                .group_by(RetrievalHit.paper_id).subquery())
    analysed = (select(PaperAnalysis.paper_id,
                       func.bool_or(PaperAnalysis.source_level == "full_text").label("full_text"))
                .group_by(PaperAnalysis.paper_id).subquery())
    query = (select(Paper, func.coalesce(verified.c.n, 0), func.coalesce(found_in.c.n, 0), analysed.c.paper_id,
                    analysed.c.full_text)
             .outerjoin(verified, verified.c.paper_id == Paper.id)
             .outerjoin(found_in, found_in.c.paper_id == Paper.id)
             .outerjoin(analysed, analysed.c.paper_id == Paper.id))
    return query, verified, found_in, analysed


def _library_paper(paper: Paper, quotes: int, investigations: int, analysed, full_text) -> dict:
    return dict(**paper_ref(paper).model_dump(), citation_count=paper.citation_count, read=analysed is not None,
                source_level=None if analysed is None else ("full_text" if full_text else "abstract"),
                verified_quotes=quotes, investigations=investigations)


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("", response_model=Page[LibraryPaper])
async def list_papers(
    session: SessionDep,
    q: str | None = Query(None, max_length=200, description="Words to find in titles and abstracts"),
    read: bool | None = Query(None, description="true: only papers Eureka read closely; false: only the others"),
    used: bool | None = Query(None, description="true: only papers found in your investigations"),
    year_from: int | None = Query(None, ge=1900, le=2100),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Page[LibraryPaper]:
    query, verified, found_in, analysed = _library_query()
    where = []
    if q and q.strip():
        pattern = f"%{_escape_like(q.strip())}%"
        where.append(or_(Paper.title.ilike(pattern, escape="\\"), Paper.abstract.ilike(pattern, escape="\\")))
    if read is not None:
        where.append(analysed.c.paper_id.isnot(None) if read else analysed.c.paper_id.is_(None))
    if used is not None:
        where.append(found_in.c.n > 0 if used else found_in.c.n.is_(None))
    if year_from is not None:
        where.append(Paper.year >= year_from)
    query = query.where(*where)
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (await session.execute(query.order_by(
        analysed.c.paper_id.is_(None), func.coalesce(verified.c.n, 0).desc(),
        Paper.citation_count.desc().nulls_last(), Paper.title).limit(limit).offset(offset))).all()
    return Page(items=[LibraryPaper(**_library_paper(*row)) for row in rows], total=total)


def _analysis(row: PaperAnalysis | None, verified_texts: set[str]) -> Analysis | None:
    if row is None:
        return None
    c = row.content or {}

    def point(item: dict, text_key: str) -> dict:
        quote = (item.get("quote") or {}).get("text", "")
        return dict(text=item.get(text_key, ""), quote=quote, verified=quote in verified_texts)

    return Analysis(
        source_level=row.source_level, created_at=row.created_at, contribution_type=c.get("contribution_type"),
        setting=c.get("setting"), tasks=c.get("tasks", []), methods=c.get("methods", []),
        datasets=c.get("datasets", []), metrics=c.get("metrics", []), baselines=c.get("baselines", []),
        findings=[Finding(**point(f, "claim"), setting=f.get("setting"), direction=f.get("direction", ""))
                  for f in c.get("findings", [])],
        limitations=[QuotedPoint(**point(item, "statement")) for item in c.get("limitations", [])],
        future_work=[QuotedPoint(**point(item, "statement")) for item in c.get("future_work", [])])


async def _uses(session: AsyncSession, paper_id: uuid.UUID) -> list[PaperUse]:
    hits = (await session.execute(
        select(RetrievalHit.investigation_id, func.bool_or(RetrievalHit.selected_for_reading))
        .where(RetrievalHit.paper_id == paper_id).group_by(RetrievalHit.investigation_id))).all()
    if not hits:
        return []
    investigation_ids = [i for i, _ in hits]
    as_evidence = set((await session.execute(
        select(Candidate.investigation_id).join(Evaluation, Evaluation.candidate_id == Candidate.id)
        .join(Claim, Claim.evaluation_id == Evaluation.id).join(ClaimEvidence, ClaimEvidence.claim_id == Claim.id)
        .join(Quote, Quote.id == ClaimEvidence.quote_id)
        .where(Quote.paper_id == paper_id, Quote.verified.is_(True),
               Candidate.investigation_id.in_(investigation_ids)))).scalars())
    closest = set()
    for investigation_id, assessment in (await session.execute(
            select(Candidate.investigation_id, Evaluation.novelty_assessment)
            .join(Evaluation, Evaluation.candidate_id == Candidate.id)
            .where(Candidate.investigation_id.in_(investigation_ids), Evaluation.novelty_assessment.isnot(None)))):
        if any(w.get("paper_id") == str(paper_id) for w in (assessment or {}).get("closest_works", [])):
            closest.add(investigation_id)
    investigations = {i.id: i for i in (await session.execute(
        select(Investigation).where(Investigation.id.in_(investigation_ids)))).scalars()}
    uses = []
    for investigation_id, was_read in hits:
        investigation = investigations[investigation_id]
        roles = ["found"] + (["read"] if was_read else []) \
            + (["evidence for a claim"] if investigation_id in as_evidence else []) \
            + (["closest work"] if investigation_id in closest else [])
        title, _ = await describe(session, investigation)
        uses.append(PaperUse(investigation_id=investigation_id, mode=investigation.mode, title=title, roles=roles))
    uses.sort(key=lambda u: -len(u.roles))
    return uses


@router.get("/{paper_id}", response_model=PaperDetail, responses=ERRORS)
async def get_paper(paper_id: uuid.UUID, session: SessionDep) -> PaperDetail:
    query, *_ = _library_query()
    row = (await session.execute(query.where(Paper.id == paper_id))).one_or_none()
    if row is None:
        raise not_found("Paper")
    paper = row[0]
    quotes = list((await session.execute(select(Quote).where(Quote.paper_id == paper_id)
                                         .order_by(Quote.verified.desc(), Quote.created_at))).scalars())
    analysis = (await session.execute(select(PaperAnalysis).where(PaperAnalysis.paper_id == paper_id)
                                      .order_by(PaperAnalysis.created_at.desc()).limit(1))).scalar_one_or_none()
    links = PaperLinks(**paper_links(paper), open_access_pdf=paper.open_access_pdf_url)
    return PaperDetail(**_library_paper(*row), authors=list(paper.authors or []), abstract=paper.abstract,
                       links=links, analysis=_analysis(analysis, {q.text for q in quotes if q.verified}),
                       quotes=[PaperQuote(id=q.id, text=q.text, verified=q.verified, source_level=q.source_level)
                               for q in quotes],
                       used_in=await _uses(session, paper_id))
