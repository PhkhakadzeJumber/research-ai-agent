"""Replaying a finished run, live, without AI calls (step pages, Phase C; local only: EUREKA_DEV_REPLAY).

To see the step pages work as a run goes (the stepper moving, each step's log filling in, the 5 s "moving on"
notice) without paying for a new run: the finished run is copied (src/db/clone.py), the copy is set back to where
its pipeline started (after the clarification), and a background run of the API plays its stored progress events
again in order, each pause shortened to at most GAP_SECONDS, moving the copy's stage (and in Mode 1 its evaluation's
step) exactly as the real run did. Runs from before progress events were stored are played from a standard script
of their stages. Nothing is charged: no AI call is made and no credits are reserved.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.progress import Entry
from src.ai.workflows.driver import Ended
from src.api.runs import Runs
from src.db.clone import clone_with_ids
from src.db.models import Candidate, Evaluation, Investigation, ProgressEvent, User
from src.db.models.run import INVESTIGATION_STAGES

GAP_SECONDS = 3.0
"""The longest pause between two replayed events."""

START_STAGE = {"evaluate": "full_evaluation", "discover": "landscape_retrieval"}

# runs without stored events: their stages in order, with what each said when it started and when it was done
SCRIPT = {
    "evaluate": [(None, "planning_queries", "Planning the searches", "Planned the searches"),
                 (None, "retrieving", "Searching arXiv and OpenAlex", "Search done"),
                 (None, "screening", "Screening the papers for relevance", "Screened the papers"),
                 (None, "reading", "Reading the closest papers", "Reading done"),
                 (None, "assessing_novelty", "Comparing the idea with the closest papers", "Compared the idea"),
                 (None, "skeptic_review", "Skeptic round: searching for evidence against the idea", "Skeptic done"),
                 (None, "assessing_value_feasibility", "Judging significance and feasibility", "Judged them"),
                 (None, "concluding", "Concluding: freezing the coverage and the verdicts", "Verdicts decided")],
    "discover": [("landscape_retrieval", None, "Searching the landscape", "Landscape searched"),
                 ("landscape_reading", None, "Reading the landscape", "Landscape read"),
                 ("opportunity_mining", None, "Finding open problems", "Open problems found"),
                 ("generating_candidates", None, "Proposing ideas", "Ideas proposed"),
                 ("prescreening", None, "Pre-screening the ideas", "Pre-screening done"),
                 ("light_evaluation", None, "Light evaluations", "Light evaluations done"),
                 ("full_evaluation", None, "Full evaluations of the finalists", "Full evaluations done"),
                 ("ranking", None, "Ranking the finalists", "Ranked")],
}


class NothingToReplay(Exception):
    """The run hasn't finished."""


@dataclass
class _Event:
    kind: str
    message: str
    stage: str | None
    step: str | None
    data: dict
    candidate_id: uuid.UUID | None
    gap: float


async def start_replay(session: AsyncSession, runs: Runs, source: Investigation, user: User) -> uuid.UUID:
    """Copies the finished run for `user`, sets the copy back to its start, and starts playing it; returns the copy's
    id (the caller commits nothing: this function commits, since the background run reads the copy)."""
    if source.stage not in ("results_ready", "writing_proposal"):
        raise NothingToReplay("Only a finished run can be replayed.")
    mode = source.mode
    new_id, _, _ = await clone_with_ids(session, source, user.id)
    copy = await session.get(Investigation, new_id)
    final_stage, final_status = copy.stage, copy.status
    events = list((await session.execute(select(ProgressEvent).where(
        ProgressEvent.investigation_id == new_id).order_by(ProgressEvent.seq))).scalars())
    start = next((n for n, e in enumerate(events) if e.stage == START_STAGE[mode]), None)
    if start is None:  # a run from before progress events: play its stages from the standard script
        plan = [_Event("step_started" if half == 0 else "step_done", msg, stage, step, {}, None, GAP_SECONDS)
                for stage, step, started, done in SCRIPT[mode] for half, msg in ((0, started), (1, done))]
        keep_until = len(events)
    else:
        end = next((n for n in range(start, len(events)) if events[n].kind == "ended"), len(events))
        plan, previous = [], events[start].created_at
        for e in events[start:end]:
            gap = min(GAP_SECONDS, max(0.4, (e.created_at - previous).total_seconds()))
            previous = e.created_at
            plan.append(_Event(e.kind, e.message, e.stage, e.step, dict(e.data or {}), e.candidate_id, gap))
        keep_until = start
    # what stays: the events before the pipeline started (reading the idea, the clarification); the rest is replayed
    later = [e.id for e in events[keep_until:]]
    if later:
        await session.execute(delete(ProgressEvent).where(ProgressEvent.id.in_(later)))
    evaluation_id, final_eval = None, None
    if mode == "evaluate":
        own = (await session.execute(select(Evaluation).join(Candidate, Candidate.id == Evaluation.candidate_id).where(
            Candidate.investigation_id == new_id, Candidate.origin == "user", Evaluation.depth == "full")
            .order_by(Evaluation.started_at).limit(1))).scalar_one_or_none()
        if own is not None:
            evaluation_id, final_eval = own.id, (own.step, own.status)
            own.step, own.status = "planning_queries", "running"
    copy.stage, copy.status = START_STAGE[mode], "running"
    await session.commit()

    async def play() -> Ended:
        recorder = runs.recorder(new_id)
        for event in plan:
            await asyncio.sleep(event.gap)
            async with runs.sessions() as s:
                inv = await s.get(Investigation, new_id)
                if event.stage in INVESTIGATION_STAGES and event.stage not in ("intake", "understanding",
                                                                              "awaiting_clarification"):
                    inv.stage = event.stage
                if event.step and evaluation_id is not None and mode == "evaluate":
                    evaluation = await s.get(Evaluation, evaluation_id)
                    if evaluation.status == "running":
                        evaluation.step = event.step
                        if event.step == "concluding" and event.kind == "step_done":
                            evaluation.step, evaluation.status = final_eval
                await s.commit()
            await recorder.store(Entry(event.kind, event.message, stage=event.stage, step=event.step,
                                       data=event.data, candidate_id=event.candidate_id))
        async with runs.sessions() as s:  # the run's end, as the real one finished
            inv = await s.get(Investigation, new_id)
            inv.stage, inv.status = final_stage, final_status
            if evaluation_id is not None:
                evaluation = await s.get(Evaluation, evaluation_id)
                evaluation.step, evaluation.status = final_eval
            await s.commit()
        return Ended("done")

    runs.start(new_id, "replaying", play)
    return new_id
