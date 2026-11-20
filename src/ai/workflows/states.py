"""The formal state graph of doc 05 §11 in code: state names and allowed transitions.

Three levels of state (doc 05 §11):
  - Stage:           where the orchestrator is in an investigation (Workflow A or B)
  - EvaluationStep:  where one candidate's evaluation is
  - CandidateStatus: what has happened to one idea

The names come from src/db/models/run.py, where the database's CHECK constraints use
the same tuples, so code, database and doc can't drift apart (tests compare all three).

Any transition not listed here is a bug: check_*() raise IllegalTransition instead of
allowing it. This module is pure (no database); src/ai/workflows/transitions.py
applies investigation changes to the database.
"""

from __future__ import annotations

from enum import StrEnum

from src.db.models.run import (
    CANDIDATE_STATUSES,
    EVALUATION_STATUSES,
    EVALUATION_STEPS,
    INVESTIGATION_STAGES,
    INVESTIGATION_STATUSES,
    MODES,
)


class IllegalTransition(Exception):
    """A state change the graph in doc 05 §11 doesn't allow."""


Mode = StrEnum("Mode", {name.upper(): name for name in MODES})
Stage = StrEnum("Stage", {name.upper(): name for name in INVESTIGATION_STAGES})
InvestigationStatus = StrEnum("InvestigationStatus", {name.upper(): name for name in INVESTIGATION_STATUSES})
EvaluationStep = StrEnum("EvaluationStep", {name.upper(): name for name in EVALUATION_STEPS})
EvaluationStatus = StrEnum("EvaluationStatus", {name.upper(): name for name in EVALUATION_STATUSES})
CandidateStatus = StrEnum("CandidateStatus", {name.upper(): name for name in CANDIDATE_STATUSES})

S, E, C = Stage, EvaluationStep, CandidateStatus


def _table(*edges: tuple[StrEnum, StrEnum]) -> dict[StrEnum, frozenset[StrEnum]]:
    table: dict[StrEnum, set[StrEnum]] = {}
    for source, target in edges:
        table.setdefault(source, set()).add(target)
    return {source: frozenset(targets) for source, targets in table.items()}


# --- investigation stages (doc 05 §11.2 and §11.3) ---------------------------------------------------

_SHARED_TAIL = (
    (S.FULL_EVALUATION, S.TRIAGE),
    (S.LIGHT_EVALUATION, S.TRIAGE),
    (S.TRIAGE, S.REFINING),
    (S.REFINING, S.LIGHT_EVALUATION),
    (S.TRIAGE, S.GENERATING_CANDIDATES),
    (S.GENERATING_CANDIDATES, S.PRESCREENING),
    (S.PRESCREENING, S.LIGHT_EVALUATION),
    (S.TRIAGE, S.FULL_EVALUATION),
    (S.TRIAGE, S.RANKING),
    (S.RANKING, S.REPORTING),
    (S.REPORTING, S.RESULTS_READY),
    (S.RESULTS_READY, S.WRITING_PROPOSAL),
    (S.WRITING_PROPOSAL, S.RESULTS_READY),
    (S.RESULTS_READY, S.FULL_EVALUATION),
)

STAGE_TRANSITIONS: dict[Mode, dict[StrEnum, frozenset[StrEnum]]] = {
    Mode.EVALUATE: _table(  # Workflow A, doc 05 §11.2
        (S.INTAKE, S.UNDERSTANDING),
        (S.UNDERSTANDING, S.AWAITING_CLARIFICATION),
        (S.AWAITING_CLARIFICATION, S.FULL_EVALUATION),
        *_SHARED_TAIL,
    ),
    Mode.DISCOVER: _table(  # Workflow B, doc 05 §11.3
        (S.INTAKE, S.UNDERSTANDING),
        (S.UNDERSTANDING, S.AWAITING_CLARIFICATION),
        (S.AWAITING_CLARIFICATION, S.LANDSCAPE_RETRIEVAL),
        (S.LANDSCAPE_RETRIEVAL, S.LANDSCAPE_READING),
        (S.LANDSCAPE_READING, S.OPPORTUNITY_MINING),
        (S.OPPORTUNITY_MINING, S.GENERATING_CANDIDATES),
        *_SHARED_TAIL,
        (S.RESULTS_READY, S.GENERATING_CANDIDATES),  # the user steers
    ),
}

# doc 05 §11.6: when a budget or the time limit runs out, any working stage after
# clarification may finish early through ranking -> reporting -> results_ready.
BUDGET_STOP_FROM = frozenset({
    S.LANDSCAPE_RETRIEVAL, S.LANDSCAPE_READING, S.OPPORTUNITY_MINING, S.GENERATING_CANDIDATES,
    S.PRESCREENING, S.LIGHT_EVALUATION, S.REFINING, S.FULL_EVALUATION, S.TRIAGE,
})

# doc 05 §11.1: the investigation status that goes with each stage while the workflow runs
STAGE_STATUS: dict[Stage, InvestigationStatus] = {
    stage: InvestigationStatus.RUNNING for stage in Stage
} | {
    S.AWAITING_CLARIFICATION: InvestigationStatus.CLARIFYING,
    S.RESULTS_READY: InvestigationStatus.COMPLETED,
}

# Statuses after which an investigation never moves again (doc 05 §11.6: cancel, mandatory failure)
FINAL_INVESTIGATION_STATUSES = frozenset({InvestigationStatus.FAILED, InvestigationStatus.CANCELLED})


def stages_reachable(mode: Mode) -> frozenset[Stage]:
    table = STAGE_TRANSITIONS[Mode(mode)]
    return frozenset(table) | frozenset().union(*table.values())


def check_stage(mode: str, current: str, target: str, *, budget_stop: bool = False) -> Stage:
    """The target stage if `current -> target` is allowed in this mode's workflow; else IllegalTransition.

    budget_stop=True also allows the early finish to `ranking` (doc 05 §11.6).
    """
    mode, current, target = Mode(mode), Stage(current), Stage(target)
    if target in STAGE_TRANSITIONS[mode].get(current, frozenset()):
        return target
    if budget_stop and target == S.RANKING and current in BUDGET_STOP_FROM and current in stages_reachable(mode):
        return target
    workflow = "Workflow A" if mode == Mode.EVALUATE else "Workflow B"
    raise IllegalTransition(f"{workflow}: stage {current} -> {target} is not in the state graph (doc 05 §11)")


# --- evaluation steps (doc 05 §11.4) -----------------------------------------------------------------

# A new evaluation starts at normalizing; a full evaluation extending a light one at planning_queries.
EVALUATION_START_STEPS = frozenset({E.NORMALIZING, E.PLANNING_QUERIES})

STEP_TRANSITIONS = _table(
    (E.NORMALIZING, E.PLANNING_QUERIES),
    (E.PLANNING_QUERIES, E.RETRIEVING),
    (E.RETRIEVING, E.SCREENING),
    (E.SCREENING, E.READING),
    (E.READING, E.ASSESSING_NOVELTY),
    (E.ASSESSING_NOVELTY, E.SKEPTIC_REVIEW),
    (E.SKEPTIC_REVIEW, E.REVISING_NOVELTY),
    (E.REVISING_NOVELTY, E.SKEPTIC_REVIEW),
    (E.REVISING_NOVELTY, E.ASSESSING_VALUE_FEASIBILITY),
    (E.SKEPTIC_REVIEW, E.ASSESSING_VALUE_FEASIBILITY),
    (E.ASSESSING_VALUE_FEASIBILITY, E.CONCLUDING),
    (E.CONCLUDING, E.DONE),
)


def check_step(current: str | None, target: str) -> EvaluationStep:
    """The target step if allowed; current=None means the evaluation is being created."""
    target = EvaluationStep(target)
    allowed = EVALUATION_START_STEPS if current is None else STEP_TRANSITIONS.get(EvaluationStep(current), frozenset())
    if target not in allowed:
        raise IllegalTransition(f"evaluation step {current or '(new)'} -> {target} is not in the state graph "
                                "(doc 05 §11.4)")
    return target


# --- candidate statuses (doc 05 §11.5) ---------------------------------------------------------------

CANDIDATE_START_STATUS = C.PROPOSED

CANDIDATE_TRANSITIONS = _table(
    (C.PROPOSED, C.SCREENED_OUT),
    (C.PROPOSED, C.LIGHT_EVALUATING),
    (C.PROPOSED, C.FULL_EVALUATING),
    (C.LIGHT_EVALUATING, C.ADVANCED),
    (C.LIGHT_EVALUATING, C.NEEDS_REFINEMENT),
    (C.LIGHT_EVALUATING, C.REJECTED),
    (C.ADVANCED, C.FULL_EVALUATING),
    (C.FULL_EVALUATING, C.FINALIST),
    (C.FULL_EVALUATING, C.NEEDS_REFINEMENT),
    (C.FULL_EVALUATING, C.REJECTED),
    (C.NEEDS_REFINEMENT, C.REFINED),
    (C.NEEDS_REFINEMENT, C.REJECTED),
    (C.FINALIST, C.SELECTED),
    (C.SELECTED, C.PROPOSAL_READY),
    (C.SELECTED, C.FULL_EVALUATING),
)

FINAL_CANDIDATE_STATUSES = frozenset({C.SCREENED_OUT, C.REFINED, C.REJECTED, C.PROPOSAL_READY})


def check_candidate_status(current: str, target: str) -> CandidateStatus:
    """The target status if `current -> target` is allowed; else IllegalTransition."""
    current, target = CandidateStatus(current), CandidateStatus(target)
    if target not in CANDIDATE_TRANSITIONS.get(current, frozenset()):
        final = " (a final status)" if current in FINAL_CANDIDATE_STATUSES else ""
        raise IllegalTransition(f"candidate status {current}{final} -> {target} is not in the state graph "
                                "(doc 05 §11.5)")
    return target
