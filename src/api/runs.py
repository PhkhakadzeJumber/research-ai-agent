"""Background runs (doc 11 §4; step 9b): the work a request starts and returns from at once.

A run is an asyncio task inside the API process. Everything it does is saved after every step (the driver and the
workflows commit as they go), so if the process stops, nothing is lost: the investigation simply shows as idle and
can be resumed. Runs are never resumed automatically when the server starts (a restart would quietly spend money);
the user resumes them (POST /investigations/{id}/resume).

Rules:
  - one run per investigation at a time (RunConflict otherwise);
  - at most `max_parallel` runs at once: the others wait their turn (they show as running);
  - stop(id) asks the run to stop at the next step boundary (the driver's should_stop); it stays resumable.

What a run did is stored and streamed as progress events (step 9c). When a run ends, its credits are settled
(src/api/billing.py; step P2).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import select

from src.ai.progress import Entry, Recorder
from src.ai.workflows.clarification import answers_for, apply_answers, apply_edits, confirm_idea, load_pending
from src.ai.workflows.driver import DriverModels, Ended, advance
from src.ai.workflows.evaluation import Sessions
from src.ai.workflows.field import (
    apply_brief_answers,
    confirm_brief,
    load_pending_brief,
    remove_subtopics,
    understand_field,
)
from src.ai.workflows.proposal import write_proposal
from src.ai.workflows.understanding import understand_idea
from src.ai.workflows.user_document import understand_document
from src.ai.workflows.states import Stage
from src.api import billing
from src.db.models import Investigation, ProgressEvent, UserDocument

log = logging.getLogger("eureka.runs")

RunState = Literal["running", "stopping", "idle"]


class RunConflict(Exception):
    """The investigation already has a run."""


@dataclass
class RunModels:
    """Models per role for the runs; None means the configured ones (tests pass fakes)."""

    driver: DriverModels = field(default_factory=DriverModels)
    idea_model: Any = None
    """The Idea Analyst: reading an idea or a field, and applying the user's answers."""
    idea_settings: Any = None
    read_model: Any = None
    """The Paper Reader, for the user's document."""
    read_settings: Any = None
    proposal_model: Any = None
    proposal_settings: Any = None


@dataclass(frozen=True)
class Clarified:
    """The user's clarification, as the API received it (validated against the reading before the run starts)."""

    replies: Sequence[str | None] = ()
    edits: dict[int, str] = field(default_factory=dict)
    """Mode 1: facet position (0-based) -> new text."""
    remove_subtopics: Sequence[int] = ()
    """Mode 2: subtopic numbers (1-based) to drop."""


class Runs:
    def __init__(self, sessions: Sessions, *, models: RunModels | None = None, max_parallel: int = 2) -> None:
        self.sessions = sessions
        self.models = models or RunModels()
        self._active: dict[uuid.UUID, str] = {}
        """investigation id -> what its run does (understanding, running, clarifying, writing the package)."""
        self._stopping: set[uuid.UUID] = set()
        self._tasks: set[asyncio.Task] = set()
        self._slots = asyncio.Semaphore(max_parallel)
        self.last_ended: dict[uuid.UUID, Ended | str] = {}
        """How each investigation's last run in this process ended (an Ended, or the error text). Stored for good
        as the run's last progress event ("ended"), which the API reads."""
        self._listeners: dict[uuid.UUID, set[asyncio.Queue]] = {}

    # --- state ----------------------------------------------------------------------------------------------

    def state(self, investigation_id: uuid.UUID) -> RunState:
        if investigation_id not in self._active:
            return "idle"
        return "stopping" if investigation_id in self._stopping else "running"

    def stop(self, investigation_id: uuid.UUID) -> bool:
        """Asks the run to stop at the next step boundary; False if there is no run."""
        if investigation_id not in self._active:
            return False
        self._stopping.add(investigation_id)
        return True

    def should_stop(self, investigation_id: uuid.UUID) -> Callable[[], bool]:
        return lambda: investigation_id in self._stopping

    # --- progress events (step 9c) --------------------------------------------------------------------------

    def subscribe(self, investigation_id: uuid.UUID) -> asyncio.Queue:
        """A queue that receives each progress event of this investigation as it is stored (the live stream)."""
        queue: asyncio.Queue = asyncio.Queue()
        self._listeners.setdefault(investigation_id, set()).add(queue)
        return queue

    def unsubscribe(self, investigation_id: uuid.UUID, queue: asyncio.Queue) -> None:
        listeners = self._listeners.get(investigation_id, set())
        listeners.discard(queue)
        if not listeners:
            self._listeners.pop(investigation_id, None)

    def recorder(self, investigation_id: uuid.UUID) -> Recorder:
        async def broadcast(row: ProgressEvent) -> None:
            for queue in self._listeners.get(investigation_id, ()):
                queue.put_nowait(row)

        return Recorder(self.sessions, investigation_id, on_stored=broadcast)

    # --- starting -------------------------------------------------------------------------------------------

    def start(self, investigation_id: uuid.UUID, what: str, work: Callable[[], Awaitable[Any]]) -> None:
        if investigation_id in self._active:
            raise RunConflict(f"Investigation {investigation_id} is already {self._active[investigation_id]}.")
        self._active[investigation_id] = what
        self._launch(self._run(investigation_id, what, work))

    def _launch(self, run: Awaitable[None]) -> None:
        task = asyncio.create_task(run)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, investigation_id: uuid.UUID, what: str, work: Callable[[], Awaitable[Any]]) -> None:
        recorder = self.recorder(investigation_id)
        try:
            try:
                async with self._slots:
                    result = await work()
                ended = result if isinstance(result, Ended) else Ended("done")
                self.last_ended[investigation_id] = ended
                log.info("run %s (%s) ended: %s", investigation_id, what, ended)
            except Exception as error:  # noqa: BLE001 - a failed run must never take the server down
                self.last_ended[investigation_id] = f"{type(error).__name__}: {error}"
                log.exception("run %s (%s) failed", investigation_id, what)
                ended = None
            # its credits are settled before anything says it ended: what the user then sees is final
            await billing.settle_run(self.sessions, investigation_id, ended.reason if ended is not None else None)
            # the run is idle before its last event goes out, so a live stream that sees "ended" can close
            self._active.pop(investigation_id, None)
            self._stopping.discard(investigation_id)
            if ended is not None:
                await recorder.ended(ended)
            else:
                await recorder.store(Entry("ended", f"Something went wrong while {what}. Nothing is lost: resume to "
                                                    "try again.", data={"reason": "failed", "where": what}))
        finally:
            self._active.pop(investigation_id, None)
            self._stopping.discard(investigation_id)

    async def shutdown(self) -> None:
        """Server shutdown: the runs are cancelled where they stand (their saved state stays resumable)."""
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # --- the work -------------------------------------------------------------------------------------------

    async def understand(self, investigation_id: uuid.UUID) -> Ended:
        """Eureka's first reading: of the idea, the user's document, or the field (it then waits for the user)."""
        m = self.models
        async with self.sessions() as session:
            investigation = await session.get(Investigation, investigation_id)
            mode = investigation.mode
            has_document = (await session.execute(select(UserDocument.id).where(
                UserDocument.investigation_id == investigation_id))).first() is not None
        recorder = self.recorder(investigation_id)
        what = ("Mapping your field" if mode == "discover" else
                "Reading your document (the Paper Reader, then the Idea Analyst)" if has_document else
                "Reading your idea")
        await recorder.store(Entry("step_started", what, stage=Stage.UNDERSTANDING))
        if mode == "discover":
            outcome = await understand_field(self.sessions, investigation_id, settings=m.idea_settings,
                                             model=m.idea_model)
        elif has_document:
            outcome = await understand_document(self.sessions, investigation_id, read_settings=m.read_settings,
                                                read_model=m.read_model, settings=m.idea_settings, model=m.idea_model)
        else:
            outcome = await understand_idea(self.sessions, investigation_id, settings=m.idea_settings,
                                            model=m.idea_model)
        if outcome.error:
            await recorder.store(Entry("error", f"The Idea Analyst failed: {outcome.error}",
                                       stage=Stage.UNDERSTANDING))
            return Ended("failed", "understanding")
        if mode == "discover":
            message = f"Proposed {len(outcome.brief.subtopics)} subtopics to explore"
            questions = len(outcome.brief.clarifying_questions)
        else:
            message = f"Read “{outcome.reading.title}”: {len(outcome.reading.facets)} parts"
            questions = len(outcome.reading.clarifying_questions)
        await recorder.store(Entry("step_done", f"{message}, {questions} question{'' if questions == 1 else 's'} "
                                                "for you", stage=Stage.UNDERSTANDING, data={"questions": questions}))
        return Ended("nothing", "awaiting_clarification")

    async def drive(self, investigation_id: uuid.UUID) -> Ended:
        return await advance(self.sessions, investigation_id, models=self.models.driver,
                             emit=self.recorder(investigation_id), should_stop=self.should_stop(investigation_id))

    async def clarify(self, investigation_id: uuid.UUID, mode: str, clarified: Clarified) -> Ended:
        """The user's answers (one Idea Analyst call if any were given), edits and confirmation, then the run."""
        m = self.models
        recorder = self.recorder(investigation_id)
        if any((reply or "").strip() for reply in clarified.replies):
            await recorder.store(Entry("step_started", "Applying your answers (one Idea Analyst call)",
                                       stage=Stage.AWAITING_CLARIFICATION))
        if mode == "discover":
            pending = await load_pending_brief(self.sessions, investigation_id)
            # subtopics are removed from the brief the user saw; their answers then refine what is left
            removed = [pending.brief.subtopics[n - 1].name for n in clarified.remove_subtopics]
            trimmed = remove_subtopics(pending.brief, clarified.remove_subtopics) if removed else pending.brief
            answers = answers_for(pending.brief, clarified.replies)
            revision = await apply_brief_answers(self.sessions, dataclasses.replace(pending, brief=trimmed), answers,
                                                 settings=m.idea_settings, model=m.idea_model)
            if revision.error:
                return Ended("failed", f"applying your answers: {revision.error}")
            final = await confirm_brief(self.sessions, investigation_id, final=revision.brief, answers=answers,
                                        removed=removed)
            await recorder.store(Entry("stage", f"Confirmed: {len(final.subtopics)} subtopics to explore",
                                       stage=Stage.AWAITING_CLARIFICATION))
        else:
            pending = await load_pending(self.sessions, investigation_id)
            # the user's edits are made on the reading they saw; their answers then refine it
            edited = apply_edits(pending.reading, clarified.edits) if clarified.edits else pending.reading
            answers = answers_for(pending.reading, clarified.replies)
            outcome = await apply_answers(self.sessions, dataclasses.replace(pending, reading=edited), answers,
                                          settings=m.idea_settings, model=m.idea_model)
            if outcome.error:
                return Ended("failed", f"applying your answers: {outcome.error}")
            await confirm_idea(self.sessions, investigation_id, final=outcome.reading, answers=answers,
                               edits=clarified.edits, explicit=True)
            await recorder.store(Entry("stage", "Confirmed: your idea goes to the full evaluation",
                                       stage=Stage.AWAITING_CLARIFICATION))
        return await self.drive(investigation_id)

    async def write_package(self, investigation_id: uuid.UUID, candidate_id: uuid.UUID) -> Ended:
        recorder = self.recorder(investigation_id)
        await recorder.store(Entry("step_started", "Writing the research package (the Proposal Designer, one call)",
                                   stage=Stage.WRITING_PROPOSAL, candidate_id=candidate_id))
        outcome = await write_proposal(self.sessions, investigation_id, candidate_id=candidate_id,
                                       settings=self.models.proposal_settings, model=self.models.proposal_model)
        await recorder.store(Entry("step_done", f"Research package written (revision {outcome.package.revision}; "
                                                f"{outcome.retagged} statement(s) marked 'to verify')",
                                   stage=Stage.WRITING_PROPOSAL, candidate_id=candidate_id,
                                   data={"revision": outcome.package.revision, "retagged": outcome.retagged}))
        return Ended("done")
