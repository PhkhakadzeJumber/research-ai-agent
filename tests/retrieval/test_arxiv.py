"""Tests for the arXiv adapter, using real recorded responses (tests/fixtures/arxiv/)."""

import asyncio
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import pytest

from src.retrieval.http import RetryPolicy, SourceHttpClient, SourceRequestError
from src.retrieval.records import SearchRequest
from src.retrieval.sources.arxiv import (
    ArxivSource,
    build_search_query,
    parse_entry,
    parse_feed,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "arxiv"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def search_with(response, request=SearchRequest(query="rag hallucination")):
    """Runs ArxivSource.search against a fake transport. Returns (papers, sent requests)."""
    sent = []

    def handler(http_request):
        sent.append(http_request)
        return response

    async def go():
        http = SourceHttpClient(
            "arxiv",
            base_url="https://export.arxiv.org",
            transport=httpx.MockTransport(handler),
            retry=RetryPolicy(max_attempts=1),
        )
        async with ArxivSource(http=http) as source:
            return await source.search(request)

    return asyncio.run(go()), sent


def by_arxiv_id(papers):
    return {paper.ids.arxiv_id: paper for paper in papers}


# --- query building ----------------------------------------------------------------

@pytest.mark.parametrize(
    "query, expected",
    [
        ("retrieval augmented generation", "all:retrieval AND all:augmented AND all:generation"),
        ("Retrieval-Augmented Generation", "all:retrieval AND all:augmented AND all:generation"),
        ("hallucination of small models", "all:hallucination AND all:small AND all:models"),
        ('rag for "low resource" languages', 'all:rag AND all:languages AND all:"low resource"'),
        ("rag rag RAG", "all:rag"),
        ("the of and", "all:the AND all:of AND all:and"),  # only stopwords: keep them rather than search nothing
    ],
)
def test_build_search_query(query, expected):
    assert build_search_query(SearchRequest(query=query)) == expected


@pytest.mark.parametrize(
    "year_from, year_to, expected_suffix",
    [
        (2021, 2026, "submittedDate:[202101010000 TO 202612312359]"),
        (None, 2020, "submittedDate:[199101010000 TO 202012312359]"),
    ],
)
def test_year_range_becomes_submitted_date(year_from, year_to, expected_suffix):
    query = build_search_query(SearchRequest(query="rag", year_from=year_from, year_to=year_to))
    assert query == f"all:rag AND {expected_suffix}"


def test_query_without_searchable_words_is_rejected():
    with pytest.raises(SourceRequestError, match="no searchable words"):
        build_search_query(SearchRequest(query="?!"))


def test_request_parameters_sent_to_arxiv():
    _, sent = search_with(httpx.Response(200, text=fixture("search.xml")), SearchRequest(query="rag", limit=7))
    params = sent[0].url.params
    assert sent[0].url.path == "/api/query"
    assert params["search_query"] == "all:rag"
    assert params["max_results"] == "7"
    assert params["sortBy"] == "relevance"


# --- parsing the recorded response ---------------------------------------------------

def test_recorded_search_returns_all_papers():
    papers, _ = search_with(httpx.Response(200, text=fixture("search.xml")))
    assert len(papers) == 5
    assert all(paper.tier == 2 and paper.sources == ("arxiv",) for paper in papers)


def test_entry_fields_are_parsed():
    paper = by_arxiv_id(parse_feed(fixture("search.xml")))["2309.15217"]  # version suffix removed

    assert paper.title == "Ragas: Automated Evaluation of Retrieval Augmented Generation"
    assert paper.year == 2023
    assert paper.authors == ("Shahul Es", "Jithin James", "Luis Espinosa-Anke", "Steven Schockaert")
    assert paper.abstract.startswith("We introduce Ragas")
    assert "\n" not in paper.abstract
    assert paper.open_access_pdf_url == "https://arxiv.org/pdf/2309.15217v2"
    assert paper.venue == "arXiv"
    assert paper.citation_count is None
    assert paper.ids.doi is None


def test_published_version_doi_is_kept_for_dedup():
    paper = by_arxiv_id(parse_feed(fixture("search.xml")))["2508.03553"]
    assert paper.ids.doi == "10.1109/icde65448.2025.00230"
    assert paper.tier == 2  # still a preprint record; dedup merges it with the published version


# --- errors and broken data --------------------------------------------------------------

def test_error_feed_is_a_source_error():
    with pytest.raises(SourceRequestError, match="max_results must be non-negative"):
        parse_feed(fixture("error.xml"))


def test_http_400_error_is_a_source_error():
    with pytest.raises(SourceRequestError, match="HTTP 400"):
        search_with(httpx.Response(400, text=fixture("error.xml")))


def test_invalid_xml_is_a_source_error():
    with pytest.raises(SourceRequestError, match="not valid XML"):
        parse_feed("<feed><entry>")


def test_non_atom_xml_is_a_source_error():
    with pytest.raises(SourceRequestError, match="not an Atom feed"):
        parse_feed("<html><body>maintenance</body></html>")


def test_empty_feed_returns_no_papers():
    empty = '<feed xmlns="http://www.w3.org/2005/Atom"><title>arXiv Query</title></feed>'
    assert parse_feed(empty) == []


def test_broken_entry_is_skipped():
    entry = ET.fromstring(
        '<entry xmlns="http://www.w3.org/2005/Atom"><id>not-an-arxiv-id</id><title>Something</title></entry>'
    )
    assert parse_entry(entry) is None
