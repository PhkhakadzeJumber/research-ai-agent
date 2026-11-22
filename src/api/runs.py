"""Background runs (doc 11 §4; step 9b): the work a request starts and returns from at once.

A run is an asyncio task inside the API process. Everything it does is saved after every step (the driver and the
workflows commit as they go), so if the process stops, nothing is lost: the investigation simply shows as idle and
can be resumed. Runs are never resumed automatically when the server starts (a restart would quietly spend money);
the user resumes them (POST /investigations/{id}/resume).

Rules:
  - one run per investigation at a time (RunConflict otherwise);
  - at most `max_parallel` runs at once: the others wait their turn (they show as running);
  - stop(id) asks the run to stop at the next step boundary (the driver's should_stop); it stays resumable.

What a run did is logged for now; step 9c stores and streams it as progress events.
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
from src.db.models import Investigation, UserDocument

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
        """How each investigation's last run ended (an Ended, or the error text). In memory only, until step 9c."""

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
        try:
            async with self._slots:
                result = await work()
            self.last_ended[investigation_id] = result if isinstance(result, Ended) else Ended("done")
            log.info("run %s (%s) ended: %s", investigation_id, what, self.last_ended[investigation_id])
        except Exception as error:  # noqa: BLE001 - a failed run must never take the server down
            self.last_ended[investigation_id] = f"{type(error).__name__}: {error}"
            log.exception("run %s (%s) failed", investigation_id, what)
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
        if mode == "discover":
            outcome = await understand_field(self.sessions, investigation_id, settings=m.idea_settings,
                                             model=m.idea_model)
        elif has_document:
            outcome = await understand_document(self.sessions, investigation_id, read_settings=m.read_settings,
                                                read_model=m.read_model, settings=m.idea_settings, model=m.idea_model)
        else:
            outcome = await understand_idea(self.sessions, investigation_id, settings=m.idea_settings,
                                            model=m.idea_model)
        return Ended("failed", "understanding") if outcome.error else Ended("nothing", "awaiting_clarification")

    async def drive(self, investigation_id: uuid.UUID) -> Ended:
        return await advance(self.sessions, investigation_id, models=self.models.driver,
                             should_stop=self.should_stop(investigation_id))

    async def clarify(self, investigation_id: uuid.UUID, mode: str, clarified: Clarified) -> Ended:
        """The user's answers (one Idea Analyst call if any were given), edits and confirmation, then the run."""
        m = self.models
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
            await confirm_brief(self.sessions, investigation_id, final=revision.brief, answers=answers,
                                removed=removed)
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
        return await self.drive(investigation_id)

    async def write_package(self, investigation_id: uuid.UUID, candidate_id: uuid.UUID) -> Ended:
        await write_proposal(self.sessions, investigation_id, candidate_id=candidate_id,
                             settings=self.models.proposal_settings, model=self.models.proposal_model)
        return Ended("done")
