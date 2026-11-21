"""arXiv adapter: searches arXiv preprints and converts them to PaperRecords.

arXiv (export.arxiv.org/api) is where most new AI/ML papers appear first. It's free,
needs no key, and asks clients to wait about 3 seconds between requests, which the
rate limiter enforces. Notes on its behavior that this module handles:

- Query syntax: a plain multi-word query is treated as OR ("any word"), which returns
  mostly irrelevant results. Words are therefore joined with AND ("all words").
  Quoted phrases in the query are kept as phrases.
- The price of AND: a long query easily matches nothing (in the step 4.4 live run,
  14 of 19 queries of 5-8 words found nothing on arXiv). So when a query of more than
  FALLBACK_TERMS terms finds nothing, it is retried once with its first FALLBACK_TERMS
  terms (the planner puts the most important words first).
- Responses are Atom XML. They're parsed with Python's built-in parser, which is
  protected against malicious entity expansion in Python 3.11.
- Errors come back as HTTP 400 (handled by the HTTP layer) with an entry titled
  "Error". An error entry inside a normal response is also detected, so it can
  never be mistaken for a paper.
- Every arXiv record is tier 2 (preprint). When the authors added the DOI of the
  published version, it's kept, so dedup can merge the preprint with the published
  paper (which then provides tier 1 and the citation count; arXiv has none).
"""

from __future__ import annotations

from dataclasses import replace

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from src.retrieval.cache import ResponseCache
from src.retrieval.http import RetryPolicy, SourceHttpClient, SourceRequestError
from src.retrieval.records import ExternalIds, PaperRecord, SearchRequest, collapse_whitespace

logger = logging.getLogger(__name__)

SOURCE = "arxiv"
BASE_URL = "https://export.arxiv.org"
QUERY_PATH = "/api/query"
MIN_INTERVAL_SECONDS = 3.0  # arXiv's requested spacing between requests
# arXiv answers bursts with HTTP 429 and no Retry-After (step 4.4b live run); wait this long instead.
RATE_LIMITED_WAIT_SECONDS = 10.0
FIRST_ARXIV_YEAR = 1991

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}

# Words that add nothing when every word must match (and boolean words that would
# change the query's meaning if passed through).
STOPWORDS = frozenset({
    "a", "an", "and", "andnot", "are", "as", "at", "by", "for", "from", "in", "is",
    "not", "of", "on", "or", "the", "to", "using", "via", "with",
})

_PHRASE_OR_WORD = re.compile(r'"([^"]+)"|(\w+)')
_NON_WORD = re.compile(r"[^\w\s]")


FALLBACK_TERMS = 4


def shortened_request(request: SearchRequest) -> SearchRequest | None:
    """The request with only its first FALLBACK_TERMS meaningful terms (words or quoted
    phrases), or None if it isn't longer than that."""
    terms: list[str] = []
    for phrase, word in _PHRASE_OR_WORD.findall(request.query):
        if phrase:
            terms.append(f'"{collapse_whitespace(phrase).lower()}"')
        elif word.lower() not in STOPWORDS:
            terms.append(word.lower())
    terms = list(dict.fromkeys(terms))
    if len(terms) <= FALLBACK_TERMS:
        return None
    return replace(request, query=" ".join(terms[:FALLBACK_TERMS]))


def build_search_query(request: SearchRequest) -> str:
    """Turns a request into arXiv's query syntax.

    'retrieval-augmented generation for "low resource" languages', 2021–2026 becomes
    all:retrieval AND all:augmented AND all:generation AND all:"low resource"
    AND all:languages AND submittedDate:[202101010000 TO 202612312359]
    """
    phrases: list[str] = []
    words: list[str] = []
    for phrase, word in _PHRASE_OR_WORD.findall(request.query):
        if phrase:
            cleaned = collapse_whitespace(_NON_WORD.sub(" ", phrase)).lower()
            if cleaned:
                phrases.append(f'all:"{cleaned}"')
        else:
            words.append(word.lower())

    meaningful = [w for w in words if w not in STOPWORDS] or words  # only stopwords? keep them
    terms = list(dict.fromkeys([*(f"all:{w}" for w in meaningful), *phrases]))  # dedupe, keep order
    if not terms:
        raise SourceRequestError(SOURCE, "the query has no searchable words")

    if request.year_from is not None or request.year_to is not None:
        start = request.year_from if request.year_from is not None else FIRST_ARXIV_YEAR
        end = request.year_to if request.year_to is not None else datetime.now(timezone.utc).year + 1
        terms.append(f"submittedDate:[{start}01010000 TO {end}12312359]")

    return " AND ".join(terms)


def build_params(request: SearchRequest) -> dict[str, str | int]:
    """The query parameters for an arXiv search."""
    return {
        "search_query": build_search_query(request),
        "start": 0,
        "max_results": request.limit,
        # arXiv can sort by submission date but has no citation counts: citations -> relevance.
        "sortBy": "submittedDate" if request.sort == "newest" else "relevance",
        "sortOrder": "descending",
    }


def _text(element: ET.Element, path: str) -> str | None:
    value = element.findtext(path, namespaces=NS)
    return value.strip() if value and value.strip() else None


def parse_entry(entry: ET.Element) -> PaperRecord | None:
    """Converts one Atom entry to a PaperRecord, or returns None if it's unusable."""
    published = _text(entry, "atom:published")
    year = int(published[:4]) if published and published[:4].isdigit() else None
    pdf_url = next(
        (link.get("href") for link in entry.findall("atom:link", NS) if link.get("title") == "pdf"),
        None,
    )
    try:
        return PaperRecord(
            ids=ExternalIds(arxiv_id=_text(entry, "atom:id"), doi=_text(entry, "arxiv:doi")),
            title=_text(entry, "atom:title") or "",
            sources=(SOURCE,),
            tier=2,
            abstract=_text(entry, "atom:summary"),
            year=year,
            venue="arXiv",
            authors=tuple(name for a in entry.findall("atom:author", NS) if (name := _text(a, "atom:name"))),
            open_access_pdf_url=pdf_url,
        )
    except ValueError as error:
        logger.info("arxiv: skipped entry %s: %s", _text(entry, "atom:id"), error)
        return None


def parse_feed(xml_text: str) -> list[PaperRecord]:
    """Parses an arXiv Atom response into PaperRecords.

    Raises:
        SourceRequestError: the response isn't a valid Atom feed, or it's an error message.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        raise SourceRequestError(SOURCE, "response was not valid XML") from None
    if root.tag != f"{{{NS['atom']}}}feed":
        raise SourceRequestError(SOURCE, "response was not an Atom feed")

    papers: list[PaperRecord] = []
    for entry in root.findall("atom:entry", NS):
        entry_id = _text(entry, "atom:id") or ""
        if "/api/errors" in entry_id:
            raise SourceRequestError(SOURCE, f"arXiv API error: {_text(entry, 'atom:summary') or entry_id}")
        paper = parse_entry(entry)
        if paper is not None:
            papers.append(paper)
    return papers


class ArxivSource:
    """Searches arXiv. Create once and reuse; close with aclose() (or use `async with`)."""

    name = SOURCE

    def __init__(self, *, http: SourceHttpClient | None = None, cache: ResponseCache | None = None) -> None:
        self._http = http or SourceHttpClient(
            SOURCE, base_url=BASE_URL, min_interval=MIN_INTERVAL_SECONDS, timeout=30.0, cache=cache,
            retry=RetryPolicy(rate_limited_delay=RATE_LIMITED_WAIT_SECONDS),
        )

    async def __aenter__(self) -> ArxivSource:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def search(self, request: SearchRequest) -> list[PaperRecord]:
        """Returns the papers arXiv finds for the request (most relevant first).

        Raises:
            SourceRequestError: the request failed or the response was unusable.
        """
        # get_parsed caches the response only if parse_feed accepts it (no cached error pages).
        papers = await self._http.get_parsed(QUERY_PATH, build_params(request), parse_feed)
        if not papers and (shorter := shortened_request(request)) is not None:
            logger.info("arxiv: nothing for %r; retrying with %r", request.query, shorter.query)
            papers = await self._http.get_parsed(QUERY_PATH, build_params(shorter), parse_feed)
        logger.info("arxiv: %d papers", len(papers))
        return papers
