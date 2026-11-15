"""OpenAlex adapter: searches OpenAlex works and converts them to PaperRecords.

OpenAlex (api.openalex.org) indexes most scholarly literature: journals, conferences
and preprint servers like arXiv. Notes on its format that this module handles:

- Abstracts come as an "inverted index" (word -> list of positions) and are rebuilt here.
- arXiv papers have no arXiv ID field. The ID is hidden in their DOI
  (10.48550/arxiv.2312.10997) or URLs, and is extracted so dedup can merge
  them with arXiv results.
- Some results aren't research papers (errata, retractions, editorials) and are skipped.
- Tier: preprints and repository-hosted works are tier 2; journal/conference works are tier 1.

Access: every request costs a little from a daily budget. With OPENALEX_API_KEY the
budget is personal; without it, the budget is shared per IP address.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from src.config import get_retrieval_settings
from src.retrieval.cache import ResponseCache
from src.retrieval.http import SourceHttpClient, SourceRequestError
from src.retrieval.records import ExternalIds, PaperRecord, SearchRequest, normalize_arxiv_id

logger = logging.getLogger(__name__)

SOURCE = "openalex"
BASE_URL = "https://api.openalex.org"
MAX_PER_PAGE = 200

# Only request the fields we use: smaller responses, faster searches.
SELECT_FIELDS = ",".join([
    "id", "doi", "display_name", "publication_year", "type", "primary_location",
    "best_oa_location", "authorships", "cited_by_count", "abstract_inverted_index", "ids",
])

# Work types that are not research papers.
EXCLUDED_TYPES = frozenset({
    "erratum", "retraction", "paratext", "editorial", "peer-review",
    "supplementary-materials", "grant",
})

ARXIV_DOI_PREFIX = "10.48550/arxiv."
MAX_ABSTRACT_WORDS = 20_000  # guard against malformed indexes


def rebuild_abstract(inverted_index: Mapping[str, list[int]] | None) -> str | None:
    """Turns OpenAlex's {word: [positions]} index back into plain text."""
    if not inverted_index:
        return None
    words: dict[int, str] = {}
    for word, positions in inverted_index.items():
        for position in positions or ():
            if isinstance(position, int) and 0 <= position < MAX_ABSTRACT_WORDS:
                words[position] = word
    if not words:
        return None
    return " ".join(words[i] for i in sorted(words))


def _arxiv_id_from(doi: str | None, urls: list[str | None]) -> str | None:
    """Finds a work's arXiv ID in its DOI (10.48550/arxiv.XXXX) or in arxiv.org URLs."""
    if doi:
        lowered = doi.lower().removeprefix("https://doi.org/")
        if lowered.startswith(ARXIV_DOI_PREFIX):
            found = normalize_arxiv_id(lowered[len(ARXIV_DOI_PREFIX):])
            if found:
                return found
    for url in urls:
        if url and "arxiv.org/" in url:
            found = normalize_arxiv_id(url)
            if found:
                return found
    return None


def parse_work(work: Mapping[str, Any]) -> PaperRecord | None:
    """Converts one OpenAlex work to a PaperRecord, or returns None if it should be skipped."""
    work_type = work.get("type")
    if work_type in EXCLUDED_TYPES:
        return None

    primary = work.get("primary_location") or {}
    best_oa = work.get("best_oa_location") or {}
    source = primary.get("source") or {}
    extra_ids = work.get("ids") or {}

    doi = work.get("doi") or extra_ids.get("doi")
    arxiv_id = _arxiv_id_from(doi, [
        primary.get("landing_page_url"), primary.get("pdf_url"),
        best_oa.get("landing_page_url"), best_oa.get("pdf_url"),
    ])
    # The tier depends on the work itself, not on where a free copy lives: a journal
    # paper whose open-access copy is on arXiv is still peer-reviewed (tier 1).
    is_preprint = work_type == "preprint" or source.get("type") == "repository"

    authors = tuple(
        (authorship.get("author") or {}).get("display_name") or ""
        for authorship in work.get("authorships") or ()
    )
    pdf_url = best_oa.get("pdf_url") or (primary.get("pdf_url") if primary.get("is_oa") else None)

    try:
        return PaperRecord(
            ids=ExternalIds(doi=doi, arxiv_id=arxiv_id, openalex_id=work.get("id") or extra_ids.get("openalex")),
            title=work.get("display_name") or work.get("title") or "",
            sources=(SOURCE,),
            tier=2 if is_preprint else 1,
            abstract=rebuild_abstract(work.get("abstract_inverted_index")),
            year=work.get("publication_year"),
            venue=source.get("display_name"),
            authors=authors,
            citation_count=work.get("cited_by_count"),
            open_access_pdf_url=pdf_url,
        )
    except ValueError as error:
        # A single broken record shouldn't fail the whole search.
        logger.info("openalex: skipped work %s: %s", work.get("id"), error)
        return None


def build_params(
    request: SearchRequest, *, api_key: str | None = None, mailto: str | None = None
) -> dict[str, Any]:
    """The query parameters for a works search."""
    params: dict[str, Any] = {
        "search": request.query,
        "per-page": min(request.limit, MAX_PER_PAGE),
        "select": SELECT_FIELDS,
    }
    if request.year_from is not None and request.year_to is not None:
        params["filter"] = f"publication_year:{request.year_from}-{request.year_to}"
    elif request.year_from is not None:
        params["filter"] = f"publication_year:>{request.year_from - 1}"
    elif request.year_to is not None:
        params["filter"] = f"publication_year:<{request.year_to + 1}"
    if api_key:
        params["api_key"] = api_key
    if mailto:
        params["mailto"] = mailto
    return params


class OpenAlexSource:
    """Searches OpenAlex. Create once and reuse; close with aclose() (or use `async with`)."""

    name = SOURCE

    def __init__(
        self,
        *,
        api_key: str | None = None,
        mailto: str | None = None,
        http: SourceHttpClient | None = None,
        cache: ResponseCache | None = None,
    ) -> None:
        self._api_key = api_key
        self._mailto = mailto
        # ~10 requests per second at most; OpenAlex's limits are generous, the budget is what counts.
        self._http = http or SourceHttpClient(SOURCE, base_url=BASE_URL, min_interval=0.1, cache=cache)

    @classmethod
    def from_settings(cls, *, cache: ResponseCache | None = None) -> OpenAlexSource:
        """Creates the source with the key/email from the environment (.env)."""
        settings = get_retrieval_settings()
        return cls(api_key=settings.openalex_api_key, mailto=settings.openalex_mailto, cache=cache)

    async def __aenter__(self) -> OpenAlexSource:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def search(self, request: SearchRequest) -> list[PaperRecord]:
        """Returns the papers OpenAlex finds for the request (best matches first).

        Raises:
            SourceRequestError: the request failed or the response had an unexpected shape.
        """
        data = await self._http.get_json(
            "/works", build_params(request, api_key=self._api_key, mailto=self._mailto)
        )
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            raise SourceRequestError(SOURCE, "unexpected response shape (no 'results' list)")

        papers = [paper for paper in (parse_work(work) for work in results) if paper is not None]
        logger.info("openalex: %d results, %d papers kept", len(results), len(papers))
        return papers
