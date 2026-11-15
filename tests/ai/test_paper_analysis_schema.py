"""Tests for the Paper Reader's output shape (src/ai/schemas/paper_analysis.py)."""

import pytest
from pydantic import ValidationError

from src.ai.schemas.paper_analysis import (
    MAX_ITEMS,
    MAX_QUOTE_CHARS,
    SCHEMA_VERSION,
    PaperAnalysisOutput,
)

# A realistic output for the Ragas paper (arXiv 2309.15217, in tests/fixtures/arxiv/search.xml),
# written the way the Paper Reader should fill it from the abstract alone.
RAGAS_FROM_ABSTRACT = {
    "research_questions": [{
        "statement": "How to evaluate RAG pipelines without ground-truth human annotations.",
        "quote": {"text": "a framework for reference-free evaluation of Retrieval Augmented Generation (RAG) "
                          "pipelines", "chunk_id": None},
    }],
    "tasks": ["Retrieval Augmented Generation"],
    "setting": None,
    "methods": ["Ragas"],
    "datasets": [],
    "metrics": [],
    "baselines": [],
    "findings": [],
    "limitations": [],  # an abstract rarely admits limitations: empty is correct
    "future_work": [],
    "assumptions": [],
    "contribution_type": "method",
}


def test_realistic_output_is_valid():
    analysis = PaperAnalysisOutput.model_validate(RAGAS_FROM_ABSTRACT)
    assert analysis.contribution_type == "method"
    assert analysis.research_questions[0].quote.chunk_id is None  # from the abstract


def test_only_contribution_type_is_required():
    analysis = PaperAnalysisOutput.model_validate({"contribution_type": "unknown"})
    assert analysis.findings == [] and analysis.setting is None


def test_unknown_fields_are_refused():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PaperAnalysisOutput.model_validate({**RAGAS_FROM_ABSTRACT, "novelty_score": 9})
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PaperAnalysisOutput.model_validate({
            "contribution_type": "method",
            "limitations": [{"statement": "x", "quote": {"text": "x", "verified": True}}],  # trust field: refused
        })


@pytest.mark.parametrize(
    "change, location",
    [
        ({"contribution_type": "breakthrough"}, "contribution_type"),
        ({"methods": ["   "]}, "methods"),
        ({"findings": [{"setting": "s", "claim": "c", "direction": "up", "quote": {"text": "q"}}]}, "direction"),
        ({"limitations": [{"statement": "s", "quote": {"text": ""}}]}, "text"),
        ({"limitations": [{"statement": "s", "quote": {"text": "x" * (MAX_QUOTE_CHARS + 1)}}]}, "text"),
        ({"datasets": ["d"] * (MAX_ITEMS + 1)}, "datasets"),
    ],
)
def test_invalid_values_are_refused(change, location):
    with pytest.raises(ValidationError, match=location):
        PaperAnalysisOutput.model_validate({**RAGAS_FROM_ABSTRACT, **change})


def test_text_is_trimmed():
    analysis = PaperAnalysisOutput.model_validate({**RAGAS_FROM_ABSTRACT, "methods": ["  Ragas  "]})
    assert analysis.methods == ["Ragas"]


def test_quotes_and_names_are_listed_with_their_locations():
    analysis = PaperAnalysisOutput.model_validate({
        **RAGAS_FROM_ABSTRACT,
        "findings": [{"setting": "s", "claim": "c", "direction": "positive",
                      "quote": {"text": "finding quote", "chunk_id": "c_1"}}],
        "datasets": ["WikiEval"],
    })
    assert [(where, quote.text) for where, quote in analysis.quotes()] == [
        ("research_questions[0]", RAGAS_FROM_ABSTRACT["research_questions"][0]["quote"]["text"]),
        ("findings[0]", "finding quote"),
    ]
    assert list(analysis.names()) == [
        ("tasks[0]", "Retrieval Augmented Generation"), ("methods[0]", "Ragas"), ("datasets[0]", "WikiEval"),
    ]


def test_json_schema_shown_to_the_model():
    schema = PaperAnalysisOutput.model_json_schema()
    properties = schema["properties"]

    # Instructions travel with the schema...
    assert "EXACTLY, word for word" in schema["$defs"]["EvidenceQuote"]["properties"]["text"]["description"]
    assert "Never add ones you know from elsewhere" in properties["datasets"]["description"]
    # ...unknown fields are forbidden...
    assert schema["additionalProperties"] is False
    # ...and no trust or identity field is ever asked of the model.
    every_field = set(properties) | {f for d in schema["$defs"].values() for f in d.get("properties", {})}
    assert not every_field & {"paper_id", "quote_id", "verified", "verification_score", "source_level", "tier"}


def test_schema_version_is_set():
    assert SCHEMA_VERSION == "paper-analysis-v1"
