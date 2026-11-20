"""Tests for the state graph in code (src/ai/workflows/states.py) against doc 05 §11. No database."""

import itertools
import re
from pathlib import Path

import pytest

from src.ai.workflows.states import (
    BUDGET_STOP_FROM,
    CANDIDATE_START_STATUS,
    CANDIDATE_TRANSITIONS,
    EVALUATION_START_STEPS,
    STAGE_STATUS,
    STAGE_TRANSITIONS,
    STEP_TRANSITIONS,
    CandidateStatus,
    EvaluationStep,
    IllegalTransition,
    Mode,
    Stage,
    check_candidate_status,
    check_stage,
    check_step,
)

DOC_05 = (Path(__file__).resolve().parents[2] / "docs" / "05-workflows-and-orchestration.md").read_text(encoding="utf-8")


def section(heading: str) -> str:
    return DOC_05.split(heading, 1)[1].split("\n### ", 1)[0]


def diagram_edges(heading: str) -> set[tuple[str, str]]:
    """The `a --> b` lines of the section's mermaid state diagram; [*] stands for start/end."""
    diagram = section(heading).split("```mermaid", 1)[1].split("```", 1)[0]
    return set(re.findall(r"^\s*(\[\*\]|[a-z_]+) --> (\[\*\]|[a-z_]+)", diagram, flags=re.MULTILINE))


def table_edges(table) -> set[tuple[str, str]]:
    return {(str(source), str(target)) for source, targets in table.items() for target in targets}


def without_start_and_end(edges):
    return {(a, b) for a, b in edges if "[*]" not in (a, b)}


# --- the code's tables equal the doc's diagrams --------------------------------------------------------

@pytest.mark.parametrize(("mode", "heading"), [(Mode.EVALUATE, "### 11.2"), (Mode.DISCOVER, "### 11.3")])
def test_stage_tables_match_doc_05(mode, heading):
    documented = diagram_edges(heading)
    assert table_edges(STAGE_TRANSITIONS[mode]) == without_start_and_end(documented)
    assert ("[*]", "intake") in documented


def test_step_table_matches_doc_05():
    documented = diagram_edges("### 11.4")
    assert table_edges(STEP_TRANSITIONS) == without_start_and_end(documented)
    assert {b for a, b in documented if a == "[*]"} == set(EVALUATION_START_STEPS)


def test_candidate_table_matches_doc_05():
    documented = diagram_edges("### 11.5")
    assert table_edges(CANDIDATE_TRANSITIONS) == without_start_and_end(documented)
    assert {b for a, b in documented if a == "[*]"} == {CANDIDATE_START_STATUS}


def test_step_and_candidate_names_match_doc_05_tables():
    steps = re.findall(r"^\| `([a-z_]+)` \|", section("### 11.4"), flags=re.MULTILINE)
    statuses = re.findall(r"^\| `([a-z_]+)` \|", section("### 11.5"), flags=re.MULTILINE)
    assert tuple(steps) == tuple(EvaluationStep)
    assert tuple(statuses) == tuple(CandidateStatus)


def test_stage_statuses_match_doc_05():
    rows = re.findall(r"^\| `([a-z_]+)` \|.*\| ([a-z]+) \|$", section("### 11.1"), flags=re.MULTILINE)
    assert {stage: status for stage, status in rows} == {str(k): str(v) for k, v in STAGE_STATUS.items()}


# --- every listed transition is allowed, every other one rejected --------------------------------------

def all_pairs(names):
    return list(itertools.product(names, repeat=2))


@pytest.mark.parametrize("mode", list(Mode))
def test_stage_transitions_allowed_exactly_as_listed(mode):
    for current, target in all_pairs(Stage):
        if target in STAGE_TRANSITIONS[mode].get(current, ()):
            assert check_stage(mode, current, target) == target
        else:
            with pytest.raises(IllegalTransition):
                check_stage(mode, current, target)


def test_step_transitions_allowed_exactly_as_listed():
    for current, target in all_pairs(EvaluationStep):
        if target in STEP_TRANSITIONS.get(current, ()):
            assert check_step(current, target) == target
        else:
            with pytest.raises(IllegalTransition):
                check_step(current, target)
    assert check_step(None, "normalizing") == EvaluationStep.NORMALIZING
    with pytest.raises(IllegalTransition, match=r"\(new\) -> reading"):
        check_step(None, "reading")


def test_candidate_transitions_allowed_exactly_as_listed():
    for current, target in all_pairs(CandidateStatus):
        if target in CANDIDATE_TRANSITIONS.get(current, ()):
            assert check_candidate_status(current, target) == target
        else:
            with pytest.raises(IllegalTransition):
                check_candidate_status(current, target)
    with pytest.raises(IllegalTransition, match="a final status"):
        check_candidate_status("rejected", "light_evaluating")


def test_workflow_a_has_no_landscape_stages():
    with pytest.raises(IllegalTransition, match="Workflow A: stage awaiting_clarification -> landscape_retrieval"):
        check_stage("evaluate", "awaiting_clarification", "landscape_retrieval")
    assert check_stage("discover", "awaiting_clarification", "landscape_retrieval") == Stage.LANDSCAPE_RETRIEVAL


def test_unknown_names_are_rejected():
    with pytest.raises(ValueError):
        check_stage("evaluate", "intake", "thinking_hard")


# --- the budget stop (doc 05 §11.6) ---------------------------------------------------------------------

def test_budget_stop_finishes_early_through_ranking_only_when_asked():
    with pytest.raises(IllegalTransition):
        check_stage("evaluate", "full_evaluation", "ranking")  # not a normal move
    assert check_stage("evaluate", "full_evaluation", "ranking", budget_stop=True) == Stage.RANKING
    assert check_stage("discover", "landscape_reading", "ranking", budget_stop=True) == Stage.RANKING


@pytest.mark.parametrize("current", ["intake", "understanding", "awaiting_clarification", "reporting",
                                     "results_ready", "writing_proposal"])
def test_budget_stop_is_not_allowed_before_clarification_or_after_ranking(current):
    with pytest.raises(IllegalTransition):
        check_stage("discover", current, "ranking", budget_stop=True)


def test_budget_stop_only_from_stages_of_the_mode():
    assert Stage.LANDSCAPE_READING in BUDGET_STOP_FROM
    with pytest.raises(IllegalTransition):
        check_stage("evaluate", "landscape_reading", "ranking", budget_stop=True)


def test_budget_stop_cannot_go_anywhere_else():
    with pytest.raises(IllegalTransition):
        check_stage("evaluate", "full_evaluation", "reporting", budget_stop=True)
