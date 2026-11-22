"""Reading papers into the library: "make sure these papers have an analysis".

read_into_library(sessions, paper_ids):
  - Cache: a paper that already has an analysis for the current extractor version and
    source level is reused, never read (or paid for) again.
  - Single-flight: if two runs in this process ask for the same paper at the same time,
    Claude is called once and both get the result. Across processes, the database's
    unique key (paper, extractor version, source level) still prevents duplicate rows;
    at worst one call is wasted.
  - Papers are read in parallel (concurrency limit); database writes happen one at a
    time, each saved as soon as its paper finishes, so a crash keeps finished papers.
  - A failing paper (no text, refusal, invalid output, API error) is reported in its
    outcome and doesn't stop the others.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.agents.paper_reader import (
    DEFAULT_CONCURRENCY,
    PaperReading,
    extractor_version,
    paper_for_reading,
    read_paper,
)
from src.ai.context import PaperForReading
from src.config import RoleLlmSettings
from src.db.models import Chunk, Paper
from src.db.models import PaperAnalysis
from src.db.repositories.analyses import QuoteToStore, find_analysis, store_analysis, store_quotes
from src.db.repositories.llm_calls import record_llm_calls
from src.llm import LlmAccountError, LlmError

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

QUOTED_FIELDS = ("research_questions", "findings", "limitations", "future_work", "assumptions")

# In-flight readings in this process: (paper ID, extractor version, source level) -> task.
_in_flight: dict[tuple[str, str, str], asyncio.Task] = {}


@dataclass(frozen=True)
class LibraryOutcome:
    paper_id: uuid.UUID
    analysis_id: uuid.UUID | None = None
    reused: bool = False
    """True if a stored analysis was reused (no Claude call)."""
    verified_quotes: int | None = None
    quotes: int | None = None
    error: str | None = None


async def store_reading(
    session: AsyncSession,
    reading: PaperReading,
    *,
    investigation_id: uuid.UUID | None = None,
    evaluation_id: uuid.UUID | None = None,
) -> PaperAnalysis:
    """Stores one reading as a unit: its quotes (with verification results), the analysis
    with each quote referenced by quote ID (doc 10 §5.3), and the Claude call records.
    If an analysis with the same key already exists, it is returned unchanged."""
    paper_id = uuid.UUID(reading.paper_id)
    existing = await find_analysis(session, paper_id, reading.extractor_version, reading.source_level)
    if existing is not None:
        return existing

    checks = reading.verification.quotes
    quotes = await store_quotes(session, paper_id, [
        QuoteToStore(c.location, c.text, c.chunk_id, c.verified, c.score) for c in checks
    ])
    references = {}
    for check in checks:
        quote = quotes[check.location]
        verified = bool(quote is not None and check.verified)
        references[check.location] = {
            "quote_id": str(quote.id) if quote is not None else None,
            "verified": verified,
            "reason": None if verified else (check.reason or "cites a chunk that doesn't exist"),
        }

    content = reading.analysis.model_dump(mode="json")
    for field in QUOTED_FIELDS:
        for index, item in enumerate(content.get(field, [])):
            item["quote"] = references[f"{field}[{index}]"]
    content["verification"] = {
        "quotes": len(checks),
        "verified_quotes": sum(1 for reference in references.values() if reference["verified"]),
        "missing_names": [check.name for check in reading.verification.missing_names],
    }

    analysis = await store_analysis(
        session, paper_id=paper_id, extractor_version=reading.extractor_version,
        source_level=reading.source_level, model=reading.model, content=content,
    )
    await record_llm_calls(session, reading.records, investigation_id=investigation_id, evaluation_id=evaluation_id)
    return analysis


async def _load_views(session: AsyncSession, paper_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, PaperForReading]:
    papers = (await session.execute(select(Paper).where(Paper.id.in_(paper_ids)))).scalars().all()
    chunks = (await session.execute(select(Chunk).where(Chunk.paper_id.in_(paper_ids)))).scalars().all()
    by_paper: dict[uuid.UUID, list[Chunk]] = {}
    for chunk in chunks:
        by_paper.setdefault(chunk.paper_id, []).append(chunk)
    return {paper.id: paper_for_reading(paper, by_paper.get(paper.id, ())) for paper in papers}


async def read_into_library(
    sessions: SessionFactory,
    paper_ids: Sequence[uuid.UUID],
    *,
    investigation_id: uuid.UUID | None = None,
    evaluation_id: uuid.UUID | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> list[LibraryOutcome]:
    """Ensures each paper has an analysis; returns one outcome per paper, in input order."""
    unique_ids = list(dict.fromkeys(paper_ids))
    version = extractor_version()
    async with sessions() as session:
        views = await _load_views(session, unique_ids)
        cached: dict[uuid.UUID, LibraryOutcome] = {}
        for paper_id, view in views.items():
            stored = await find_analysis(session, paper_id, version, view.source_level)
            if stored is not None:
                verification = stored.content.get("verification", {})
                cached[paper_id] = LibraryOutcome(paper_id, stored.id, reused=True,
                                                  verified_quotes=verification.get("verified_quotes"),
                                                  quotes=verification.get("quotes"))

    semaphore = asyncio.Semaphore(concurrency)
    write_lock = asyncio.Lock()

    async def read_and_store(paper_id: uuid.UUID, view: PaperForReading) -> LibraryOutcome:
        async with semaphore:
            try:
                reading: PaperReading = await read_paper(view, settings=settings, model=model)
            except LlmAccountError:  # the whole account is refused: stop, don't degrade
                raise
            except (LlmError, ValueError) as error:
                logger.warning("reading: %s failed: %s", paper_id, error)
                return LibraryOutcome(paper_id, error=f"{type(error).__name__}: {error}")
        async with write_lock, sessions() as session:
            stored = await store_reading(session, reading, investigation_id=investigation_id,
                                         evaluation_id=evaluation_id)
            return LibraryOutcome(paper_id, stored.id, verified_quotes=len(reading.verification.verified_quotes),
                                  quotes=len(reading.verification.quotes))

    def single_flight(paper_id: uuid.UUID, view: PaperForReading) -> asyncio.Task:
        key = (str(paper_id), version, view.source_level)
        task = _in_flight.get(key)
        if task is None or task.done() or task.get_loop() is not asyncio.get_running_loop():
            task = asyncio.ensure_future(read_and_store(paper_id, view))
            _in_flight[key] = task
            task.add_done_callback(lambda finished, key=key: _in_flight.pop(key, None)
                                   if _in_flight.get(key) is finished else None)
        return task

    pending = {
        paper_id: single_flight(paper_id, view)
        for paper_id, view in views.items() if paper_id not in cached
    }
    finished = dict(zip(pending, await asyncio.gather(*pending.values()))) if pending else {}

    outcomes = []
    for paper_id in paper_ids:
        if paper_id in cached:
            outcomes.append(cached[paper_id])
        elif paper_id in finished:
            outcomes.append(finished[paper_id])
        else:
            outcomes.append(LibraryOutcome(paper_id, error="paper not found in the library"))
    return outcomes
