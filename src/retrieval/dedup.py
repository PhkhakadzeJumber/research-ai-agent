"""Deduplication: one record per scholarly work, however many sources returned it.

Why it matters: a paper counted twice inflates the coverage report ("31 papers read"),
looks like two independent works to the novelty rules, and gets analysed (and paid
for) twice. The opposite error, merging two *different* papers, is worse: it hides
prior work. So the rules are conservative: when in doubt, don't merge.

Two records are the same work if:
  1. they share any normalized external ID (DOI, arXiv, OpenAlex, Semantic Scholar).
     Matches chain: A~B by arXiv ID and B~C by DOI puts A, B and C together. This is
     how a preprint joins its published version. Or:
  2. no ID links them, but the title is the same or nearly so (>= 95% similar after
     normalization), it is at least 4 words long, the years are at most 1 apart, and
     the first authors' surnames match (when both are listed).
Never merged by title (rule 2) when the records carry different arXiv IDs, or
different DOIs where neither is an arXiv DOI (e.g. a conference paper and its journal
extension). Exception: DOIs of versioned repositories (Zenodo, Figshare) give every
version of a deposit its own DOI, so, like arXiv DOIs, they don't block a title match
(step 4.5 live run: one Zenodo preprint appeared four times, once per version).

Merged records take: all IDs; tier 1 if any version is published; the EARLIEST year
(when the idea first became public, which is what novelty cares about); title and
venue from the published version; the longest abstract; the longest author list;
the highest citation count; an arXiv PDF link if any (reliably downloadable); and
the union of sources.
"""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass

from src.retrieval.records import ExternalIds, PaperRecord

logger = logging.getLogger(__name__)

TITLE_SIMILARITY = 0.95
MIN_TITLE_WORDS_FOR_TITLE_MATCH = 4
MAX_YEAR_GAP = 1
ARXIV_DOI_PREFIX = "10.48550/arxiv."
# Repositories that mint one DOI per version of the same deposit.
VERSIONED_DOI_PREFIXES = ("10.5281/zenodo.", "10.6084/m9.figshare.")
_BLOCK_WORDS = 3  # title-matching only compares records whose titles start with the same 3 words

REASON_DOI = "same DOI"
REASON_ARXIV = "same arXiv ID"
REASON_OPENALEX = "same OpenAlex ID"
REASON_S2 = "same Semantic Scholar ID"
REASON_TITLE = "same title, year and first author"

_ID_FIELDS = (("doi", REASON_DOI), ("arxiv_id", REASON_ARXIV), ("openalex_id", REASON_OPENALEX), ("s2_id", REASON_S2))
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str) -> str:
    """'Ragas:  Automated Évaluation!' -> 'ragas automated evaluation' (for comparison only)."""
    decomposed = unicodedata.normalize("NFKD", title)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _NON_ALPHANUMERIC.sub(" ", without_accents.lower()).strip()


def surname(name: str) -> str:
    """'Shahul Es' -> 'es'; 'Es, Shahul' -> 'es'."""
    family = name.split(",", 1)[0] if "," in name else (name.split() or [""])[-1]
    return normalize_title(family)


def is_arxiv_doi(doi: str | None) -> bool:
    return bool(doi) and doi.startswith(ARXIV_DOI_PREFIX)


def is_version_doi(doi: str | None) -> bool:
    """An arXiv DOI or a versioned-repository DOI: one of possibly several DOIs of the same work."""
    return is_arxiv_doi(doi) or (bool(doi) and doi.startswith(VERSIONED_DOI_PREFIXES))


@dataclass(frozen=True)
class Merge:
    """One group of records that were merged into a single paper."""

    paper: PaperRecord
    merged_count: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class DedupResult:
    papers: tuple[PaperRecord, ...]
    """One record per work, in the order each work first appeared in the input."""

    merges: tuple[Merge, ...]
    """Only the groups that actually merged something (for the coverage report and debugging)."""

    input_count: int

    @property
    def removed_count(self) -> int:
        return self.input_count - len(self.papers)


class _Groups:
    """Union-find over record indexes. The smallest index is always a group's root,
    so output order follows first appearance."""

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))
        self.reasons: dict[int, set[str]] = defaultdict(set)

    def find(self, i: int) -> int:
        while self._parent[i] != i:
            self._parent[i] = self._parent[self._parent[i]]
            i = self._parent[i]
        return i

    def union(self, a: int, b: int, reason: str) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            root, child = min(root_a, root_b), max(root_a, root_b)
            self._parent[child] = root
            self.reasons[root] |= self.reasons.pop(child, set())
        self.reasons[self.find(a)].add(reason)

    def members(self, size: int) -> dict[int, list[int]]:
        groups: dict[int, list[int]] = defaultdict(list)
        for i in range(size):
            groups[self.find(i)].append(i)
        return groups


def _titles_match(a: PaperRecord, b: PaperRecord) -> bool:
    """Rule 2: same (or nearly the same) distinctive title, close years, same first author."""
    title_a, title_b = normalize_title(a.title), normalize_title(b.title)
    if min(len(title_a.split()), len(title_b.split())) < MIN_TITLE_WORDS_FOR_TITLE_MATCH:
        return False
    if title_a != title_b and difflib.SequenceMatcher(None, title_a, title_b).ratio() < TITLE_SIMILARITY:
        return False
    if a.year is not None and b.year is not None and abs(a.year - b.year) > MAX_YEAR_GAP:
        return False
    if a.first_author and b.first_author and surname(a.first_author) != surname(b.first_author):
        return False
    return True


def _conflicting_ids(group_a: list[PaperRecord], group_b: list[PaperRecord]) -> bool:
    """True if merging the two groups would combine two different arXiv IDs or two different
    published DOIs (not arXiv or versioned-repository DOIs): signs of distinct works that only
    look alike."""
    records = group_a + group_b
    arxiv_ids = {r.ids.arxiv_id for r in records if r.ids.arxiv_id}
    published_dois = {r.ids.doi for r in records if r.ids.doi and not is_version_doi(r.ids.doi)}
    return len(arxiv_ids) > 1 or len(published_dois) > 1


def same_work_by_title(a: PaperRecord, b: PaperRecord) -> bool:
    """Rule 2 for two single records (used for stored papers): the titles, years and first
    authors match, and no IDs say they're different works."""
    return _titles_match(a, b) and not _conflicting_ids([a], [b])


def merge_records(records: list[PaperRecord]) -> PaperRecord:
    """Combines records of the same work (see the module docstring for the rules)."""
    if len(records) == 1:
        return records[0]
    published = [r for r in records if r.tier == 1]
    primary = published[0] if published else records[0]
    ordered = [primary, *(r for r in records if r is not primary)]  # primary's values win ties

    def first(values):
        return next((v for v in values if v), None)

    ids = ExternalIds(
        doi=first(r.ids.doi for r in published) or first(r.ids.doi for r in ordered),
        arxiv_id=first(r.ids.arxiv_id for r in ordered),
        openalex_id=first(r.ids.openalex_id for r in ordered),
        s2_id=first(r.ids.s2_id for r in ordered),
    )
    years = [r.year for r in records if r.year is not None]
    citations = [r.citation_count for r in records if r.citation_count is not None]
    pdf_urls = [r.open_access_pdf_url for r in ordered if r.open_access_pdf_url]

    return PaperRecord(
        ids=ids,
        title=primary.title,
        sources=tuple(dict.fromkeys(source for r in records for source in r.sources)),
        tier=min(r.tier for r in records),
        abstract=max((r.abstract for r in ordered if r.abstract), key=len, default=None),
        year=min(years) if years else None,
        venue=primary.venue or first(r.venue for r in ordered),
        authors=max((r.authors for r in ordered), key=len),
        citation_count=max(citations) if citations else None,
        open_access_pdf_url=next((u for u in pdf_urls if "arxiv.org/" in u), pdf_urls[0] if pdf_urls else None),
    )


def deduplicate(papers: list[PaperRecord] | tuple[PaperRecord, ...]) -> DedupResult:
    """Merges records of the same work. Output order follows each work's first appearance."""
    papers = list(papers)
    groups = _Groups(len(papers))

    # Rule 1: shared external IDs (certain). Chains form automatically through union-find.
    first_seen: dict[tuple[str, str], int] = {}
    for index, paper in enumerate(papers):
        for field_name, reason in _ID_FIELDS:
            value = getattr(paper.ids, field_name)
            if value is None:
                continue
            key = (field_name, value)
            if key in first_seen:
                groups.union(first_seen[key], index, reason)
            else:
                first_seen[key] = index

    # Rule 2: matching titles, only within blocks of titles that start with the same words.
    blocks: dict[str, list[int]] = defaultdict(list)
    for index, paper in enumerate(papers):
        blocks[" ".join(normalize_title(paper.title).split()[:_BLOCK_WORDS])].append(index)
    for indexes in blocks.values():
        for position, i in enumerate(indexes):
            for j in indexes[position + 1:]:
                root_i, root_j = groups.find(i), groups.find(j)
                if root_i == root_j or not _titles_match(papers[i], papers[j]):
                    continue
                members = groups.members(len(papers))
                if _conflicting_ids([papers[k] for k in members[root_i]], [papers[k] for k in members[root_j]]):
                    continue
                groups.union(i, j, REASON_TITLE)

    merged_papers: list[PaperRecord] = []
    merges: list[Merge] = []
    for root, member_indexes in sorted(groups.members(len(papers)).items()):
        paper = merge_records([papers[i] for i in member_indexes])
        merged_papers.append(paper)
        if len(member_indexes) > 1:
            merges.append(Merge(paper=paper, merged_count=len(member_indexes),
                                reasons=tuple(sorted(groups.reasons[root]))))

    logger.info("dedup: %d records -> %d papers (%d merges)", len(papers), len(merged_papers), len(merges))
    return DedupResult(papers=tuple(merged_papers), merges=tuple(merges), input_count=len(papers))
