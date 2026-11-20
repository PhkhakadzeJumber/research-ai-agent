"""Saving papers into the global library, deduplicated against what's already stored.

upsert_paper(session, record) finds existing rows by ANY external ID:
  - no match: looks for a stored paper that is the same work by title (the same rule 2
    as src/retrieval/dedup.py: same title, year +-1, same first author, no conflicting
    IDs). Each query's results are deduplicated separately, so without this, version A
    found by one query and version B by another became two papers (step 4.5b). If one is
    found, the record is merged into it (nothing is deleted, so its analyses are kept);
    otherwise a new paper is inserted;
  - one match: merges the new information into it (same rules as src/retrieval/dedup.py:
    tier 1 wins, earliest year, longest abstract, all IDs kept, ...). This is how a stored
    arXiv preprint becomes "published" when OpenAlex later returns the journal version;
  - several matches: they're one work (e.g. one row known only by DOI, another only by
    arXiv ID, and the new record carries both). They're merged into the oldest row and
    their retrieval hits move over. If a row being merged away already has chunks,
    analyses or quotes, PaperMergeConflict is raised instead of risking data loss
    (nothing creates those before step 3, which must handle this case).

Concurrency: matching rows are locked while merging, and if another transaction
inserts the same new paper at the same moment, the unique keys reject one insert
and the upsert retries as a merge.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Chunk, Paper, PaperAnalysis, Quote, RetrievalHit
from src.retrieval.dedup import (
    MAX_YEAR_GAP,
    MIN_TITLE_WORDS_FOR_TITLE_MATCH,
    merge_records,
    normalize_title,
    same_work_by_title,
)
from src.retrieval.records import ExternalIds, PaperRecord

logger = logging.getLogger(__name__)

STORED = "database"  # PaperRecord.sources value for a record rebuilt from a stored row


class PaperMergeConflict(RuntimeError):
    """Two stored papers turned out to be one work, but merging would touch analyses/chunks/quotes."""


def paper_to_record(paper: Paper) -> PaperRecord:
    """The stored row as a PaperRecord, so the dedup merge rules can be reused."""
    return PaperRecord(
        ids=ExternalIds(doi=paper.doi, arxiv_id=paper.arxiv_id, openalex_id=paper.openalex_id, s2_id=paper.s2_id),
        title=paper.title,
        sources=(STORED,),
        tier=paper.tier,
        abstract=paper.abstract,
        year=paper.year,
        venue=paper.venue,
        authors=tuple(paper.authors or ()),
        citation_count=paper.citation_count,
        open_access_pdf_url=paper.open_access_pdf_url,
    )


def _apply(paper: Paper, record: PaperRecord) -> None:
    paper.doi = record.ids.doi
    paper.arxiv_id = record.ids.arxiv_id
    paper.openalex_id = record.ids.openalex_id
    paper.s2_id = record.ids.s2_id
    paper.title = record.title
    paper.abstract = record.abstract
    paper.year = record.year
    paper.venue = record.venue
    paper.authors = list(record.authors)
    paper.citation_count = record.citation_count
    paper.open_access_pdf_url = record.open_access_pdf_url
    paper.tier = record.tier


async def find_papers_by_ids(session: AsyncSession, ids: ExternalIds, *, lock: bool = False) -> list[Paper]:
    """Stored papers sharing any external ID with `ids`, oldest first."""
    conditions = []
    if ids.doi:
        conditions.append(Paper.doi == ids.doi)
    if ids.arxiv_id:
        conditions.append(Paper.arxiv_id == ids.arxiv_id)
    if ids.openalex_id:
        conditions.append(Paper.openalex_id == ids.openalex_id)
    if ids.s2_id:
        conditions.append(Paper.s2_id == ids.s2_id)
    if not conditions:
        return []
    query = select(Paper).where(or_(*conditions)).order_by(Paper.created_at, Paper.id)
    if lock:
        query = query.with_for_update()
    return list((await session.execute(query)).scalars().all())


async def _merge_hits_into(session: AsyncSession, keeper: Paper, others: list[Paper]) -> None:
    """Moves the retrieval hits of `others` to `keeper`, combining hits that share a scope."""
    hits = (await session.execute(
        select(RetrievalHit).where(RetrievalHit.paper_id.in_([o.id for o in others]))
    )).scalars().all()
    for hit in hits:
        existing = (await session.execute(
            select(RetrievalHit).where(
                RetrievalHit.paper_id == keeper.id,
                RetrievalHit.investigation_id == hit.investigation_id,
                RetrievalHit.evaluation_id.is_not_distinct_from(hit.evaluation_id),
            )
        )).scalar_one_or_none()
        if existing is None:
            hit.paper_id = keeper.id
            continue
        existing.found_by_query_ids = list(dict.fromkeys([*existing.found_by_query_ids, *hit.found_by_query_ids]))
        ranks = [r for r in (existing.source_rank, hit.source_rank) if r is not None]
        existing.source_rank = min(ranks) if ranks else None
        existing.screened = existing.screened or hit.screened
        existing.selected_for_reading = existing.selected_for_reading or hit.selected_for_reading
        await session.delete(hit)
    await session.flush()


async def _ensure_no_content(session: AsyncSession, paper_ids: list[uuid.UUID]) -> None:
    for model in (Chunk, PaperAnalysis, Quote):
        count = (await session.execute(
            select(func.count()).select_from(model).where(model.paper_id.in_(paper_ids))
        )).scalar()
        if count:
            raise PaperMergeConflict(
                f"Papers {[str(i) for i in paper_ids]} are the same work as another stored paper, but already "
                f"have {model.__tablename__}; merging them isn't supported yet (step 3)."
            )


_TITLE_PREFIX_WORDS = 3


async def find_paper_by_title(session: AsyncSession, record: PaperRecord) -> Paper | None:
    """A stored paper that is the same work as `record` by title (dedup rule 2), or None.

    Candidates are narrowed in SQL (titles starting with the same words, years close
    enough), then checked with the exact rule in Python.
    """
    words = normalize_title(record.title).split()
    if len(words) < MIN_TITLE_WORDS_FOR_TITLE_MATCH:
        return None
    prefix = " ".join(words[:_TITLE_PREFIX_WORDS])
    normalized = func.trim(func.regexp_replace(func.lower(Paper.title), "[^a-z0-9]+", " ", "g"))
    statement = select(Paper).where(normalized.like(prefix.replace("%", "") + "%")).order_by(Paper.created_at)
    if record.year is not None:
        statement = statement.where(or_(Paper.year.is_(None),
                                        Paper.year.between(record.year - MAX_YEAR_GAP, record.year + MAX_YEAR_GAP)))
    for candidate in (await session.execute(statement.with_for_update())).scalars():
        if same_work_by_title(paper_to_record(candidate), record):
            return candidate
    return None


async def _upsert_once(session: AsyncSession, record: PaperRecord) -> Paper:
    matches = await find_papers_by_ids(session, record.ids, lock=True)
    if not matches and (same_work := await find_paper_by_title(session, record)) is not None:
        logger.info("papers: %s is a version of stored paper %s (same title, year, first author)",
                    record.ids, same_work.id)
        matches = [same_work]
    if not matches:
        paper = Paper()
        _apply(paper, record)
        session.add(paper)
        await session.flush()
        return paper

    keeper, *others = matches
    merged = merge_records([paper_to_record(keeper), *(paper_to_record(o) for o in others), record])
    if others:
        await _ensure_no_content(session, [o.id for o in others])
        await _merge_hits_into(session, keeper, others)
        logger.info("papers: merged %d stored duplicates into %s", len(others), keeper.id)
        for other in others:
            await session.delete(other)
        await session.flush()  # free the other rows' IDs before the keeper takes them
    _apply(keeper, merged)
    await session.flush()
    return keeper


async def upsert_paper(session: AsyncSession, record: PaperRecord) -> Paper:
    """Saves a paper, merging with stored papers that share any external ID. Returns the stored row."""
    for attempt in (1, 2):
        try:
            async with session.begin_nested():  # a savepoint: a failed attempt leaves the session usable
                return await _upsert_once(session, record)
        except IntegrityError:
            if attempt == 2:
                raise
            # Another transaction stored the same paper in the meantime; the retry finds and merges it.
            logger.info("papers: insert conflict for %s, retrying as a merge", record.ids)
    raise AssertionError("unreachable")
