"""The evaluation sub-graph (doc 05 §11.4), first steps: normalizing -> planning_queries ->
retrieving -> screening -> reading.

  begin_evaluation()  a new evaluation of a candidate. Mode 1's v1 skips `normalizing`:
                      its facets come from the confirmed Intake.
  plan_step()         A1 writes the query plan (quotas checked, regenerated once). If A1
                      fails, a simple plan built from the facets is used instead and the
                      step is recorded as degraded: the evaluation ends `partial`, the
                      investigation goes on (doc 05 §11.4).
  retrieve_step()     runs every planned query on every source (in parallel), then stores
                      each search's trail and papers (search_queries, papers, retrieval_hits,
                      all tied to the evaluation). A failing source is recorded, not fatal.
                      If EVERY search failed on every source, nothing could be looked at:
                      the evaluation and the investigation fail (doc 05 §11.6).

  screen_step()       the relevance screener (a small model) scores every found paper 0-3
                      against the candidate, in parallel batches; code then selects for
                      reading the papers scored 3, then 2, most-found first, up to
                      max_read. Batches that fail leave their papers unscreened and mark
                      the step degraded.
  read_step()         the Paper Reader reads the selected papers into the library (stored
                      analyses are reused for free) with quote verification.

Each step's results commit together with the step change. Snowballing (4.5b) and full
text come later.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.agents.idea_analyst import plan_queries
from src.ai.agents.relevance_screener import BATCH_SIZE, BatchScreening, screen_batch
from src.ai.context import CandidateForPlanning, FacetForPlanning, PaperForScreening
from src.ai.schemas.query_plan import PlannedQuery, QueryPlanOutput
from src.ai.workflows.reading import LibraryOutcome, read_into_library
from src.ai.workflows.states import EvaluationStep
from src.ai.workflows.transitions import (
    StaleState,
    advance_evaluation,
    end_evaluation,
    fail_investigation,
    note_degraded,
    start_evaluation,
)
from src.config import RoleLlmSettings
from src.db.models import Candidate, Evaluation, Paper, RetrievalHit
from src.db.repositories.candidates import candidate_facets
from src.db.repositories.evaluations import count_hits
from src.db.repositories.llm_calls import record_llm_calls
from src.db.repositories.papers import PaperMergeConflict
from src.db.repositories.search_trail import save_search
from src.llm import LlmError
from src.retrieval.records import SearchRequest, SearchResult

logger = logging.getLogger(__name__)

Sessions = Callable[[], AbstractAsyncContextManager[AsyncSession]]

# Results per query per source. In the step 4.4 live run OpenAlex returned the full 20
# for every query, so the cap, not the source, decided how much was seen.
PER_SOURCE_LIMIT = {"light": 20, "full": 30}
SEARCH_CONCURRENCY = 4
FALLBACK_WORDS = 8


class SearchesLike(Protocol):
    """What retrieve_step needs from src.retrieval.search.LiteratureSearch (tests pass a fake)."""

    async def search(self, request: SearchRequest) -> SearchResult: ...


async def _at_step(session: AsyncSession, evaluation_id: uuid.UUID, step: EvaluationStep) -> Evaluation:
    """The evaluation, if it's running and at `step`: checked before any money is spent."""
    evaluation = await session.get(Evaluation, evaluation_id)
    if evaluation is None:
        raise LookupError(f"No evaluation {evaluation_id}.")
    if evaluation.status != "running" or evaluation.step != step:
        raise StaleState(f"evaluation {evaluation_id} is at {evaluation.step} ({evaluation.status}), not {step}")
    return evaluation


# --- normalizing ----------------------------------------------------------------------------------------

async def begin_evaluation(sessions: Sessions, candidate_id: uuid.UUID, *, depth: str) -> uuid.UUID:
    """Starts an evaluation and moves it to planning_queries. Only Mode 1's user idea so far."""
    async with sessions() as session:
        candidate = await session.get(Candidate, candidate_id)
        if candidate is None:
            raise LookupError(f"No candidate {candidate_id}.")
        if candidate.origin != "user":
            raise NotImplementedError("Normalizing Scout candidates into facets comes with Workflow B (step 5).")
        evaluation = await start_evaluation(session, candidate_id, depth=depth)
        evaluation.counters = {"normalizing": "skipped: the facets come from the confirmed Intake"}
        await advance_evaluation(session, evaluation.id, to=EvaluationStep.PLANNING_QUERIES,
                                 expected_from=EvaluationStep.NORMALIZING)
        return evaluation.id


# --- planning_queries -----------------------------------------------------------------------------------

async def candidate_for_planning(session: AsyncSession, candidate: Candidate) -> CandidateForPlanning:
    facets = await candidate_facets(session, candidate.id)
    return CandidateForPlanning(
        title=candidate.title, research_question=candidate.research_question,
        facets=tuple(FacetForPlanning(number=f.position + 1, kind=f.kind, text=f.text, is_key=f.is_key)
                     for f in facets),
    )


def _keywords(text: str) -> str:
    return " ".join(text.replace(",", " ").replace("(", " ").replace(")", " ").split()[:FALLBACK_WORDS])


def fallback_plan(candidate: CandidateForPlanning) -> QueryPlanOutput:
    """A plain plan from the facets, used only when A1 can't write one."""
    queries: list[PlannedQuery] = []
    seen: set[str] = set()

    def add(text: str, kind: str, facets: list[int]) -> None:
        text = _keywords(text)
        if len(text) >= 3 and text.casefold() not in seen:
            seen.add(text.casefold())
            queries.append(PlannedQuery(text=text, kind=kind, facets=facets))

    keys = [facet for facet in candidate.facets if facet.is_key] or list(candidate.facets)
    add(candidate.title, "core", [facet.number for facet in keys])
    for facet in candidate.facets:
        add(facet.text, "facet_only", [facet.number])
    no_method = [facet for facet in keys if facet.kind != "method"]
    if no_method:
        add(" ".join(_keywords(facet.text) for facet in no_method), "without_method", [f.number for f in no_method])
    return QueryPlanOutput(queries=queries)


@dataclass(frozen=True)
class PlanOutcome:
    plan: QueryPlanOutput
    problems: list[str]
    regenerated: bool = False
    fallback_reason: str | None = None


async def plan_step(
    sessions: Sessions, evaluation_id: uuid.UUID, *, settings: RoleLlmSettings | None = None, model: Any | None = None
) -> PlanOutcome:
    async with sessions() as session:
        evaluation = await _at_step(session, evaluation_id, EvaluationStep.PLANNING_QUERIES)
        candidate = await session.get(Candidate, evaluation.candidate_id)
        investigation_id, depth = candidate.investigation_id, evaluation.depth
        view = await candidate_for_planning(session, candidate)

    records, fallback_reason = [], None
    try:
        reading = await plan_queries(view, depth=depth, settings=settings, model=model)
        records, outcome = reading.records, PlanOutcome(reading.output, reading.problems, reading.regenerated)
    except LlmError as error:
        logger.warning("query plan failed for evaluation %s: %s", evaluation_id, error)
        records = getattr(error, "records", [])
        fallback_reason = f"{type(error).__name__}: {error}"
        outcome = PlanOutcome(fallback_plan(view), [], fallback_reason=fallback_reason)

    async with sessions() as session:
        await record_llm_calls(session, records, investigation_id=investigation_id, evaluation_id=evaluation_id)
        if outcome.fallback_reason:
            await note_degraded(session, evaluation_id, "planning_queries",
                                f"A1 failed ({outcome.fallback_reason}); a simple plan from the facets was used")
        elif outcome.problems:
            await note_degraded(session, evaluation_id, "planning_queries",
                                "query quotas missed after regeneration: " + "; ".join(outcome.problems))
        evaluation = await advance_evaluation(session, evaluation_id, to=EvaluationStep.RETRIEVING,
                                              expected_from=EvaluationStep.PLANNING_QUERIES)
        evaluation.query_plan = outcome.plan.model_dump(mode="json")
        evaluation.counters = {**evaluation.counters, "queries_planned": len(outcome.plan.queries),
                               "query_plan_regenerated": outcome.regenerated}
    return outcome


# --- retrieving -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class RetrievalOutcome:
    searches: int
    retrieved: int
    """Records returned by all sources together, before deduplication."""
    unique_papers: int
    """Papers in this evaluation's scope after deduplication across all queries."""
    failed_source_searches: int
    """(query, source) pairs that failed."""
    failed: bool = False


async def retrieve_step(
    sessions: Sessions, evaluation_id: uuid.UUID, search: SearchesLike, *, concurrency: int = SEARCH_CONCURRENCY
) -> RetrievalOutcome:
    async with sessions() as session:
        evaluation = await _at_step(session, evaluation_id, EvaluationStep.RETRIEVING)
        candidate = await session.get(Candidate, evaluation.candidate_id)
        investigation_id = candidate.investigation_id
        plan = QueryPlanOutput.model_validate(evaluation.query_plan)
        limit = PER_SOURCE_LIMIT[evaluation.depth]

    limiter = asyncio.Semaphore(concurrency)

    async def run(query: PlannedQuery) -> SearchResult:
        async with limiter:
            return await search.search(SearchRequest(query=query.text, year_from=query.year_from, limit=limit))

    results = await asyncio.gather(*(run(query) for query in plan.queries))

    save_errors = 0
    for query, result in zip(plan.queries, results):
        try:
            async with sessions() as session:  # one transaction per search: one bad paper loses only its search
                await save_search(session, investigation_id, result, origin="analyst",
                                  evaluation_id=evaluation_id, query_type=query.kind)
        except PaperMergeConflict as error:
            save_errors += 1
            logger.warning("evaluation %s: search %r not stored: %s", evaluation_id, query.text, error)

    retrieved = sum(result.retrieved_count for result in results)
    failed_pairs = sum(1 for result in results for report in result.reports if not report.ok)
    everything_failed = all(result.all_failed for result in results)

    async with sessions() as session:
        unique = await count_hits(session, evaluation_id)
        if everything_failed:
            evaluation = await end_evaluation(session, evaluation_id, status="failed")
            await fail_investigation(session, investigation_id)
        else:
            evaluation = await advance_evaluation(session, evaluation_id, to=EvaluationStep.SCREENING,
                                                  expected_from=EvaluationStep.RETRIEVING)
        evaluation.counters = {**evaluation.counters, "searches": len(results), "retrieved": retrieved,
                               "unique_papers": unique, "failed_source_searches": failed_pairs,
                               "searches_not_stored": save_errors}
    return RetrievalOutcome(searches=len(results), retrieved=retrieved, unique_papers=unique,
                            failed_source_searches=failed_pairs, failed=everything_failed)



# --- screening ------------------------------------------------------------------------------------------

MAX_READ = {"light": 12, "full": 25}  # papers read per evaluation by default (step 4.5: about 3.4 cents each on Opus)
READ_THRESHOLD = 2  # scores 2 and 3 are worth reading; 1 and 0 never are
SCREEN_CONCURRENCY = 4


@dataclass(frozen=True)
class ScreeningOutcome:
    screened: int
    """Papers scored in this step."""
    score_counts: dict[int, int]
    """Over all screened papers of the evaluation: score -> count."""
    selected: int
    not_screened: int
    """Papers left unscreened because their batch failed or the model skipped them."""
    relevant_not_selected: int
    """Papers scored 2-3 that the max_read limit left out (reported in the coverage snapshot)."""


def _found_count():
    return func.cardinality(RetrievalHit.found_by_query_ids)


async def screen_step(
    sessions: Sessions,
    evaluation_id: uuid.UUID,
    *,
    max_read: int | None = None,
    batch_size: int = BATCH_SIZE,
    concurrency: int = SCREEN_CONCURRENCY,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> ScreeningOutcome:
    async with sessions() as session:
        evaluation = await _at_step(session, evaluation_id, EvaluationStep.SCREENING)
        candidate = await session.get(Candidate, evaluation.candidate_id)
        investigation_id = candidate.investigation_id
        limit = max_read if max_read is not None else MAX_READ[evaluation.depth]
        view = await candidate_for_planning(session, candidate)
        rows = (await session.execute(
            select(RetrievalHit.id, Paper.title, Paper.year, Paper.abstract)
            .join(Paper, Paper.id == RetrievalHit.paper_id)
            .where(RetrievalHit.evaluation_id == evaluation_id, RetrievalHit.screened.is_(False))
            .order_by(_found_count().desc(), RetrievalHit.source_rank.asc().nulls_last())
        )).all()

    batches = []
    for start in range(0, len(rows), batch_size):
        chunk = rows[start:start + batch_size]
        papers = [PaperForScreening(label=f"P{n}", title=title, year=year, abstract=abstract)
                  for n, (_, title, year, abstract) in enumerate(chunk, start=1)]
        batches.append(({paper.label: hit_id for paper, (hit_id, *_) in zip(papers, chunk)}, papers))

    limiter = asyncio.Semaphore(concurrency)

    async def run(papers) -> BatchScreening | LlmError:
        async with limiter:
            try:
                return await screen_batch(view, papers, settings=settings, model=model)
            except LlmError as error:
                logger.warning("screening batch failed for evaluation %s: %s", evaluation_id, error)
                return error

    results = await asyncio.gather(*(run(papers) for _, papers in batches))

    screened = not_screened = 0
    async with sessions() as session:
        for (hit_by_label, papers), result in zip(batches, results):
            if isinstance(result, LlmError):
                await record_llm_calls(session, getattr(result, "records", []), investigation_id=investigation_id,
                                       evaluation_id=evaluation_id)
                not_screened += len(papers)
                continue
            await record_llm_calls(session, result.records, investigation_id=investigation_id,
                                   evaluation_id=evaluation_id)
            not_screened += len(result.missing)
            for label, judgement in result.judgements.items():
                hit = await session.get(RetrievalHit, hit_by_label[label])
                hit.llm_score, hit.llm_reason, hit.screened = judgement.score, judgement.reason, True
                screened += 1
        await session.flush()

        if not_screened:
            await note_degraded(session, evaluation_id, "screening",
                                f"{not_screened} papers could not be screened (failed or skipped batches)")

        # Selection over the whole evaluation (also papers screened by an earlier, interrupted run).
        ranked = (await session.execute(
            select(RetrievalHit).where(RetrievalHit.evaluation_id == evaluation_id,
                                       RetrievalHit.llm_score >= READ_THRESHOLD)
            .order_by(RetrievalHit.llm_score.desc(), _found_count().desc(),
                      RetrievalHit.source_rank.asc().nulls_last())
        )).scalars().all()
        for position, hit in enumerate(ranked):
            hit.selected_for_reading = position < limit
        counts = dict((await session.execute(
            select(RetrievalHit.llm_score, func.count()).where(RetrievalHit.evaluation_id == evaluation_id,
                                                               RetrievalHit.screened.is_(True))
            .group_by(RetrievalHit.llm_score)
        )).all())
        score_counts = {score: counts.get(score, 0) for score in (3, 2, 1, 0)}
        selected = min(len(ranked), limit)

        evaluation = await advance_evaluation(session, evaluation_id, to=EvaluationStep.READING,
                                              expected_from=EvaluationStep.SCREENING)
        evaluation.counters = {**evaluation.counters, "screened": sum(score_counts.values()),
                               "score_counts": {str(k): v for k, v in score_counts.items()},
                               "not_screened": not_screened, "max_read": limit, "selected_for_reading": selected,
                               "relevant_not_selected": len(ranked) - selected}
    return ScreeningOutcome(screened=screened, score_counts=score_counts, selected=selected,
                            not_screened=not_screened, relevant_not_selected=len(ranked) - selected)


# --- reading --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ReadPaper:
    paper: Paper
    hit: RetrievalHit
    outcome: LibraryOutcome


@dataclass(frozen=True)
class ReadingOutcome:
    papers: list[ReadPaper]

    @property
    def read_now(self) -> int:
        return sum(1 for p in self.papers if not p.outcome.error and not p.outcome.reused)

    @property
    def reused(self) -> int:
        return sum(1 for p in self.papers if p.outcome.reused)

    @property
    def failed(self) -> int:
        return sum(1 for p in self.papers if p.outcome.error)


async def selected_papers(session: AsyncSession, evaluation_id: uuid.UUID) -> list[tuple[Paper, RetrievalHit]]:
    """The papers selected for reading, most relevant first."""
    rows = await session.execute(
        select(Paper, RetrievalHit).join(RetrievalHit, RetrievalHit.paper_id == Paper.id)
        .where(RetrievalHit.evaluation_id == evaluation_id, RetrievalHit.selected_for_reading.is_(True))
        .order_by(RetrievalHit.llm_score.desc(), _found_count().desc(), RetrievalHit.source_rank.asc().nulls_last())
    )
    return [(paper, hit) for paper, hit in rows.all()]


async def read_step(
    sessions: Sessions,
    evaluation_id: uuid.UUID,
    *,
    concurrency: int = 4,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> ReadingOutcome:
    async with sessions() as session:
        evaluation = await _at_step(session, evaluation_id, EvaluationStep.READING)
        investigation_id = (await session.get(Candidate, evaluation.candidate_id)).investigation_id
        chosen = await selected_papers(session, evaluation_id)

    outcomes = await read_into_library(sessions, [paper.id for paper, _ in chosen], investigation_id=investigation_id,
                                       evaluation_id=evaluation_id, concurrency=concurrency, settings=settings,
                                       model=model)
    result = ReadingOutcome([ReadPaper(paper, hit, outcome) for (paper, hit), outcome in zip(chosen, outcomes)])

    async with sessions() as session:
        if chosen and result.failed == len(chosen):
            await note_degraded(session, evaluation_id, "reading", "every selected paper failed to be read")
        elif result.failed:
            await note_degraded(session, evaluation_id, "reading", f"{result.failed} selected papers failed to be read")
        evaluation = await advance_evaluation(session, evaluation_id, to=EvaluationStep.ASSESSING_NOVELTY,
                                              expected_from=EvaluationStep.READING)
        ok = [p.outcome for p in result.papers if not p.outcome.error]
        evaluation.counters = {**evaluation.counters, "read_now": result.read_now, "read_reused": result.reused,
                               "read_failed": result.failed, "quotes": sum(o.quotes or 0 for o in ok),
                               "verified_quotes": sum(o.verified_quotes or 0 for o in ok)}
    return result
