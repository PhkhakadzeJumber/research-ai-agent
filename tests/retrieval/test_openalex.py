"""Tests for the OpenAlex adapter, using a real recorded response (tests/fixtures/openalex/)."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from src.retrieval.http import RetryPolicy, SourceHttpClient, SourceRequestError
from src.retrieval.records import SearchRequest
from src.retrieval.sources.openalex import (
    OpenAlexSource,
    build_params,
    parse_work,
    rebuild_abstract,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "openalex" / "search_works.json"


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def search_with(body, request=SearchRequest(query="rag hallucination"), **source_kwargs):
    """Runs OpenAlexSource.search against a fake transport returning `body`. Returns (papers, sent requests)."""
    sent = []

    def handler(http_request):
        sent.append(http_request)
        return httpx.Response(200, json=body)

    async def go():
        http = SourceHttpClient(
            "openalex",
            base_url="https://api.openalex.org",
            transport=httpx.MockTransport(handler),
            retry=RetryPolicy(max_attempts=1),
        )
        async with OpenAlexSource(http=http, **source_kwargs) as source:
            return await source.search(request)

    return asyncio.run(go()), sent


def by_openalex_id(papers):
    return {paper.ids.openalex_id: paper for paper in papers}


# --- parsing the recorded response ---------------------------------------------

def test_recorded_search_skips_non_papers():
    papers, _ = search_with(load_fixture())
    ids = by_openalex_id(papers)
    assert len(papers) == 4
    assert "W7214210488" not in ids  # the "Corrigendum to ..." erratum


def test_arxiv_preprint_is_parsed_with_its_arxiv_id():
    paper = by_openalex_id(search_with(load_fixture())[0])["W4389984066"]

    assert paper.title == "Retrieval-Augmented Generation for Large Language Models: A Survey"
    assert paper.ids.doi == "10.48550/arxiv.2312.10997"
    assert paper.ids.arxiv_id == "2312.10997"  # extracted from the DOI
    assert paper.tier == 2
    assert paper.year == 2023
    assert paper.venue == "arXiv (Cornell University)"
    assert paper.first_author == "Yunfan Gao"
    assert paper.open_access_pdf_url == "https://arxiv.org/pdf/2312.10997"
    assert paper.citation_count > 0
    assert paper.abstract.startswith("Large Language Models (LLMs)")
    assert paper.sources == ("openalex",)


def test_conference_paper_is_tier_one():
    paper = by_openalex_id(search_with(load_fixture())[0])["W4402671555"]
    assert paper.title.startswith("RAGTruth")
    assert paper.tier == 1
    assert paper.ids.doi is not None
    assert paper.ids.arxiv_id is None


def test_every_kept_paper_is_valid():
    for paper in search_with(load_fixture())[0]:
        assert paper.title and paper.ids.openalex_id and paper.year


# --- request parameters ----------------------------------------------------------

def test_request_sends_search_and_selected_fields():
    _, sent = search_with(load_fixture(), request=SearchRequest(query="rag", limit=7))
    params = sent[0].url.params
    assert sent[0].url.path == "/works"
    assert params["search"] == "rag"
    assert params["per-page"] == "7"
    assert "abstract_inverted_index" in params["select"]
    assert "api_key" not in params and "mailto" not in params


def test_request_sends_api_key_and_mailto_when_configured():
    _, sent = search_with(load_fixture(), api_key="test-key", mailto="me@example.org")
    params = sent[0].url.params
    assert params["api_key"] == "test-key"
    assert params["mailto"] == "me@example.org"


@pytest.mark.parametrize(
    "year_from, year_to, expected",
    [
        (2021, 2026, "publication_year:2021-2026"),
        (2021, None, "publication_year:>2020"),
        (None, 2023, "publication_year:<2024"),
        (None, None, None),
    ],
)
def test_year_filter(year_from, year_to, expected):
    params = build_params(SearchRequest(query="x", year_from=year_from, year_to=year_to))
    assert params.get("filter") == expected


# --- edge cases --------------------------------------------------------------------

def test_unexpected_response_shape_is_a_source_error():
    with pytest.raises(SourceRequestError, match="unexpected response shape"):
        search_with({"error": "something"})


def test_work_without_title_or_ids_is_skipped():
    assert parse_work({"id": "https://openalex.org/W1", "display_name": "", "type": "article"}) is None
    assert parse_work({"display_name": "No IDs at all", "type": "article"}) is None


def test_journal_paper_with_arxiv_copy_stays_tier_one():
    work = {
        "id": "https://openalex.org/W1",
        "display_name": "A journal paper",
        "type": "article",
        "primary_location": {"source": {"display_name": "Some Journal", "type": "journal"}},
        "best_oa_location": {"pdf_url": "https://arxiv.org/pdf/2101.00001v2"},
    }
    paper = parse_work(work)
    assert paper.tier == 1
    assert paper.ids.arxiv_id == "2101.00001"  # still found, so dedup can merge it with the preprint


def test_rebuild_abstract():
    assert rebuild_abstract({"world": [1], "Hello": [0], "again": [3], "hello": [2]}) == "Hello world hello again"
    assert rebuild_abstract({}) is None
    assert rebuild_abstract(None) is None


@pytest.mark.parametrize(("sort", "expected"), [("citations", "cited_by_count:desc"),
                                                ("newest", "publication_date:desc"), ("relevance", None)])
def test_sort_order_sent_to_openalex(sort, expected):
    assert build_params(SearchRequest(query="x", sort=sort)).get("sort") == expected



def test_min_citations_filter_is_combined_with_years():
    params = build_params(SearchRequest(query="x", year_from=2022, min_citations=50))
    assert params["filter"] == "publication_year:>2021,cited_by_count:>49"
    assert "filter" not in build_params(SearchRequest(query="x"))
