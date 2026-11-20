"""Tests for scripts/evaluate.py: arguments, input checks and output. No database, no network."""

import pytest

from scripts.evaluate import InputError, format_reading, investigation_input, main, parse_args
from src.ai.schemas.structured_idea import StructuredIdeaOutput
from tests.ai.test_structured_idea_schema import VALID


def test_arguments_and_input():
    args = parse_args(["  my idea  ", "--field", "NLP", "--complexity", "beginner", "--seed", "2401.12345",
                       "--seed", "Some title", "--constraint", "available time = 8 weeks"])
    assert args.constraint == [("available time", "8 weeks")]
    assert investigation_input(args) == {"idea": "my idea", "field": "NLP",
                                         "seed_papers": ["2401.12345", "Some title"]}
    assert parse_args(["idea"]).complexity == "intermediate"  # doc 02 default


@pytest.mark.parametrize("bad", [["idea", "--constraint", "no-equals-sign"], ["idea", "--complexity", "expert"]])
def test_invalid_arguments_exit(bad):
    with pytest.raises(SystemExit):
        parse_args(bad)


@pytest.mark.parametrize(("idea", "message"), [("   ", "empty"), ("x" * 2001, "limit is 2000")])
def test_invalid_ideas(idea, message):
    with pytest.raises(InputError, match=message):
        investigation_input(parse_args([idea]))


def test_invalid_idea_exits_with_code_2(capsys):
    assert main(["   "]) == 2
    assert "The idea is empty" in capsys.readouterr().err


def test_format_reading():
    text = format_reading(StructuredIdeaOutput.model_validate(VALID))
    assert "RQ:     Does RAG reduce" in text
    assert "  method       * retrieval-augmented generation" in text
    assert "  evaluation     hallucination rate vs closed-book baseline  [proposed]" in text  # not key, proposed
    assert "Key facets Eureka proposed" not in text  # the only proposal isn't key
    assert "Assumed:\n  - small means under 3B parameters" in text
    assert "Vague:\n  - how hallucination is measured is unspecified" in text
    assert "  1. Which hallucination measure?\n     → an automatic measure" in text


def test_proposed_key_facets_are_pointed_out():
    facets = [{**f, "stated_by_user": f["kind"] == "problem"} for f in VALID["facets"]]
    text = format_reading(StructuredIdeaOutput.model_validate({**VALID, "facets": facets}))
    assert "  method       * retrieval-augmented generation  [proposed]" in text
    assert "Key facets Eureka proposed: you'll be asked to confirm these" in text


def test_format_reading_without_questions():
    text = format_reading(StructuredIdeaOutput.model_validate({**VALID, "clarifying_questions": [],
                                                               "assumptions": [], "vagueness_flags": []}))
    assert "Questions: none" in text and "Assumed" not in text and "Vague" not in text


def test_a_revised_reading_is_shown_without_the_questions_block():
    text = format_reading(StructuredIdeaOutput.model_validate({**VALID, "clarifying_questions": []}), questions=False)
    assert "Questions" not in text and text.endswith("- how hallucination is measured is unspecified")
