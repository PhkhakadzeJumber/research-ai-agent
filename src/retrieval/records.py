"""The shapes that flow through retrieval: a paper record, a search request, a search result.

Every source adapter converts its API's response into PaperRecord objects, so dedup,
ranking, storage and agents work with one format. Records validate themselves when
created: a broken record fails loudly at the adapter instead of somewhere far away.

External IDs are normalized here (e.g. "https://doi.org/10.1/ABC" -> "10.1/abc"),
because dedup compares IDs across sources, and the same DOI arrives in different
spellings from different APIs.

These are retrieval-side records, not database rows: the database Paper table
(doc 10 §5.1) adds Eureka's internal ID, embeddings and full-text status in step 2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

Tier = Literal[1, 2]
"""1 = peer-reviewed publication, 2 = preprint (doc 07 §1.3)."""

_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi:")
_ARXIV_NEW = re.compile(r"^\d{4}\.\d{4,5}$")                     # e.g. 2405.01234
_ARXIV_OLD = re.compile(r"^[a-z][a-z\-]*(\.[a-z]{2})?/\d{7}$")   # e.g. hep-th/9901001, math.ag/0601001
_ARXIV_VERSION = re.compile(r"v\d+$")
_OPENALEX_WORK = re.compile(r"^W\d+$")
_S2_PAPER = re.compile(r"^[0-9a-f]{40}$")
_WHITESPACE = re.compile(r"\s+")

MAX_QUERY_LENGTH = 500
MAX_LIMIT = 200


# ---------------------------------------------------------------------------
# ID normalization: each function returns the canonical form, or None if the
# value isn't a valid ID. Invalid IDs from an API are dropped, not fatal.
# ---------------------------------------------------------------------------

def normalize_doi(raw: str | None) -> str | None:
    """'https://doi.org/10.18653/V1/2024.ACL-LONG.1' -> '10.18653/v1/2024.acl-long.1'."""
    if not raw:
        return None
    value = raw.strip()
    lowered = value.lower()
    for prefix in _DOI_PREFIXES:
        if lowered.startswith(prefix):
            value = value[len(prefix):]
            break
    value = value.strip().lower()
    # DOIs are case-insensitive; a valid one starts with "10." and has a suffix after "/"
    if not value.startswith("10.") or "/" not in value or value.endswith("/"):
        return None
    return value


def normalize_arxiv_id(raw: str | None) -> str | None:
    """'arXiv:2405.01234v2' or 'https://arxiv.org/abs/2405.01234' -> '2405.01234'.

    The version suffix is removed: v1 and v2 of a preprint are the same work.
    """
    if not raw:
        return None
    value = raw.strip()
    for prefix in ("https://arxiv.org/abs/", "http://arxiv.org/abs/",
                   "https://arxiv.org/pdf/", "http://arxiv.org/pdf/"):
        if value.lower().startswith(prefix):
            value = value[len(prefix):]
            break
    if value.lower().startswith("arxiv:"):
        value = value[len("arxiv:"):]
    if value.lower().endswith(".pdf"):
        value = value[: -len(".pdf")]
    value = _ARXIV_VERSION.sub("", value.strip()).lower()
    if _ARXIV_NEW.match(value) or _ARXIV_OLD.match(value):
        return value
    return None


def normalize_openalex_id(raw: str | None) -> str | None:
    """'https://openalex.org/W2741809807' -> 'W2741809807'."""
    if not raw:
        return None
    value = raw.strip().rstrip("/").rsplit("/", 1)[-1].upper()
    return value if _OPENALEX_WORK.match(value) else None


def normalize_s2_id(raw: str | None) -> str | None:
    """Semantic Scholar paper IDs are 40-character hex strings."""
    if not raw:
        return None
    value = raw.strip().lower()
    return value if _S2_PAPER.match(value) else None


def collapse_whitespace(text: str) -> str:
    """Turns line breaks and repeated spaces into single spaces (arXiv titles contain newlines)."""
    return _WHITESPACE.sub(" ", text).strip()


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExternalIds:
    """A paper's identifiers in external systems. Values are normalized on creation."""

    doi: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    s2_id: str | None = None

    def __post_init__(self) -> None:
        # frozen dataclass: object.__setattr__ is the supported way to set fields in __post_init__
        object.__setattr__(self, "doi", normalize_doi(self.doi))
        object.__setattr__(self, "arxiv_id", normalize_arxiv_id(self.arxiv_id))
        object.__setattr__(self, "openalex_id", normalize_openalex_id(self.openalex_id))
        object.__setattr__(self, "s2_id", normalize_s2_id(self.s2_id))

    @property
    def is_empty(self) -> bool:
        return not (self.doi or self.arxiv_id or self.openalex_id or self.s2_id)


@dataclass(frozen=True)
class PaperRecord:
    """One paper as returned by a source (or merged from several sources by dedup)."""

    ids: ExternalIds
    title: str
    sources: tuple[str, ...]
    """Which adapters returned this paper, e.g. ("openalex",) or, after dedup, ("openalex", "arxiv")."""

    tier: Tier
    abstract: str | None = None
    year: int | None = None
    venue: str | None = None
    authors: tuple[str, ...] = ()
    citation_count: int | None = None
    open_access_pdf_url: str | None = None

    def __post_init__(self) -> None:
        if self.ids.is_empty:
            raise ValueError("A paper record needs at least one valid external ID.")

        title = collapse_whitespace(self.title or "")
        if not title:
            raise ValueError("A paper record needs a title.")
        object.__setattr__(self, "title", title)

        abstract = collapse_whitespace(self.abstract) if self.abstract else ""
        object.__setattr__(self, "abstract", abstract or None)

        venue = collapse_whitespace(self.venue) if self.venue else ""
        object.__setattr__(self, "venue", venue or None)

        authors = tuple(a for a in (collapse_whitespace(name) for name in self.authors) if a)
        object.__setattr__(self, "authors", authors)

        if not self.sources:
            raise ValueError("A paper record must name at least one source.")
        if self.tier not in (1, 2):
            raise ValueError(f"tier must be 1 (peer-reviewed) or 2 (preprint), got {self.tier!r}.")

        latest_year = datetime.now(timezone.utc).year + 1  # +1: papers can be dated next year
        if self.year is not None and not 1800 <= self.year <= latest_year:
            raise ValueError(f"Implausible publication year: {self.year}.")
        if self.citation_count is not None and self.citation_count < 0:
            raise ValueError("citation_count can't be negative.")
        if self.open_access_pdf_url is not None and not self.open_access_pdf_url.startswith(("http://", "https://")):
            raise ValueError("open_access_pdf_url must be an http(s) URL.")

    @property
    def first_author(self) -> str | None:
        return self.authors[0] if self.authors else None


@dataclass(frozen=True)
class SearchRequest:
    """What to search for. One request is sent to every source."""

    query: str
    year_from: int | None = None
    year_to: int | None = None
    limit: int = 25
    """Maximum results per source."""

    def __post_init__(self) -> None:
        query = collapse_whitespace(self.query or "")
        if not query:
            raise ValueError("The search query is empty.")
        if len(query) > MAX_QUERY_LENGTH:
            raise ValueError(f"The search query is longer than {MAX_QUERY_LENGTH} characters.")
        object.__setattr__(self, "query", query)

        if not 1 <= self.limit <= MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_LIMIT}.")
        if self.year_from is not None and self.year_to is not None and self.year_from > self.year_to:
            raise ValueError("year_from can't be after year_to.")


@dataclass(frozen=True)
class SourceReport:
    """How one source did for one request. Failed sources are reported, never hidden (doc 07 §5)."""

    source: str
    ok: bool
    result_count: int = 0
    latency_ms: int = 0
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.ok and not self.error:
            raise ValueError("A failed source report must say what went wrong.")
        if self.result_count < 0 or self.latency_ms < 0:
            raise ValueError("Counts and latency can't be negative.")


@dataclass(frozen=True)
class SearchResult:
    """The deduplicated papers for one request, plus a report per source."""

    request: SearchRequest
    papers: tuple[PaperRecord, ...] = ()
    reports: tuple[SourceReport, ...] = field(default_factory=tuple)

    @property
    def failed_sources(self) -> tuple[str, ...]:
        return tuple(r.source for r in self.reports if not r.ok)

    @property
    def ok_sources(self) -> tuple[str, ...]:
        return tuple(r.source for r in self.reports if r.ok)
