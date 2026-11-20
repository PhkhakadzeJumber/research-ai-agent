"""The Paper Reader (agent A3, doc 04): an idea-agnostic, structured reading of one paper.

read_paper(paper):
  1. renders the paper as a context packet (src/ai/context.py);
  2. runs the agent through run_structured() with the Paper Reader prompt and the
     PaperAnalysisOutput schema (src/llm.py, src/ai/prompts, src/ai/schemas);
  3. verifies every quote against the exact text the model was shown, and every
     listed name against the whole text (src/ai/evidence/verifier.py);
  4. returns a PaperReading: analysis + verification + versions + call records.

read_papers(papers) reads several papers in parallel with a concurrency limit. A failure
on one paper (refusal, cut-off, invalid output, API error) is recorded for that paper
only; the others still complete.

Storing readings is done by src/db/repositories (step 3.6); this module never touches
the database.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from src.ai.context import ChunkForReading, PaperForReading, render_paper
from src.ai.evidence.verifier import VerificationReport, verify_analysis
from src.ai.prompts import SHARED_PREAMBLE, load_prompt, system_prompt
from src.ai.schemas.paper_analysis import SCHEMA_VERSION, PaperAnalysisOutput
from src.config import RoleLlmSettings
from src.db.models import Chunk, Paper
from src.llm import LlmCallRecord, LlmError, run_structured

logger = logging.getLogger(__name__)

ROLE = "paper_reader"
DEFAULT_CONCURRENCY = 4

# Prompt caching is off for the Paper Reader. The only prefix shared across papers is
# the system prompt, which is shorter than the minimum Claude caches, so ADK's cache
# entry always ended with the paper text: every new paper paid the 1.25x cache-write
# price and no later call ever read it (step 3.7 live run: cache_read=0 on every call).
# A paper is read once and then reused from the library, so there is nothing to cache.
PROMPT_CACHE = False


def extractor_version() -> str:
    """Which prompts and schema produce a reading, e.g. 'reader-p1.1-s1' (preamble 1, role prompt 1,
    schema 1). Stored with every analysis: a change means papers may be re-read (doc 10 §5.3)."""
    schema_number = SCHEMA_VERSION.rsplit("-v", 1)[-1]
    return f"reader-p{load_prompt(SHARED_PREAMBLE).version}.{load_prompt(ROLE).version}-s{schema_number}"


@dataclass(frozen=True)
class PaperReading:
    paper_id: str
    source_level: str
    """'abstract' or 'full_text': what the model was actually shown."""
    extractor_version: str
    model: str
    analysis: PaperAnalysisOutput
    verification: VerificationReport
    records: list[LlmCallRecord]

    @property
    def cost_usd(self) -> Decimal | None:
        costs = [record.cost_usd for record in self.records]
        return None if any(cost is None for cost in costs) else sum(costs, Decimal(0))


@dataclass(frozen=True)
class ReadOutcome:
    """The result of reading one paper in a batch: a reading, or the reason it failed."""

    paper_id: str
    reading: PaperReading | None = None
    error: str | None = None


def paper_for_reading(paper: Paper, chunks: Sequence[Chunk] = ()) -> PaperForReading:
    """What the Paper Reader is shown of a stored paper (and its full-text chunks, if any)."""
    return PaperForReading(
        paper_id=str(paper.id),
        title=paper.title,
        abstract=paper.abstract,
        year=paper.year,
        venue=paper.venue,
        authors=tuple(paper.authors or ()),
        chunks=tuple(
            ChunkForReading(chunk_id=str(chunk.id), section_title=chunk.section_title, text=chunk.text)
            for chunk in sorted(chunks, key=lambda c: c.position)
        ),
    )


async def read_paper(
    paper: PaperForReading, *, settings: RoleLlmSettings | None = None, model: Any | None = None
) -> PaperReading:
    """Reads one paper. Raises ValueError (no text to read) or LlmError subclasses."""
    system = system_prompt(ROLE)
    result = await run_structured(
        ROLE, system=system.text, prompt_version=system.version_id, user_message=render_paper(paper),
        output_schema=PaperAnalysisOutput, settings=settings, model=model, cache=PROMPT_CACHE,
    )
    verification = verify_analysis(
        result.output, abstract=paper.abstract, chunks={chunk.chunk_id: chunk.text for chunk in paper.chunks}
    )
    if verification.unverified_quotes:
        logger.info("paper_reader: %s: %d of %d quotes unverified", paper.paper_id,
                    len(verification.unverified_quotes), len(verification.quotes))
    return PaperReading(
        paper_id=paper.paper_id,
        source_level=paper.source_level,
        extractor_version=extractor_version(),
        model=result.records[-1].model if result.records else (settings.model if settings else "unknown"),
        analysis=result.output,
        verification=verification,
        records=result.records,
    )


async def read_papers(
    papers: Sequence[PaperForReading],
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
) -> list[ReadOutcome]:
    """Reads papers in parallel (at most `concurrency` at once). Never raises for one paper's
    failure: each outcome holds a reading or an error. Output order = input order."""
    semaphore = asyncio.Semaphore(concurrency)

    async def read_one(paper: PaperForReading) -> ReadOutcome:
        async with semaphore:
            try:
                return ReadOutcome(paper.paper_id, reading=await read_paper(paper, settings=settings, model=model))
            except (LlmError, ValueError) as error:
                logger.warning("paper_reader: %s failed: %s", paper.paper_id, error)
                return ReadOutcome(paper.paper_id, error=f"{type(error).__name__}: {error}")

    return list(await asyncio.gather(*(read_one(paper) for paper in papers)))
