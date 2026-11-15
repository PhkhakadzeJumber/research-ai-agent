"""Paper analyses and quotes in the global library (paper_analyses, quotes).

Generic building blocks, with no knowledge of agents (imports go downward: ai -> db):
  - find_analysis(...): the cache lookup, one analysis per (paper, extractor version, source level);
  - store_quotes(...): get-or-create Quote rows (global, deduplicated per paper + chunk +
    text) with the verifier's result. A quote citing a chunk that doesn't exist (or belongs
    to another paper) can't become a row: it would point nowhere. Its result is None;
  - store_analysis(...): the analysis row itself.
The Paper Reader workflow (src/ai/workflows/reading.py) combines them into one unit of work.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Chunk, PaperAnalysis, Quote
from src.db.models.library import quote_text_hash


@dataclass(frozen=True)
class QuoteToStore:
    location: str
    """Where the quote sits in the analysis, e.g. 'findings[1]' (the key of the result)."""
    text: str
    chunk_id: str | None
    """A chunk ID as the model gave it; None = the abstract."""
    verified: bool
    score: float


async def find_analysis(
    session: AsyncSession, paper_id: uuid.UUID, extractor_version: str, source_level: str
) -> PaperAnalysis | None:
    return (await session.execute(
        select(PaperAnalysis).where(
            PaperAnalysis.paper_id == paper_id,
            PaperAnalysis.extractor_version == extractor_version,
            PaperAnalysis.source_level == source_level,
        )
    )).scalar_one_or_none()


def _as_uuid(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(value) if value else None
    except ValueError:
        return None


async def store_quotes(
    session: AsyncSession, paper_id: uuid.UUID, quotes: Sequence[QuoteToStore]
) -> dict[str, Quote | None]:
    """Get-or-create a Quote row per quote. Returns location -> Quote (None if its chunk doesn't exist)."""
    stored: dict[str, Quote | None] = {}
    for quote in quotes:
        chunk_id = None
        if quote.chunk_id is not None:
            chunk_id = _as_uuid(quote.chunk_id)
            chunk = await session.get(Chunk, chunk_id) if chunk_id else None
            if chunk is None or chunk.paper_id != paper_id:
                stored[quote.location] = None
                continue
        text_hash = quote_text_hash(quote.text)
        await session.execute(
            insert(Quote).values(
                id=uuid.uuid4(), paper_id=paper_id, chunk_id=chunk_id, text=quote.text, text_hash=text_hash,
                source_level="full_text" if chunk_id else "abstract", verified=quote.verified,
                verification_score=quote.score,
            ).on_conflict_do_nothing(constraint="uq_quotes_paper_id_chunk_id_text_hash")
        )
        stored[quote.location] = (await session.execute(
            select(Quote).where(Quote.paper_id == paper_id, Quote.chunk_id.is_not_distinct_from(chunk_id),
                                Quote.text_hash == text_hash)
        )).scalar_one()
    return stored


async def store_analysis(
    session: AsyncSession,
    *,
    paper_id: uuid.UUID,
    extractor_version: str,
    source_level: str,
    model: str,
    content: dict[str, Any],
) -> PaperAnalysis:
    """Stores an analysis; if one with the same key exists, returns that one unchanged."""
    existing = await find_analysis(session, paper_id, extractor_version, source_level)
    if existing is not None:
        return existing
    analysis = PaperAnalysis(paper_id=paper_id, extractor_version=extractor_version, model=model,
                             source_level=source_level, content=content)
    session.add(analysis)
    await session.flush()
    return analysis
