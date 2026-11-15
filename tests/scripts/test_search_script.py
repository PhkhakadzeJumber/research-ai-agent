"""Tests for scripts/search.py: options, cache choice and output (no network, no database).

Saving to the database is tested in tests/db/test_search_script_save.py.
"""

import asyncio

import pytest

from scripts import search as search_script
from scripts.search import choose_cache, describe_cache, format_result, main, parse_args
from src.db.repositories.cache import DbResponseCache
from src.retrieval.cache import FileResponseCache
from src.retrieval.records import ExternalIds, PaperRecord, SearchRequest, SearchResult, SourceReport


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


def test_format_result_shows_papers_reports_and_counts():
    output = format_result(make_result(), "Cache: 0 hit(s), 0 miss(es)  [database (api_cache)]")

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
    assert "Cache: 0 hit(s), 0 miss(es)  [database (api_cache)]" in output


def test_format_result_without_papers_or_cache():
    result = SearchResult(request=SearchRequest(query="x"), reports=(SourceReport(source="arxiv", ok=True),))
    output = format_result(result)
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


def test_save_and_cache_options():
    args = parse_args(["rag", "--save", "--mode", "evaluate", "--complexity", "advanced", "--file-cache"])
    assert (args.save, args.mode, args.complexity, args.file_cache) == (True, "evaluate", "advanced", True)
    defaults = parse_args(["rag"])
    assert (defaults.save, defaults.mode, defaults.complexity) == (False, "discover", "beginner")


def test_no_cache_and_file_cache_cannot_be_combined():
    with pytest.raises(SystemExit):
        parse_args(["rag", "--no-cache", "--file-cache"])


class FakeDatabase:
    async def session(self):  # only its identity matters here
        raise AssertionError("not used")


def choose(argv, database, database_ok):
    return asyncio.run(choose_cache(parse_args(argv), database, database_ok))


def test_database_cache_is_the_default_when_reachable():
    choice = choose(["rag"], FakeDatabase(), True)
    assert isinstance(choice.cache, DbResponseCache)
    assert choice.warning is None
    assert describe_cache(choice) == "Cache: 0 hit(s), 0 miss(es)  [database (api_cache)]"


def test_file_cache_with_a_warning_when_the_database_is_down(tmp_path):
    choice = choose(["rag", "--cache-dir", str(tmp_path)], FakeDatabase(), False)
    assert isinstance(choice.cache, FileResponseCache)
    assert "database not reachable" in choice.warning


def test_explicit_file_cache_and_no_cache(tmp_path):
    assert isinstance(choose(["rag", "--file-cache", "--cache-dir", str(tmp_path)], FakeDatabase(), True).cache,
                      FileResponseCache)
    off = choose(["rag", "--no-cache"], FakeDatabase(), True)
    assert off.cache is None and describe_cache(off) == "Cache: off"


def test_save_without_a_database_exits_with_code_2_before_searching(monkeypatch, capsys):
    monkeypatch.setattr(search_script, "open_database", lambda: None)
    monkeypatch.setattr(search_script.LiteratureSearch, "from_settings",
                        lambda **kwargs: pytest.fail("must not search when saving is impossible"))
    assert main(["rag", "--save", "--sources", "arxiv"]) == 2
    assert "--save needs the database" in capsys.readouterr().err
