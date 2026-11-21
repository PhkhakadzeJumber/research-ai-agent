"""Workflow B's landscape retrieval (doc 05 §3, doc 06 §4; stage `landscape_retrieval`, step 5.1b).

landscape_step(sessions, investigation_id, search_for) runs every search of the confirmed
field brief several ways, decided by code, not by the model:

    kind      how                                   sources
    recent    last 3 years, by relevance            OpenAlex + arXiv
    survey    + "survey", last 5 years              OpenAlex
    cited     last 5 years, >= 50 citations,        OpenAlex
              by relevance (sorting a loose match by citations let famous off-topic papers in:
              step 5.1b live run)
    newest    newest first                          arXiv

Every search is stored as the investigation's landscape scope: origin `landscape`, the kind
as the query type, hits outside any evaluation (the same papers found twice are one paper,
dedup rules of steps 1.6 and 4.5b). If every search fails on every source, nothing could be
looked at and the investigation fails (doc 05 §11.6); otherwise the stage moves to
`landscape_reading` (step 5.2). No Claude calls.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import func, select

from src.ai.schemas.field_brief import FieldBriefOutput
from src.ai.workflows.evaluation import SearchesLike, Sessions
from src.ai.workflows.states import Stage
from src.ai.workflows.transitions import StaleState, advance_investigation, fail_investigation
from src.db.models import Investigation, Paper, RetrievalHit
from src.db.repositories.intakes import find_intake
from src.db.repositories.search_trail import save_search
from src.retrieval.records import SearchRequest, SearchResult
from src.timeutil import now

SEARCH_CONCURRENCY = 4


@dataclass(frozen=True)
class LandscapeKind:
    name: str
    sources: tuple[str, ...]
    years_back: int | None
    sort: str
    suffix: str = ""
    limit: int = 10
    min_citations: int | None = None


KINDS = (
    LandscapeKind("recent", ("openalex", "arxiv"), years_back=3, sort="relevance", limit=15),
    LandscapeKind("survey", ("openalex",), years_back=5, sort="relevance", suffix=" survey"),
    LandscapeKind("cited", ("openalex",), years_back=5, sort="relevance", min_citations=50),
    LandscapeKind("newest", ("arxiv",), years_back=None, sort="newest"),
)


@dataclass(frozen=True)
class PlannedSearch:
    subtopic: str
    kind: LandscapeKind
    request: SearchRequest


def plan_landscape(brief: FieldBriefOutput, *, year: int) -> list[PlannedSearch]:
    """Every (subtopic search x kind) to run, with its filters."""
    planned = []
    for subtopic in brief.subtopics:
        for query in subtopic.queries:
            for kind in KINDS:
                year_from = year - kind.years_back + 1 if kind.years_back else None
                planned.append(PlannedSearch(subtopic.name, kind, SearchRequest(
                    query=f"{query}{kind.suffix}", year_from=year_from, limit=kind.limit, sort=kind.sort,
                    min_citations=kind.min_citations)))
    return planned


@dataclass
class LandscapeOutcome:
    searches: int = 0
    retrieved: int = 0
    unique_papers: int = 0
    failed_source_searches: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)
    """Records retrieved per kind."""
    by_subtopic: dict[str, int] = field(default_factory=dict)
    """Distinct papers found per subtopic."""
    most_cited: list[Paper] = field(default_factory=list)
    failed: bool = False


async def confirmed_brief(session, investigation_id: uuid.UUID) -> FieldBriefOutput:
    intake = await find_intake(session, investigation_id)
    if intake is None or intake.confirmed_at is None:
        raise StaleState(f"Investigation {investigation_id} has no confirmed field brief.")
    return FieldBriefOutput.model_validate(intake.user_edits.get("final_brief", intake.agent_reading))


async def landscape_step(
    sessions: Sessions,
    investigation_id: uuid.UUID,
    search_for: Callable[[tuple[str, ...]], SearchesLike],
    *,
    concurrency: int = SEARCH_CONCURRENCY,
) -> LandscapeOutcome:
    """search_for(sources) gives a search over exactly those sources (e.g. ("openalex",))."""
    async with sessions() as session:
        investigation = await session.get(Investigation, investigation_id)
        if investigation is None or investigation.stage != Stage.LANDSCAPE_RETRIEVAL:
            raise StaleState(f"Investigation {investigation_id} isn't at landscape_retrieval.")
        brief = await confirmed_brief(session, investigation_id)
    planned = plan_landscape(brief, year=now().year)

    limiter = asyncio.Semaphore(concurrency)

    async def run(item: PlannedSearch) -> SearchResult:
        async with limiter:
            return await search_for(item.kind.sources).search(item.request)

    results = await asyncio.gather(*(run(item) for item in planned))

    outcome = LandscapeOutcome(searches=len(results))
    paper_subtopics: dict[uuid.UUID, set[str]] = {}
    for item, result in zip(planned, results):
        async with sessions() as session:
            saved = await save_search(session, investigation_id, result, origin="landscape", query_type=item.kind.name)
        for paper in saved.papers:
            paper_subtopics.setdefault(paper.id, set()).add(item.subtopic)
        outcome.retrieved += result.retrieved_count
        outcome.failed_source_searches += sum(1 for report in result.reports if not report.ok)
        outcome.by_kind[item.kind.name] = outcome.by_kind.get(item.kind.name, 0) + result.retrieved_count
    outcome.failed = all(result.all_failed for result in results)
    for subtopic in brief.subtopics:
        outcome.by_subtopic[subtopic.name] = sum(1 for names in paper_subtopics.values() if subtopic.name in names)

    in_scope = (RetrievalHit.investigation_id == investigation_id) & RetrievalHit.evaluation_id.is_(None)
    async with sessions() as session:
        outcome.unique_papers = (await session.execute(
            select(func.count()).select_from(RetrievalHit).where(in_scope))).scalar_one()
        outcome.most_cited = list((await session.execute(
            select(Paper).join(RetrievalHit, RetrievalHit.paper_id == Paper.id).where(in_scope)
            .order_by(Paper.citation_count.desc().nulls_last()).limit(5)
        )).scalars())
        if outcome.failed:
            await fail_investigation(session, investigation_id)
        else:
            await advance_investigation(session, investigation_id, to=Stage.LANDSCAPE_READING,
                                        expected_from=Stage.LANDSCAPE_RETRIEVAL)
    return outcome
