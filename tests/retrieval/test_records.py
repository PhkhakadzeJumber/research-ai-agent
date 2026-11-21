"""Tests for src/retrieval/records.py: ID normalization and record validation."""

import pytest

from src.retrieval.records import (
    ExternalIds,
    PaperRecord,
    SearchRequest,
    SearchResult,
    SourceReport,
    normalize_arxiv_id,
    normalize_doi,
    normalize_openalex_id,
    normalize_s2_id,
)

# --- ID normalization -------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("10.18653/v1/2024.acl-long.1", "10.18653/v1/2024.acl-long.1"),
        ("https://doi.org/10.18653/V1/2024.ACL-LONG.1", "10.18653/v1/2024.acl-long.1"),
        ("http://dx.doi.org/10.1000/xyz", "10.1000/xyz"),
        ("DOI:10.1000/XYZ", "10.1000/xyz"),
        ("  10.1000/xyz  ", "10.1000/xyz"),
        ("not-a-doi", None),
        ("10.1000/", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_doi(raw, expected):
    assert normalize_doi(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2405.01234", "2405.01234"),
        ("2405.01234v3", "2405.01234"),
        ("arXiv:2405.01234v1", "2405.01234"),
        ("https://arxiv.org/abs/2405.01234v2", "2405.01234"),
        ("http://arxiv.org/pdf/2405.01234v2.pdf", "2405.01234"),
        ("1501.0001", "1501.0001"),
        ("hep-th/9901001v2", "hep-th/9901001"),
        ("math.AG/0601001", "math.ag/0601001"),
        ("12345", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_arxiv_id(raw, expected):
    assert normalize_arxiv_id(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://openalex.org/W2741809807", "W2741809807"),
        ("W2741809807", "W2741809807"),
        ("w2741809807", "W2741809807"),
        ("https://openalex.org/A123", None),  # an author ID, not a work
        (None, None),
    ],
)
def test_normalize_openalex_id(raw, expected):
    assert normalize_openalex_id(raw) == expected


def test_normalize_s2_id():
    valid = "A" * 40
    assert normalize_s2_id(valid) == "a" * 40
    assert normalize_s2_id("abc") is None


def test_external_ids_normalize_on_creation_and_drop_invalid_values():
    ids = ExternalIds(doi="https://doi.org/10.1/ABC", arxiv_id="garbage")
    assert ids.doi == "10.1/abc"
    assert ids.arxiv_id is None
    assert not ids.is_empty
    assert ExternalIds(arxiv_id="garbage").is_empty


# --- PaperRecord --------------------------------------------------------------

def make_paper(**overrides):
    values = dict(
        ids=ExternalIds(arxiv_id="2405.01234"),
        title="A  Paper\nTitle",
        sources=("arxiv",),
        tier=2,
    )
    values.update(overrides)
    return PaperRecord(**values)


def test_paper_cleans_text_fields():
    paper = make_paper(abstract="  ", venue=" ACL\n2024 ", authors=("Ada  Lovelace", "  ", "Alan Turing"))
    assert paper.title == "A Paper Title"
    assert paper.abstract is None
    assert paper.venue == "ACL 2024"
    assert paper.authors == ("Ada Lovelace", "Alan Turing")
    assert paper.first_author == "Ada Lovelace"


def test_paper_without_authors_has_no_first_author():
    assert make_paper().first_author is None


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"ids": ExternalIds()}, "external ID"),
        ({"title": "  \n "}, "title"),
        ({"sources": ()}, "source"),
        ({"tier": 3}, "tier"),
        ({"year": 1500}, "year"),
        ({"citation_count": -1}, "citation_count"),
        ({"open_access_pdf_url": "ftp://example.org/a.pdf"}, "http"),
    ],
)
def test_invalid_paper_records_are_rejected(overrides, message):
    with pytest.raises(ValueError, match=message):
        make_paper(**overrides)


def test_paper_records_are_immutable():
    paper = make_paper()
    with pytest.raises(AttributeError):
        paper.title = "changed"


# --- SearchRequest, SourceReport, SearchResult ---------------------------------

def test_search_request_cleans_query_and_has_defaults():
    request = SearchRequest(query="  small language models\n hallucination ")
    assert request.query == "small language models hallucination"
    assert request.limit == 25


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"query": "   "}, "empty"),
        ({"query": "x" * 501}, "longer"),
        ({"query": "x", "limit": 0}, "limit"),
        ({"query": "x", "limit": 201}, "limit"),
        ({"query": "x", "year_from": 2025, "year_to": 2020}, "year_from"),
    ],
)
def test_invalid_search_requests_are_rejected(kwargs, message):
    with pytest.raises(ValueError, match=message):
        SearchRequest(**kwargs)


def test_failed_source_report_needs_an_error():
    with pytest.raises(ValueError, match="went wrong"):
        SourceReport(source="arxiv", ok=False)


def test_search_result_lists_ok_and_failed_sources():
    result = SearchResult(
        request=SearchRequest(query="x"),
        papers=(make_paper(),),
        reports=(
            SourceReport(source="openalex", ok=True, result_count=1),
            SourceReport(source="arxiv", ok=False, error="timeout"),
        ),
        retrieved_count=1,
    )
    assert result.ok_sources == ("openalex",)
    assert result.failed_sources == ("arxiv",)
    assert result.all_failed is False
    assert result.duplicates_removed == 0


def test_search_result_counts_duplicates_and_detects_total_failure():
    request = SearchRequest(query="x")
    failed = SourceReport(source="arxiv", ok=False, error="timeout")

    assert SearchResult(request=request, papers=(make_paper(),), retrieved_count=3).duplicates_removed == 2
    assert SearchResult(request=request, reports=(failed,)).all_failed is True
    assert SearchResult(request=request).all_failed is False  # no sources asked: nothing failed
    with pytest.raises(ValueError, match="retrieved_count"):
        SearchResult(request=request, papers=(make_paper(),), retrieved_count=0)



def test_search_requests_sort_by_relevance_unless_told_otherwise():
    assert SearchRequest(query="x").sort == "relevance"
    assert SearchRequest(query="x", sort="citations").sort == "citations"
    with pytest.raises(ValueError, match="sort must be one of"):
        SearchRequest(query="x", sort="popularity")
