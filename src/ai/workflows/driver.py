"""Running an investigation from wherever it stands (doc 05 §11; step 9b.1), for both workflows.

advance(sessions, investigation_id) looks at the investigation's stage and carries out what comes next, stage after
stage, until it needs the user, ends or has to stop:

    Mode 2:  landscape_retrieval -> landscape_reading -> opportunity_mining -> generating_candidates
    both:    generating_candidates -> prescreening -> light_evaluation -> triage -> (refining | generating_candidates
             | full_evaluation | ranking) -> ... -> ranking -> reporting -> results_ready
    Mode 1:  full_evaluation of the user's idea (v1) -> triage -> the same loop for refinements and alternatives

It never prints. What happens is told to an `emit(Event)` callback (the command-line scripts print each event;
the API will store and stream them), and how it ended comes back as an `Ended`:
    done                the results are ready
    budget              the cost cap is reached (resume with a higher budget)
    account_refused     Anthropic refused the account (usage limit, credit, key); resume once access is back
    failed              a step failed; what happened was emitted just before
    stopped             should_stop() asked for it, between steps; nothing is lost, resume any time
    nothing             the stage isn't one this driver runs (e.g. the clarification, which needs the user)

This is the control flow that lived in scripts/candidate_loop.py, scripts/evaluate.py and scripts/discover.py,
moved here unchanged so the commands and the API run the same code. The understanding and clarification steps
stay outside: they need the user (the commands ask on the terminal, the API takes the answers in a request).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from src.ai.workflows.alternatives import NoIdeaToReplace
from src.ai.workflows.budget import budget_left, cost_cap
from src.ai.workflows.concluding import finish_investigation
from src.ai.workflows.evaluation import Sessions, begin_evaluation
from src.ai.workflows.full_evaluation import full_evaluation_step
from src.ai.workflows.generating import generate_step
from src.ai.workflows.landscape import landscape_reading_step, landscape_step
from src.ai.workflows.light_evaluation import light_evaluation_step
from src.ai.workflows.mining import mining_step
from src.ai.workflows.prescreening import prescreen_step
from src.ai.workflows.ranking import complete_results, ranked_results
from src.ai.workflows.refining import refine_step
from src.ai.workflows.routing import route_after_triage
from src.ai.workflows.runner import EvaluationModels, ShouldStop, run_evaluation
from src.ai.workflows.scouting import NothingToGround
from src.ai.workflows.states import Mode, Stage
from src.db.models import Investigation
from src.db.repositories.cache import DbResponseCache
from src.db.repositories.candidates import investigation_candidates
from src.db.repositories.evaluations import latest_evaluation
from src.llm import LlmError
from src.retrieval.search import LiteratureSearch
from src.retrieval.sources.arxiv import ArxivSource
from src.retrieval.sources.openalex import OpenAlexSource


@dataclass
class DriverModels:
    """Models and searches per role; None means the configured ones (tests pass fakes)."""

    evaluation: EvaluationModels | None = None
    scout_model: Any = None
    search_factory: Any = None
    quick_model: Any = None
    landscape_search_for: Any = None
    """Mode 2 landscape: sources -> a search over exactly those sources."""
    landscape_screen_model: Any = None
    landscape_read_model: Any = None
    mine_model: Any = None


@dataclass(frozen=True)
class Event:
    kind: str
    data: dict = field(default_factory=dict)


Emit = Callable[[Event], Awaitable[None]]


@dataclass(frozen=True)
class Ended:
    reason: str
    """done / budget / account_refused / failed / stopped / nothing (see the module docstring)."""
    where: str | None = None
    """Where it ended: "before <stage>", "at <candidate>: <step>", a step or a stage."""
    cost_usd: Decimal | None = None
    cap_usd: Decimal | None = None
    """For budget: the cost so far and the cap."""

    @property
    def exit_code(self) -> int:
        """The commands' exit codes: 0 ok, 1 a step failed, 3 the budget is used up."""
        return {"budget": 3, "failed": 1, "account_refused": 1}.get(self.reason, 0)


async def _ignore(event: Event) -> None:
    return None


def _never() -> bool:
    return False


@dataclass
class _Run:
    sessions: Sessions
    investigation_id: uuid.UUID
    models: DriverModels
    emit: Emit
    should_stop: ShouldStop

    async def investigation(self) -> Investigation:
        async with self.sessions() as session:
            investigation = await session.get(Investigation, self.investigation_id)
            await session.refresh(investigation)
            return investigation

    async def budget_ended(self, where: str) -> Ended:
        investigation = await self.investigation()
        return Ended("budget", where, Decimal(investigation.cost_usd or 0), cost_cap(investigation))

    async def over_budget(self, before: str) -> Ended | None:
        investigation = await self.investigation()
        return None if budget_left(investigation) else await self.budget_ended(f"before {before}")

    async def finished(self) -> Ended:
        investigation = await self.investigation()
        await self.emit(Event("done", {"cost_usd": investigation.cost_usd}))
        return Ended("done")

    async def loop_stop(self, outcome, phase: str) -> Ended | None:
        """After a light or full evaluation step: how it stopped, if it did."""
        if outcome.stopped is None:
            return None
        if outcome.stopped == "budget":
            return await self.budget_ended(f"at {outcome.stopped_at}")
        if outcome.stopped in ("account_refused", "stopped"):
            return Ended(outcome.stopped, outcome.stopped_at)
        await self.emit(Event("loop_failed", {"phase": phase, "stopped": outcome.stopped,
                                              "stopped_at": outcome.stopped_at}))
        return Ended("failed", outcome.stopped_at)


# --- Mode 2: the landscape --------------------------------------------------------------------------------

async def _landscape(run: _Run) -> Ended | None:
    await run.emit(Event("landscape_searching"))
    if run.models.landscape_search_for is not None:
        outcome = await landscape_step(run.sessions, run.investigation_id, run.models.landscape_search_for)
    else:
        # One client per source, shared by every combination: two arXiv clients each kept their own
        # 3-second spacing and together got rate-limited (step 5.1b live run: 16 of 34 failed).
        cache = DbResponseCache(run.sessions)
        openalex, arxiv = OpenAlexSource.from_settings(cache=cache), ArxivSource(cache=cache)
        searches = {("openalex", "arxiv"): LiteratureSearch([openalex, arxiv]),
                    ("openalex",): LiteratureSearch([openalex]), ("arxiv",): LiteratureSearch([arxiv])}
        try:
            outcome = await landscape_step(run.sessions, run.investigation_id,
                                           lambda sources: searches[tuple(sources)])
        finally:
            await openalex.aclose()
            await arxiv.aclose()
    await run.emit(Event("landscape_searched", {"outcome": outcome}))
    return Ended("failed", Stage.LANDSCAPE_RETRIEVAL) if outcome.failed else None


async def _landscape_reading(run: _Run) -> Ended | None:
    if stop := await run.over_budget("reading the landscape"):
        return stop
    await run.emit(Event("landscape_reading"))
    outcome = await landscape_reading_step(run.sessions, run.investigation_id,
                                           screen_model=run.models.landscape_screen_model,
                                           read_model=run.models.landscape_read_model)
    await run.emit(Event("landscape_read", {"outcome": outcome}))
    return None


async def _mining(run: _Run) -> Ended | None:
    if stop := await run.over_budget("mining the landscape"):
        return stop
    await run.emit(Event("mining"))
    try:
        outcome = await mining_step(run.sessions, run.investigation_id, model=run.models.mine_model)
    except LlmError as error:
        await run.emit(Event("mining_failed", {"error": error}))
        return Ended("failed", Stage.OPPORTUNITY_MINING)
    await run.emit(Event("mined", {"outcome": outcome}))
    return None


# --- the candidate loop (both workflows) ------------------------------------------------------------------

async def _generating(run: _Run) -> Ended | None:
    if stop := await run.over_budget("generating candidates"):
        return stop
    await run.emit(Event("generating"))
    try:
        task, outcome = await generate_step(run.sessions, run.investigation_id, model=run.models.scout_model)
    except (NothingToGround, NoIdeaToReplace) as error:
        await run.emit(Event("generate_failed", {"error": error, "nothing_to_generate": True}))
        return Ended("failed", Stage.GENERATING_CANDIDATES)
    except LlmError as error:
        await run.emit(Event("generate_failed", {"error": error, "nothing_to_generate": False}))
        return Ended("failed", Stage.GENERATING_CANDIDATES)
    await run.emit(Event("generated", {"task": task, "outcome": outcome}))
    return None


async def _prescreening(run: _Run) -> Ended | None:
    if stop := await run.over_budget("pre-screening"):
        return stop
    await run.emit(Event("prescreening"))
    try:
        outcome = await prescreen_step(run.sessions, run.investigation_id, model=run.models.quick_model)
    except LlmError as error:
        await run.emit(Event("prescreen_failed", {"error": error}))
        return Ended("failed", Stage.PRESCREENING)
    await run.emit(Event("prescreened", {"outcome": outcome}))
    return None


async def _evaluation_callbacks(run: _Run, phase: str):
    async def on_candidate(candidate, number, total) -> None:
        await run.emit(Event(f"{phase}_candidate", {"candidate": candidate, "number": number, "total": total}))

    async def report(step, step_phase, outcome) -> None:
        await run.emit(Event("loop_step", {"step": step, "phase": step_phase, "outcome": outcome}))

    return on_candidate, report


async def _light_evaluation(run: _Run) -> Ended | None:
    on_candidate, report = await _evaluation_callbacks(run, "light")
    outcome = await light_evaluation_step(run.sessions, run.investigation_id, models=run.models.evaluation,
                                          search_factory=run.models.search_factory, report=report,
                                          on_candidate=on_candidate, should_stop=run.should_stop)
    await run.emit(Event("light_done", {"outcome": outcome}))
    return await run.loop_stop(outcome, "light evaluation")


async def _refining(run: _Run) -> Ended | None:
    if stop := await run.over_budget("refining"):
        return stop
    await run.emit(Event("refining"))
    try:
        outcome = await refine_step(run.sessions, run.investigation_id, model=run.models.scout_model)
    except LlmError as error:
        await run.emit(Event("refine_failed", {"error": error}))
        return Ended("failed", Stage.REFINING)
    await run.emit(Event("refined", {"outcome": outcome}))
    return None


async def _triage(run: _Run) -> Ended | None:
    decision = await route_after_triage(run.sessions, run.investigation_id)
    await run.emit(Event("routed", {"decision": decision}))
    return None


async def _ranking(run: _Run) -> Ended | None:
    async with run.sessions() as session:
        results = await ranked_results(session, run.investigation_id)
    await complete_results(run.sessions, run.investigation_id)
    investigation = await run.investigation()
    await run.emit(Event("ranked", {"results": results, "mode": investigation.mode}))
    return await run.finished()


# --- full evaluation: the user's idea (Mode 1), or the best advanced candidates --------------------------

async def _idea_evaluation(run: _Run, candidate_id: uuid.UUID) -> Ended | None:
    """Mode 1: the full evaluation of the user's own idea (v1), then its triage."""
    async with run.sessions() as session:
        evaluation = await latest_evaluation(session, candidate_id)
        evaluation_id = evaluation.id if evaluation is not None and evaluation.status == "running" else None
    if evaluation_id is None:
        evaluation_id = await begin_evaluation(run.sessions, candidate_id, depth="full")
        await run.emit(Event("evaluation_started", {"evaluation_id": evaluation_id}))

    async def report(step: str, phase: str, outcome) -> None:
        await run.emit(Event("idea_step", {"evaluation_id": evaluation_id, "step": step, "phase": phase,
                                           "outcome": outcome}))

    result = await run_evaluation(run.sessions, evaluation_id, run.investigation_id, models=run.models.evaluation,
                                  search_factory=run.models.search_factory, report=report,
                                  should_stop=run.should_stop)
    if result.ended == "failed":
        await run.emit(Event("evaluation_failed", {"step": result.step}))
        return Ended("failed", result.step)
    if result.ended == "retrieval_failed":  # the retrieving step's event already said so
        return Ended("failed", result.step)
    if result.ended in ("account_refused", "stopped"):
        return Ended(result.ended, result.step)
    if result.ended == "budget":
        return await run.budget_ended(f"before {result.step}")

    investigation = await run.investigation()
    if investigation.stage == Stage.FULL_EVALUATION:
        outcome = await finish_investigation(run.sessions, run.investigation_id)
        await run.emit(Event("idea_triaged", {"outcome": outcome, "evaluation_status": result.ended}))
        if outcome.route is not None and outcome.route.stage != Stage.RANKING:
            return None  # the candidate loop carries on from the new stage
    return await run.finished()


async def _full_evaluation(run: _Run) -> Ended | None:
    investigation = await run.investigation()
    if investigation.mode == Mode.EVALUATE:
        async with run.sessions() as session:
            own = [c for c in await investigation_candidates(session, run.investigation_id)
                   if c.status == "full_evaluating" and c.origin == "user" and c.version == 1]
        if own:
            return await _idea_evaluation(run, own[0].id)
    on_candidate, report = await _evaluation_callbacks(run, "full")
    outcome = await full_evaluation_step(run.sessions, run.investigation_id, models=run.models.evaluation,
                                         search_factory=run.models.search_factory, report=report,
                                         on_candidate=on_candidate, should_stop=run.should_stop)
    await run.emit(Event("full_done", {"outcome": outcome}))
    return await run.loop_stop(outcome, "full evaluation")


STAGES: dict[str, Callable[[_Run], Awaitable[Ended | None]]] = {
    Stage.LANDSCAPE_RETRIEVAL: _landscape,
    Stage.LANDSCAPE_READING: _landscape_reading,
    Stage.OPPORTUNITY_MINING: _mining,
    Stage.GENERATING_CANDIDATES: _generating,
    Stage.PRESCREENING: _prescreening,
    Stage.LIGHT_EVALUATION: _light_evaluation,
    Stage.REFINING: _refining,
    Stage.FULL_EVALUATION: _full_evaluation,
    Stage.TRIAGE: _triage,
    Stage.RANKING: _ranking,
}
"""The stages advance() runs, and the function that carries each one out."""


async def advance(
    sessions: Sessions,
    investigation_id: uuid.UUID,
    *,
    models: DriverModels | None = None,
    emit: Emit | None = None,
    should_stop: ShouldStop | None = None,
    only: str | None = None,
) -> Ended:
    """Runs the investigation from its current stage until it ends, needs the user or has to stop.

    only=<stage>: carry out just that stage (the Mode 2 command's `landscape` step), then return Ended("done")."""
    run = _Run(sessions, investigation_id, models or DriverModels(), emit or _ignore, should_stop or _never)
    while True:
        stage = (await run.investigation()).stage
        handler = STAGES.get(stage)
        if handler is None or (only is not None and stage != only):
            return Ended("nothing", stage)
        if run.should_stop():
            return Ended("stopped", f"before {stage.replace('_', ' ')}")
        ended = await handler(run)
        if ended is not None:
            return ended
        if only is not None:
            return Ended("done", stage)
