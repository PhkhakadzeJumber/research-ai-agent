"""Tests for the Idea Analyst's output shape (src/ai/schemas/structured_idea.py)."""

import copy

import pytest
from pydantic import ValidationError

from src.ai.schemas.structured_idea import FACET_KINDS, StructuredIdeaOutput

VALID = {
    "title": "RAG for hallucination reduction in Georgian QA with small LMs",
    "research_question": "Does RAG reduce hallucination of sub-3B LMs in Georgian open-domain QA?",
    "facets": [
        {"kind": "problem", "text": "hallucination of small LMs in low-resource QA", "is_key": True},
        {"kind": "method", "text": "retrieval-augmented generation", "is_key": True},
        {"kind": "data_setting", "text": "Georgian open-domain QA", "is_key": True},
        {"kind": "evaluation", "text": "hallucination rate vs closed-book baseline", "is_key": False},
        {"kind": "contribution", "text": "first evaluation of RAG for sub-3B models in Georgian", "is_key": False},
    ],
    "assumptions": ["small means under 3B parameters"],
    "vagueness_flags": ["how hallucination is measured is unspecified"],
    "clarifying_questions": [{"question": "Which hallucination measure?", "if_skipped": "an automatic measure"}],
}


def changed(**changes):
    data = copy.deepcopy(VALID)
    data.update(changes)
    return data


def test_a_complete_reading_is_valid():
    reading = StructuredIdeaOutput.model_validate(VALID)
    assert [facet.kind for facet in reading.facets] == list(FACET_KINDS)
    assert reading.facets_of("method")[0].text == "retrieval-augmented generation"


def test_lists_may_be_empty_for_a_clear_idea():
    reading = StructuredIdeaOutput.model_validate(changed(assumptions=[], vagueness_flags=[], clarifying_questions=[]))
    assert reading.clarifying_questions == []


def test_every_facet_kind_is_required():
    facets = [f for f in VALID["facets"] if f["kind"] != "evaluation"]
    facets.append({"kind": "method", "text": "dense retrieval", "is_key": False})
    with pytest.raises(ValidationError, match="missing evaluation"):
        StructuredIdeaOutput.model_validate(changed(facets=facets))


def test_at_most_two_facets_per_kind():
    extra = [{"kind": "method", "text": f"method {n}", "is_key": False} for n in range(2)]
    with pytest.raises(ValidationError, match="too many method"):
        StructuredIdeaOutput.model_validate(changed(facets=VALID["facets"] + extra))


def test_at_least_one_key_facet():
    facets = [{**f, "is_key": False} for f in VALID["facets"]]
    with pytest.raises(ValidationError, match="at least one facet as key"):
        StructuredIdeaOutput.model_validate(changed(facets=facets))


def test_duplicate_facets_are_refused():
    facets = VALID["facets"] + [{"kind": "problem", "text": "Retrieval-Augmented Generation", "is_key": False}]
    with pytest.raises(ValidationError, match="each facet must be different"):
        StructuredIdeaOutput.model_validate(changed(facets=facets))


def test_at_most_three_questions():
    questions = [{"question": f"Q{n}?", "if_skipped": f"A{n}"} for n in range(4)]
    with pytest.raises(ValidationError, match="clarifying_questions"):
        StructuredIdeaOutput.model_validate(changed(clarifying_questions=questions))


def test_every_question_needs_its_skip_assumption():
    with pytest.raises(ValidationError, match="if_skipped"):
        StructuredIdeaOutput.model_validate(changed(clarifying_questions=[{"question": "Which languages?"}]))


@pytest.mark.parametrize("bad", [
    {"facets": [{"kind": "dataset", "text": "x", "is_key": True}]},  # unknown kind
    {"title": "   "},  # empty after stripping
    {"title": "x" * 121},
    {"verdict": "novel"},  # unknown field: the model can't add judgements
])
def test_malformed_readings_are_refused(bad):
    with pytest.raises(ValidationError):
        StructuredIdeaOutput.model_validate(changed(**bad))
