"""The progress screen's data (doc 11 §3.4; step 9c): the live event stream, the activity list and the progress
summary.

GET /investigations/{id}/events is a Server-Sent Events stream: it first replays the stored events after `after`
(or the browser's Last-Event-ID, when it reconnects), then sends each new event as the run stores it, with a
comment line every HEARTBEAT_SECONDS so proxies keep the connection open. It ends after the run's "ended" event,
or right after the replay if nothing is running. The stream reads the database through the runs' own sessions,
one short session at a time, so an open stream never holds a connection.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.workflows.budget import cost_cap
from src.ai.workflows.ranking import FINALIST_STATUSES
from src.api.billing import investigation_credits
from src.api.auth import COOKIE_NAME, CurrentUser, authenticate
from src.api.errors import ApiError
from src.api.deps import RunsDep, SessionDep
from src.api.routes.investigations import get_investigation, last_run
from src.api.runs import Runs
from src.api.schemas.common import ERRORS
from src.api.schemas.progress import Activity, Counters, Progress, RailStage, event_out
from src.db.models import Candidate, Evaluation, Investigation, Quote, RetrievalHit
from src.db.repositories.progress import events_after, last_event

router = APIRouter(prefix="/investigations", tags=["progress"])

HEARTBEAT_SECONDS = 15.0


# --- the stage rail ---------------------------------------------------------------------------------------

EVALUATE_RAIL = (("understanding", "Understanding your idea"), ("clarification", "Your answers"),
                 ("searching", "Searching the literature"), ("screening", "Screening for relevance"),
                 ("reading", "Reading the closest papers"), ("novelty", "Comparing part by part"),
                 ("skeptic", "Skeptic rounds"), ("value", "Significance & feasibility"),
                 ("verdicts", "Verdicts & triage"), ("loop", "Refinements & alternatives"), ("results", "Results"))
DISCOVER_RAIL = (("understanding", "Mapping your field"), ("clarification", "Your answers"),
                 ("landscape", "Searching the landscape"), ("landscape_reading", "Reading the landscape"),
                 ("mining", "Finding open problems"), ("ideas", "Proposing ideas"),
                 ("light", "Light evaluations"), ("full", "Full evaluations"), ("results", "Results"))
STEP_KEYS = {"normalizing": "searching", "planning_queries": "searching", "retrieving": "searching",
             "screening": "screening", "reading": "reading", "assessing_novelty": "novelty",
             "skeptic_review": "skeptic", "revising_novelty": "skeptic", "assessing_value_feasibility": "value",
             "concluding": "verdicts", "done": "verdicts"}
STAGE_KEYS = {"intake": "understanding", "understanding": "understanding", "awaiting_clarification": "clarification",
              "landscape_retrieval": "landscape", "landscape_reading": "landscape_reading",
              "opportunity_mining": "mining", "generating_candidates": "ideas", "prescreening": "ideas",
              "light_evaluation": "light", "refining": "light", "full_evaluation": "full",
              "ranking": "results", "reporting": "results"}
FINISHED_STAGES = ("results_ready", "writing_proposal")


async def _evaluate_key(session: AsyncSession, investigation: Investigation) -> tuple[str, str | None]:
    """Mode 1: where on the rail the investigation is, and the evaluation step under way (if any)."""
    stage = investigation.stage
    if stage in ("intake", "understanding", "awaiting_clarification"):
        return STAGE_KEYS[stage], None
    if stage in ("ranking", "reporting"):
        return "results", None
    own = (await session.execute(
        select(Evaluation).join(Candidate, Candidate.id == Evaluation.candidate_id)
        .where(Candidate.investigation_id == investigation.id, Candidate.origin == "user", Candidate.version == 1,
               Evaluation.depth == "full").order_by(Evaluation.started_at.desc()).limit(1))).scalar_one_or_none()
    if stage == "full_evaluation" and own is not None and own.status == "running":
        return STEP_KEYS.get(own.step, "searching"), own.step
    if stage == "full_evaluation" and own is None:
        return "searching", None
    return "loop", None


async def _discover_key(session: AsyncSession, investigation: Investigation) -> tuple[str, str | None]:
    stage = investigation.stage
    if stage == "triage":  # between light and full evaluations: the later one reached so far
        full = (await session.execute(
            select(func.count()).select_from(Evaluation).join(Candidate, Candidate.id == Evaluation.candidate_id)
            .where(Candidate.investigation_id == investigation.id, Evaluation.depth == "full"))).scalar_one()
        return ("full" if full else "light"), None
    return STAGE_KEYS.get(stage, "results"), None


async def rail(session: AsyncSession, investigation: Investigation, run: str) -> tuple[list[RailStage], str | None]:
    evaluate = investigation.mode == "evaluate"
    items = EVALUATE_RAIL if evaluate else DISCOVER_RAIL
    finished = investigation.stage in FINISHED_STAGES
    key, step = (None, None) if finished else await (_evaluate_key if evaluate else _discover_key)(
        session, investigation)
    position = len(items) if finished else [k for k, _ in items].index(key)
    if investigation.status == "failed":
        active = "failed"
    elif run != "idle" or investigation.stage == "awaiting_clarification":
        active = "active"
    else:
        active = "paused"
    out = []
    loop_used = True
    if evaluate:  # the loop only runs for refinements and alternatives: skipped if there were none
        loop_used = (await session.execute(select(func.count()).select_from(Candidate).where(
            Candidate.investigation_id == investigation.id))).scalar_one() > 1
    for n, (k, label) in enumerate(items):
        status = "done" if n < position else active if n == position else "todo"
        if k == "loop" and status == "done" and not loop_used:
            status = "skipped"
        out.append(RailStage(key=k, label=label, status=status))
    return out, step


async def counters(session: AsyncSession, investigation_id: uuid.UUID) -> Counters:
    hits = select(RetrievalHit).where(RetrievalHit.investigation_id == investigation_id).subquery()

    async def distinct_papers(*where) -> int:
        return (await session.execute(select(func.count(distinct(hits.c.paper_id))).where(*where))).scalar_one()

    read_papers = select(hits.c.paper_id).where(hits.c.selected_for_reading.is_(True))
    quotes = (await session.execute(select(func.count()).select_from(Quote).where(
        Quote.verified.is_(True), Quote.paper_id.in_(read_papers)))).scalar_one()
    statuses = list((await session.execute(select(Candidate.status).where(
        Candidate.investigation_id == investigation_id))).scalars())
    return Counters(papers_found=await distinct_papers(), relevant=await distinct_papers(hits.c.llm_score >= 2),
                    read=await distinct_papers(hits.c.selected_for_reading.is_(True)), quotes_verified=quotes,
                    ideas=len(statuses), finalists=sum(1 for s in statuses if s in FINALIST_STATUSES))


# --- routes -----------------------------------------------------------------------------------------------

@router.get("/{investigation_id}/progress", response_model=Progress, responses=ERRORS)
async def get_progress(investigation_id: uuid.UUID, session: SessionDep, user: CurrentUser, runs: RunsDep) -> Progress:
    """Everything the progress screen shows at once: the stage rail, counters from the database, cost against the
    budget, the run state and how the last run ended."""
    investigation = await get_investigation(session, investigation_id, user)
    run = runs.state(investigation_id)
    stages, step = await rail(session, investigation, run)
    newest = await last_event(session, investigation_id)
    return Progress(mode=investigation.mode, stage=investigation.stage, step=step, run=run, rail=stages,
                    counters=await counters(session, investigation_id),
                    cost_usd=float(investigation.cost_usd or 0), budget_usd=float(cost_cap(investigation)),
                    credits=await investigation_credits(session, investigation_id), last_seq=newest.seq if newest else 0, last_run=await last_run(session, investigation_id))


@router.get("/{investigation_id}/activity", response_model=Activity, responses=ERRORS)
async def get_activity(investigation_id: uuid.UUID, session: SessionDep, user: CurrentUser, after: int = Query(0, ge=0),
                       limit: int = Query(200, ge=1, le=500)) -> Activity:
    """The progress events after `after`, oldest first."""
    await get_investigation(session, investigation_id, user)
    rows = await events_after(session, investigation_id, after, limit)
    newest = await last_event(session, investigation_id)
    return Activity(items=[event_out(r) for r in rows], last_seq=newest.seq if newest else 0)


def sse(row) -> str:
    """One event in the Server-Sent Events format."""
    payload = event_out(row).model_dump(mode="json")
    return f"id: {row.seq}\nevent: {row.kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def event_stream(runs: Runs, investigation_id: uuid.UUID, after: int,
                       heartbeat: float | None = None) -> AsyncIterator[str]:
    """Replay, then live events, as SSE text; ends after the run's "ended" event (or after the replay if idle)."""
    heartbeat = HEARTBEAT_SECONDS if heartbeat is None else heartbeat
    queue = runs.subscribe(investigation_id)  # before the replay, so nothing stored meanwhile is missed
    try:
        last = after
        async with runs.sessions() as session:
            replay = await events_after(session, investigation_id, last)
        for row in replay:
            yield sse(row)
            last = row.seq
        if runs.state(investigation_id) == "idle":
            return
        while True:
            try:
                row = await asyncio.wait_for(queue.get(), timeout=heartbeat)
            except TimeoutError:
                yield ": keep-alive\n\n"
                if runs.state(investigation_id) == "idle" and queue.empty():
                    return  # the run ended without a final event reaching us (e.g. it was cancelled)
                continue
            if row.seq <= last:
                continue
            yield sse(row)
            last = row.seq
            if row.kind == "ended":  # a run's last event (stored while it still counts as running: src/api/runs.py)
                return
    finally:
        runs.unsubscribe(investigation_id, queue)


@router.get("/{investigation_id}/events", response_class=StreamingResponse, responses=ERRORS)
async def stream_events(investigation_id: uuid.UUID, request: Request, runs: RunsDep, after: int = Query(0, ge=0),
                        last_event_id: Annotated[str | None, Header()] = None) -> StreamingResponse:
    """Live progress (Server-Sent Events): stored events after `after` (or Last-Event-ID), then new ones as they
    happen, until the run ends."""
    async with runs.sessions() as session:  # a short session: the stream may stay open for many minutes
        user = await authenticate(session, request.cookies.get(COOKIE_NAME))
        if user is None:
            raise ApiError(401, "not_logged_in", "Log in to continue.")
        await get_investigation(session, investigation_id, user)
    if last_event_id and last_event_id.isdigit():
        after = max(after, int(last_event_id))
    return StreamingResponse(event_stream(runs, investigation_id, after), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
