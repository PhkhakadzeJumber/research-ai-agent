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
`landscape_reading`. No Claude calls.

landscape_reading_step(sessions, investigation_id) (stage `landscape_reading`, step 5.2a):
  1. the relevance screener (a small model) scores every landscape paper 0-3 against the
     field brief's subtopics (3: central and useful for finding open problems);
  2. code chooses what to read (about 45 papers, doc 05): at least PER_SUBTOPIC per subtopic,
     surveys first, then by score and how often a paper was found. A paper's subtopics are
     known from the searches that found it, not guessed;
  3. the Paper Reader reads them into the library (stored analyses are reused for free),
     and the stage moves to `opportunity_mining` (step 5.2b).
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import func, or_, select

from src.ai.agents.relevance_screener import screen_landscape_batch
from src.ai.schemas.field_brief import FieldBriefOutput
from src.ai.workflows.evaluation import READ_THRESHOLD, SearchesLike, Sessions, score_hits
from src.ai.workflows.reading import LibraryOutcome, read_into_library
from src.ai.workflows.states import Stage
from src.ai.workflows.transitions import StaleState, advance_investigation, fail_investigation
from src.config import RoleLlmSettings
from src.db.models import Chunk, Investigation, Paper, RetrievalHit, SearchQuery
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



# --- landscape_reading (step 5.2a) --------------------------------------------------------------------------

MAX_LANDSCAPE_READ = 45  # doc 05 §3: ~40-50 papers
PER_SUBTOPIC = 5
_SURVEY = re.compile(r"\b(survey|review|overview|systematic|tutorial)\b", re.IGNORECASE)


@dataclass
class LandscapeReading:
    screened: int = 0
    not_screened: int = 0
    score_counts: dict[int, int] = field(default_factory=dict)
    selected: list[tuple[Paper, list[str], bool]] = field(default_factory=list)
    """(paper, its subtopics, is a survey), in reading order."""
    read: list[LibraryOutcome] = field(default_factory=list)

    @property
    def read_now(self) -> int:
        return sum(1 for o in self.read if not o.error and not o.reused)

    @property
    def reused(self) -> int:
        return sum(1 for o in self.read if o.reused)

    @property
    def failed(self) -> int:
        return sum(1 for o in self.read if o.error)


def is_survey(title: str) -> bool:
    return bool(_SURVEY.search(title or ""))


def choose_landscape_papers(rows, subtopic_names: list[str], *, limit: int = MAX_LANDSCAPE_READ,
                            per_subtopic: int = PER_SUBTOPIC) -> list:
    """rows: (paper, subtopics, score, found_count, source_rank). At least per_subtopic per subtopic
    (surveys first, then score, then how often found), then the best of the rest, up to limit."""
    def key(row):
        paper, _, score, found, rank = row
        return (not is_survey(paper.title), -score, -found, rank if rank is not None else 10**9)

    ordered = sorted(rows, key=key)
    chosen, seen = [], set()
    for name in subtopic_names:
        taken = 0
        for row in ordered:
            if taken == per_subtopic or len(chosen) == limit:
                break
            if name in row[1] and row[0].id not in seen:
                chosen.append(row)
                seen.add(row[0].id)
                taken += 1
    for row in ordered:
        if len(chosen) == limit:
            break
        if row[0].id not in seen:
            chosen.append(row)
            seen.add(row[0].id)
    return chosen


async def landscape_reading_step(
    sessions: Sessions,
    investigation_id: uuid.UUID,
    *,
    limit: int = MAX_LANDSCAPE_READ,
    per_subtopic: int = PER_SUBTOPIC,
    screen_settings: RoleLlmSettings | None = None,
    screen_model=None,
    read_settings: RoleLlmSettings | None = None,
    read_model=None,
) -> LandscapeReading:
    async with sessions() as session:
        investigation = await session.get(Investigation, investigation_id)
        if investigation is None or investigation.stage != Stage.LANDSCAPE_READING:
            raise StaleState(f"Investigation {investigation_id} isn't at landscape_reading.")
        brief = await confirmed_brief(session, investigation_id)
    subtopics = tuple((n, s.name, s.why_it_fits_you) for n, s in enumerate(brief.subtopics, start=1))
    outcome = LandscapeReading()

    # 1. screening
    outcome.screened, outcome.not_screened = await score_hits(
        sessions, investigation_id, None,
        lambda papers: screen_landscape_batch(subtopics, papers, settings=screen_settings, model=screen_model))

    # 2. choosing: subtopics from the searches that found each paper
    query_subtopic = {}
    for subtopic in brief.subtopics:
        for query in subtopic.queries:
            for kind in KINDS:
                query_subtopic[f"{query}{kind.suffix}"] = subtopic.name
    in_scope = (RetrievalHit.investigation_id == investigation_id) & RetrievalHit.evaluation_id.is_(None)
    async with sessions() as session:
        texts = dict((await session.execute(
            select(SearchQuery.id, SearchQuery.query_text).where(SearchQuery.investigation_id == investigation_id,
                                                                 SearchQuery.origin == "landscape"))).all())
        has_text = or_(Paper.abstract.is_not(None), select(Chunk.id).where(Chunk.paper_id == Paper.id).exists())
        hits = (await session.execute(
            select(RetrievalHit, Paper, has_text).join(Paper, Paper.id == RetrievalHit.paper_id).where(in_scope)
        )).all()
        counts = {}
        rows = []
        for hit, paper, readable in hits:
            if hit.screened:
                counts[hit.llm_score] = counts.get(hit.llm_score, 0) + 1
            if (hit.llm_score or 0) >= READ_THRESHOLD and readable:
                names = sorted({query_subtopic.get(texts.get(q), "") for q in hit.found_by_query_ids} - {""})
                rows.append((paper, names, hit.llm_score, len(hit.found_by_query_ids), hit.source_rank))
        outcome.score_counts = {score: counts.get(score, 0) for score in (3, 2, 1, 0)}
        chosen = choose_landscape_papers(rows, [s.name for s in brief.subtopics], limit=limit,
                                         per_subtopic=per_subtopic)
        chosen_ids = {row[0].id for row in chosen}
        for hit, paper, _ in hits:
            hit.selected_for_reading = paper.id in chosen_ids
        outcome.selected = [(paper, names, is_survey(paper.title)) for paper, names, *_ in chosen]

    # 3. reading
    outcome.read = await read_into_library(sessions, [paper.id for paper, _, _ in outcome.selected],
                                           investigation_id=investigation_id, settings=read_settings,
                                           model=read_model)
    async with sessions() as session:
        await advance_investigation(session, investigation_id, to=Stage.OPPORTUNITY_MINING,
                                    expected_from=Stage.LANDSCAPE_READING)
    return outcome
