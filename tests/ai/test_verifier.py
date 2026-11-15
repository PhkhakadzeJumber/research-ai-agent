"""Tests for the quote verifier (src/ai/evidence/verifier.py): pure code, no LLM, no database."""

import time
from pathlib import Path

import pytest

from src.ai.evidence.verifier import SIMILARITY_THRESHOLD, match_quote, normalize, verify_analysis
from src.ai.schemas.paper_analysis import PaperAnalysisOutput
from src.retrieval.sources.arxiv import parse_feed
from tests.ai.test_paper_analysis_schema import RAGAS_FROM_ABSTRACT

SOURCE = (
    "We evaluate retrieval-augmented generation on six low-resource languages. Our results show that "
    "retrieval reduces hallucinations of small language models by a large margin, but the method is not "
    "effective for models with fewer than one billion parameters."
)


# --- tolerated: formatting differences ---------------------------------------------------------------

@pytest.mark.parametrize(
    "quote",
    [
        "retrieval reduces hallucinations of small language models",
        "Retrieval Reduces   Hallucinations\nof small language models",            # case, spacing, line break
        "“retrieval reduces hallucinations of small language models”",   # curly quote marks around
        "retrieval reduces hallucinations of small language models.",            # trailing period
    ],
)
def test_formatting_differences_are_tolerated(quote):
    result = match_quote(quote, SOURCE)
    assert result.verified and result.method == "exact" and result.score == 1.0


def test_words_hyphenated_across_a_line_break_match():
    pdf_text = "Retrieval reduces hallu-\ncinations of small language models."
    assert match_quote("retrieval reduces hallucinations of small language models", pdf_text).verified


def test_ligatures_dashes_and_soft_hyphens_are_normalized():
    pdf_text = "The ﬁne‐tuned model — trained on Swahili — out­performs the baseline."
    assert match_quote("the fine-tuned model - trained on Swahili - outperforms the baseline", pdf_text).verified


def test_a_tiny_spelling_difference_in_a_long_quote_is_a_fuzzy_match():
    source = "We optimise the retriever jointly with the generator using a contrastive objective on all tasks."
    result = match_quote("We optimize the retriever jointly with the generator using a contrastive objective "
                         "on all tasks.", source)
    assert result.verified and result.method == "fuzzy"
    assert SIMILARITY_THRESHOLD <= result.score < 1.0


# --- not tolerated: content differences -----------------------------------------------------------------

def test_changed_words_are_rejected():
    result = match_quote("retrieval eliminates hallucinations of large language models", SOURCE)
    assert not result.verified and "not found" in result.reason


def test_a_flipped_negation_is_rejected_even_when_almost_identical():
    quote = ("but the method is effective for models with fewer than one billion parameters")  # "not" removed
    result = match_quote(quote, SOURCE)
    assert result.score >= SIMILARITY_THRESHOLD  # textually almost identical...
    assert not result.verified and "negation" in result.reason  # ...but it says the opposite


def test_passages_joined_with_an_ellipsis_are_rejected():
    result = match_quote("We evaluate retrieval-augmented generation ... by a large margin", SOURCE)
    assert not result.verified and "ellipsis" in result.reason


def test_invented_quotes_are_rejected():
    assert not match_quote("a completely different sentence that was never written", SOURCE).verified


def test_missing_source_is_rejected():
    assert match_quote("anything", None).reason == "no source text to check against"


# --- whole analyses: quotes are checked where they claim to come from --------------------------------------

def make_analysis(**fields):
    return PaperAnalysisOutput.model_validate({"contribution_type": "empirical_study", **fields})


def limitation(text, chunk_id=None):
    return {"statement": "s", "quote": {"text": text, "chunk_id": chunk_id}}


CHUNKS = {"c_1": "Limitations. We only evaluate models with at least seven billion parameters."}


def test_quotes_are_checked_at_their_stated_location():
    analysis = make_analysis(limitations=[
        limitation("We only evaluate models with at least seven billion parameters", "c_1"),  # right chunk
        limitation("We only evaluate models with at least seven billion parameters"),  # claims abstract: wrong
        limitation("retrieval reduces hallucinations", "c_9"),  # unknown chunk
    ])
    report = verify_analysis(analysis, abstract=SOURCE, chunks=CHUNKS)

    assert [check.verified for check in report.quotes] == [True, False, False]
    assert "not found" in report.quotes[1].reason
    assert report.quotes[2].reason == "cites unknown chunk 'c_9'"
    assert report.verification_rate == pytest.approx(1 / 3)


def test_abstract_quotes_without_an_abstract_are_rejected():
    report = verify_analysis(make_analysis(limitations=[limitation("x y z")]), abstract=None)
    assert report.quotes[0].reason == "cites the abstract, but the paper has no abstract"


def test_names_must_appear_somewhere_in_the_text():
    analysis = make_analysis(tasks=["Retrieval Augmented Generation"], datasets=["AfriQA"])
    report = verify_analysis(analysis, abstract=SOURCE, chunks=CHUNKS)
    found = {check.name: check.found for check in report.names}
    assert found == {"Retrieval Augmented Generation": True, "AfriQA": False}  # hyphen-insensitive; invented name
    assert [check.name for check in report.missing_names] == ["AfriQA"]


def test_no_quotes_means_no_rate():
    assert verify_analysis(make_analysis(), abstract=SOURCE).verification_rate is None


# --- real data ---------------------------------------------------------------------------------------------

def test_the_ragas_example_verifies_against_the_real_abstract():
    feed = (Path(__file__).parent.parent / "fixtures" / "arxiv" / "search.xml").read_text(encoding="utf-8")
    ragas = next(paper for paper in parse_feed(feed) if paper.ids.arxiv_id == "2309.15217")
    report = verify_analysis(PaperAnalysisOutput.model_validate(RAGAS_FROM_ABSTRACT), abstract=ragas.abstract)

    assert report.verification_rate == 1.0
    assert report.missing_names == ()


def test_normalize():
    assert normalize("  The ﬁne–tuned\n  Model  ") == "the fine-tuned model"


def test_long_texts_are_checked_quickly():
    chunk = " ".join(f"sentence number {i} talks about retrieval and generation." for i in range(120))
    quote = "sentence number 87 talks about retrieval and generations"  # one small change
    start = time.perf_counter()
    result = match_quote(quote, chunk)
    assert time.perf_counter() - start < 2.0
    assert result.verified and result.method == "fuzzy"
