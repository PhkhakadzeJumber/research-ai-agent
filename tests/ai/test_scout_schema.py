"""Tests for the Scout's output schema (src/ai/schemas/scout.py). No database, no Claude."""

import copy

import pytest
from pydantic import ValidationError

from src.ai.schemas.scout import ScoutOutput

FACETS = [{"kind": kind, "text": f"{kind} text", "is_key": kind == "problem"}
          for kind in ("problem", "method", "data_setting", "evaluation", "contribution")]


def candidate(title="Idea", strategy="limitation", sources=("Q1", "Q2"), **changes):
    data = {
        "title": title, "research_question": "Does X improve Y?", "origin_strategy": strategy,
        "motivation": "P1 and P2 report the problem.",
        "inspirations": [{"source": s, "role": "gap_hint", "note": "n"} for s in sources],
        "potential_contribution": "c", "facets": copy.deepcopy(FACETS),
        "fit": {"compute_tier": "T1", "data_source": "public", "timeline_weeks": 8, "complexity_note": "fits"},
        "risks": ["may exist"],
    }
    data.update(changes)
    return data


def candidate_set(n=8, strategies=("limitation", "future_work", "practical_problem")):
    return {"candidates": [candidate(f"Idea {i}", strategies[i % len(strategies)]) for i in range(n)]}


def test_a_valid_set():
    output = ScoutOutput.model_validate(candidate_set())
    assert len(output.candidates) == 8 and output.candidates[0].hypothesis is None


@pytest.mark.parametrize(("data", "message"), [
    (candidate_set(7), "at least 8"),
    (candidate_set(13), "at most 12"),
    (candidate_set(strategies=("limitation", "future_work")), "at least 3 different origin strategies"),
    ({"candidates": [candidate("Same", s) for s in ("limitation", "future_work", "practical_problem") * 3]},
     "different title"),
    ({"candidates": [candidate(sources=("Q1",)), *candidate_set()["candidates"][1:]]}, "at least 2"),
    ({"candidates": [candidate(sources=("Q1", "P3")), *candidate_set()["candidates"][1:]]}, "pattern"),
    ({"candidates": [candidate(facets=FACETS[:4]), *candidate_set()["candidates"][1:]]}, "missing contribution"),
    ({"candidates": [candidate(strategy="refinement"), *candidate_set()["candidates"][1:]]}, "origin_strategy"),
    ({"candidates": [candidate(fit={"compute_tier": "T5", "data_source": "d", "timeline_weeks": 8,
                                    "complexity_note": "n"}), *candidate_set()["candidates"][1:]]}, "compute_tier"),
])
def test_broken_sets_are_refused(data, message):
    with pytest.raises(ValidationError, match=message):
        ScoutOutput.model_validate(data)


def test_replenish_and_alternatives_sets():
    from src.ai.schemas.scout import AlternativesOutput, ReplenishOutput

    two = {"candidates": [candidate(f"Idea {i}", ("limitation", "future_work")[i % 2]) for i in range(4)]}
    assert len(ReplenishOutput.model_validate(two).candidates) == 4
    one_strategy = {"candidates": [candidate(f"Idea {i}", "limitation") for i in range(3)]}
    with pytest.raises(ValidationError, match="at least 2 different origin strategies"):
        ReplenishOutput.model_validate(one_strategy)
    with pytest.raises(ValidationError, match="at most 6"):
        ReplenishOutput.model_validate({"candidates": [candidate(f"I{i}", ("limitation", "future_work")[i % 2])
                                                       for i in range(7)]})
    assert len(AlternativesOutput.model_validate(one_strategy).candidates) == 3  # no diversity rule
    with pytest.raises(ValidationError, match="at most 5"):
        AlternativesOutput.model_validate({"candidates": [candidate(f"I{i}") for i in range(6)]})
    with pytest.raises(ValidationError, match="at least 3"):
        AlternativesOutput.model_validate({"candidates": [candidate(f"I{i}") for i in range(2)]})
