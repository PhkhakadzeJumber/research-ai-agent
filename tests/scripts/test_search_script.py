"""Tests for scripts/search.py: argument parsing and output formatting (no network)."""

from src.retrieval.cache import FileResponseCache
from src.retrieval.records import ExternalIds, PaperRecord, SearchRequest, SearchResult, SourceReport
from scripts.search import format_result, main, parse_args


def make_result():
    papers = (
        PaperRecord(
            ids=ExternalIds(doi="10.1/pub", arxiv_id="2405.01234"), title="A Published Paper About RAG",
            sources=("openalex", "arxiv"), tier=1, year=2024, authors=("Ada Lovelace", "Alan Turing"),
            citation_count=57, open_access_pdf_url="https://arxiv.org/pdf/2405.01234",
        ),
        PaperRecord(
            ids=ExternalIds(arxiv_id="2501.00001"), title="A Preprint " + "With A Very Long Title " * 6,
            sources=("arxiv",), tier=2, year=2025,
        ),
    )
    return SearchResult(
        request=SearchRequest(query="rag hallucination", year_from=2021, limit=5),
        papers=papers,
        reports=(
            SourceReport(source="openalex", ok=True, result_count=1, latency_ms=812),
            SourceReport(source="arxiv", ok=False, error="HTTP 503 (gave up after 4 attempts)"),
        ),
        retrieved_count=3,
    )


def test_format_result_shows_papers_reports_and_counts(tmp_path):
    output = format_result(make_result(), FileResponseCache(tmp_path))

    assert 'Search: "rag hallucination"  (up to 5 per source, years 2021–…)' in output
    assert "A Published Paper About RAG" in output
    assert "published" in output and "preprint" in output
    assert "openalex+arxiv" in output
    assert "Ada Lovelace et al." in output
    assert "https://arxiv.org/pdf/2405.01234" in output
    assert "unknown author" in output
    assert "…" in output  # the long title was shortened
    assert "openalex ok: 1 results (812 ms)" in output
    assert "arxiv FAILED: HTTP 503 (gave up after 4 attempts)" in output
    assert "Records: 3 retrieved -> 2 papers (1 duplicates merged)" in output
    assert "Cache: 0 hit(s), 0 miss(es)" in output


def test_format_result_without_papers_or_cache():
    result = SearchResult(request=SearchRequest(query="x"), reports=(SourceReport(source="arxiv", ok=True),))
    output = format_result(result, None)
    assert "No papers found." in output
    assert "Cache: off" in output


def test_parse_args():
    args = parse_args(["rag", "--limit", "5", "--from", "2022", "--to", "2026", "--sources", "arxiv", "--no-cache"])
    assert (args.query, args.limit, args.year_from, args.year_to, args.sources, args.no_cache) == (
        "rag", 5, 2022, 2026, "arxiv", True
    )


def test_invalid_input_exits_with_code_2(capsys):
    assert main(["   "]) == 2  # empty query: rejected before any request is made
    assert "Error:" in capsys.readouterr().err
    assert main(["rag", "--sources", "nosuchsource", "--no-cache"]) == 2
