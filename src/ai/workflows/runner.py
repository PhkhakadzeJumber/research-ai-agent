"""Running one evaluation through its steps (doc 05 §11.4), for both workflows.

run_evaluation(sessions, evaluation_id) picks the evaluation up wherever it stands and runs
step after step (planning_queries -> retrieving -> screening -> reading -> assessing_novelty
-> skeptic_review <-> revising_novelty -> assessing_value_feasibility -> concluding) until it
ends, fails, or the investigation's budget is used up. Before every paid step the cost cap
is checked (doc 05 §6), so a run stops cleanly and can be resumed later.

It prints nothing. A `report(step, phase, outcome)` callback is told when each step starts
(phase "start", outcome None) and ends (phase "done", with that step's outcome), so Mode 1's
command shows the whole story and Mode 2's shows one line per step.

Models and settings per role are passed in an EvaluationModels (tests pass fakes); empty
fields mean the configured models.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from src.ai.workflows.budget import budget_left
from src.ai.workflows.concluding import conclude_step
from src.ai.workflows.evaluation import (
    Sessions,
    novelty_step,
    plan_step,
    read_step,
    retrieve_step,
    screen_step,
)
from src.ai.workflows.skeptic_round import revision_step, skeptic_round_step
from src.ai.workflows.states import EvaluationStep
from src.ai.workflows.value_feasibility import value_step
from src.db.models import Evaluation, Investigation
from src.db.repositories.cache import DbResponseCache
from src.retrieval.search import LiteratureSearch

PAID_STEPS = {EvaluationStep.PLANNING_QUERIES, EvaluationStep.SCREENING, EvaluationStep.READING,
              EvaluationStep.ASSESSING_NOVELTY, EvaluationStep.SKEPTIC_REVIEW, EvaluationStep.REVISING_NOVELTY,
              EvaluationStep.ASSESSING_VALUE_FEASIBILITY}

Report = Callable[[str, str, Any], Awaitable[None]]


@dataclass
class EvaluationModels:
    """Settings and models per role; None means the configured one."""

    max_read: int | None = None
    settings: Any = None  # the Idea Analyst (query plan)
    model: Any = None
    screen_settings: Any = None
    screen_model: Any = None
    read_settings: Any = None
    read_model: Any = None
    novelty_settings: Any = None
    novelty_model: Any = None
    skeptic_settings: Any = None
    skeptic_model: Any = None
    value_settings: Any = None
    value_model: Any = None


@dataclass(frozen=True)
class RunResult:
    ended: str
    """done / partial (the evaluation ended), failed, retrieval_failed, or budget (stopped before a paid step)."""
    step: str
    """The step it ended or stopped at."""


async def _no_report(step: str, phase: str, outcome: Any) -> None:
    return None


async def run_evaluation(
    sessions: Sessions,
    evaluation_id: uuid.UUID,
    investigation_id: uuid.UUID,
    *,
    models: EvaluationModels | None = None,
    search_factory=None,
    report: Report | None = None,
) -> RunResult:
    models = models or EvaluationModels()
    report = report or _no_report
    factory = search_factory or (lambda: LiteratureSearch.from_settings(cache=DbResponseCache(sessions)))
    while True:
        async with sessions() as session:
            evaluation = await session.get(Evaluation, evaluation_id)
            await session.refresh(evaluation)
            step, status = evaluation.step, evaluation.status
        if status == "failed":
            return RunResult("failed", step)
        if status != "running":
            return RunResult(status, step)
        if step in PAID_STEPS:
            async with sessions() as session:
                if not budget_left(await session.get(Investigation, investigation_id)):
                    return RunResult("budget", step)

        await report(step, "start", None)
        if step == EvaluationStep.PLANNING_QUERIES:
            outcome = await plan_step(sessions, evaluation_id, settings=models.settings, model=models.model)
        elif step == EvaluationStep.RETRIEVING:
            async with factory() as search:
                outcome = await retrieve_step(sessions, evaluation_id, search)
        elif step == EvaluationStep.SCREENING:
            outcome = await screen_step(sessions, evaluation_id, max_read=models.max_read,
                                        settings=models.screen_settings, model=models.screen_model)
        elif step == EvaluationStep.READING:
            outcome = await read_step(sessions, evaluation_id, settings=models.read_settings, model=models.read_model)
        elif step == EvaluationStep.ASSESSING_NOVELTY:
            outcome = await novelty_step(sessions, evaluation_id, settings=models.novelty_settings,
                                         model=models.novelty_model)
        elif step == EvaluationStep.SKEPTIC_REVIEW:
            async with factory() as search:
                outcome = await skeptic_round_step(
                    sessions, evaluation_id, search, settings=models.skeptic_settings, model=models.skeptic_model,
                    screen_settings=models.screen_settings, screen_model=models.screen_model,
                    read_settings=models.read_settings, read_model=models.read_model)
        elif step == EvaluationStep.REVISING_NOVELTY:
            outcome = await revision_step(sessions, evaluation_id, settings=models.novelty_settings,
                                          model=models.novelty_model)
        elif step == EvaluationStep.ASSESSING_VALUE_FEASIBILITY:
            outcome = await value_step(sessions, evaluation_id, settings=models.value_settings,
                                       model=models.value_model)
        elif step == EvaluationStep.CONCLUDING:
            outcome = await conclude_step(sessions, evaluation_id)
        else:
            raise ValueError(f"Evaluation {evaluation_id} is at {step}, which the runner can't run.")
        await report(step, "done", outcome)
        if step == EvaluationStep.RETRIEVING and outcome.failed:
            return RunResult("retrieval_failed", step)
