"""Tests for src/retrieval/search.py with fake sources: no network."""

import asyncio

import pytest

from src.config import RetrievalSettings
from src.retrieval.http import SourceRequestError
from src.retrieval.records import ExternalIds, PaperRecord, SearchRequest
from src.retrieval.search import LiteratureSearch, interleave, search_sources

REQUEST = SearchRequest(query="rag hallucination")


def paper(name, *, arxiv=None, source="fake"):
    """A distinct paper whose title, first author and OpenAlex-style ID derive from `name`.

    The distinct first author matters: titles like "Paper a2 ..." and "Paper a3 ..." are
    near-identical, and without different authors dedup would (correctly) merge them.
    """
    unique_digits = "".join(f"{ord(c):03d}" for c in name)  # "a2" -> "097050": unique per name
    ids = ExternalIds(arxiv_id=arxiv) if arxiv else ExternalIds(openalex_id=f"W{unique_digits}")
    return PaperRecord(
        ids=ids, title=f"Paper {name} about a distinct research topic", sources=(source,), tier=1,
        authors=(f"Author {name}",),
    )


class FakeSource:
    def __init__(self, name, papers=(), *, error=None, delay=0.0, wait_for=None, started=None):
        self.name = name
        self._papers = list(papers)
        self._error = error
        self._delay = delay
        self._wait_for = wait_for  # an Event that must be set by another source (proves parallelism)
        self._started = started    # an Event this source sets when it starts
        self.closed = False

    async def search(self, request):
        if self._started:
            self._started.set()
        if self._wait_for:
            await asyncio.wait_for(self._wait_for.wait(), timeout=2)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error:
            raise self._error
        return list(self._papers)

    async def aclose(self):
        self.closed = True


def run(sources, request=REQUEST, timeout=5.0):
    return asyncio.run(search_sources(sources, request, timeout=timeout))


def titles(result):
    return [p.title.split()[1] for p in result.papers]


# --- normal operation --------------------------------------------------------------------

def test_all_sources_succeed():
    result = run([FakeSource("a", [paper("a1"), paper("a2")]), FakeSource("b", [paper("b1")])])

    assert len(result.papers) == 3
    assert [r.source for r in result.reports] == ["a", "b"]
    assert all(r.ok for r in result.reports)
    assert [r.result_count for r in result.reports] == [2, 1]
    assert result.retrieved_count == 3


def test_results_are_interleaved_by_rank():
    result = run([FakeSource("a", [paper("a1"), paper("a2"), paper("a3")]), FakeSource("b", [paper("b1")])])
    assert titles(result) == ["a1", "b1", "a2", "a3"]


def test_interleave_helper():
    a1, a2, b1 = paper("a1"), paper("a2"), paper("b1")
    assert interleave([[a1, a2], [b1], []]) == [a1, b1, a2]
    assert interleave([]) == []


def test_duplicates_across_sources_are_merged():
    same_in_a = paper("x", arxiv="2405.01234", source="a")
    same_in_b = paper("x", arxiv="2405.01234", source="b")
    result = run([FakeSource("a", [same_in_a]), FakeSource("b", [same_in_b, paper("b2")])])

    assert len(result.papers) == 2
    assert result.papers[0].sources == ("a", "b")
    assert (result.retrieved_count, result.duplicates_removed) == (3, 1)


def test_sources_run_in_parallel():
    # Each source waits until the other has started. Run one after the other, this would time out.
    a_started, b_started = asyncio.Event(), asyncio.Event()

    async def go():
        a = FakeSource("a", [paper("a1")], started=a_started, wait_for=b_started)
        b = FakeSource("b", [paper("b1")], started=b_started, wait_for=a_started)
        return await search_sources([a, b], REQUEST, timeout=5)

    result = asyncio.run(go())
    assert all(r.ok for r in result.reports)


# --- failures never break the search --------------------------------------------------------

def test_one_failing_source_still_returns_the_others_papers():
    failing = FakeSource("a", error=SourceRequestError("a", "HTTP 429 (gave up after 4 attempts)", status=429))
    result = run([failing, FakeSource("b", [paper("b1")])])

    assert titles(result) == ["b1"]
    assert result.failed_sources == ("a",)
    assert result.reports[0].error == "HTTP 429 (gave up after 4 attempts)"
    assert result.all_failed is False


def test_unexpected_crash_is_reported_not_raised():
    result = run([FakeSource("a", error=KeyError("bug")), FakeSource("b", [paper("b1")])])
    assert result.reports[0].error == "internal error (KeyError)"
    assert titles(result) == ["b1"]


def test_slow_source_hits_the_time_limit():
    result = run([FakeSource("slow", [paper("s1")], delay=2), FakeSource("b", [paper("b1")])], timeout=0.1)
    assert result.reports[0].ok is False
    assert "no answer within 0.1 seconds" in result.reports[0].error
    assert titles(result) == ["b1"]


def test_all_sources_failing_is_reported():
    result = run([
        FakeSource("a", error=SourceRequestError("a", "HTTP 503")),
        FakeSource("b", error=SourceRequestError("b", "request timed out")),
    ])
    assert result.all_failed is True
    assert result.papers == ()
    assert result.retrieved_count == 0


# --- LiteratureSearch ----------------------------------------------------------------------------

def test_literature_search_searches_and_closes_all_sources():
    a, b = FakeSource("a", [paper("a1")]), FakeSource("b", [paper("b1")])

    async def go():
        async with LiteratureSearch([a, b]) as search:
            return await search.search(REQUEST)

    result = asyncio.run(go())
    assert len(result.papers) == 2
    assert a.closed and b.closed


def test_closing_continues_when_one_source_fails_to_close():
    class BadClose(FakeSource):
        async def aclose(self):
            raise RuntimeError("close failed")

    good = FakeSource("good")
    asyncio.run(LiteratureSearch([BadClose("bad"), good]).aclose())
    assert good.closed


def test_literature_search_validates_its_sources():
    with pytest.raises(ValueError, match="at least one source"):
        LiteratureSearch([])
    with pytest.raises(ValueError, match="unique"):
        LiteratureSearch([FakeSource("a"), FakeSource("a")])


def test_default_sources_are_openalex_and_arxiv(monkeypatch):
    # Don't read the real .env in tests: give OpenAlex empty settings.
    monkeypatch.setattr("src.retrieval.sources.openalex.get_retrieval_settings", lambda: RetrievalSettings())

    async def go():
        search = LiteratureSearch.from_settings()
        names = search.source_names
        await search.aclose()
        return names

    assert asyncio.run(go()) == ("openalex", "arxiv")
