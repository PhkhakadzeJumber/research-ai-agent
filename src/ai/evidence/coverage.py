"""The coverage snapshot (doc 07 §5, doc 10 §7.1): how thorough an evaluation's search was.

compute_coverage() reads the search trail and the evaluation's counters; nothing here is
asserted by a model. It's shown with every novelty verdict, and frozen into the
evaluation at the `concluding` step (step 4.9). "We found nothing" means very different
things after 20 papers and after 300; the verdict engine uses these numbers.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Chunk, Evaluation, Paper, PaperAnalysis, RetrievalHit, SearchQuery

ALL_SOURCES = ("openalex", "arxiv", "semantic_scholar")
ALWAYS_BLIND = (
    "Search terms are English; literature in other languages is mostly not covered.",
    "References and citations of the papers found were not followed (no snowballing yet).",
)


async def compute_coverage(session: AsyncSession, evaluation_id: uuid.UUID) -> dict[str, Any]:
    evaluation = await session.get(Evaluation, evaluation_id)
    counters = evaluation.counters or {}
    in_scope = RetrievalHit.evaluation_id == evaluation_id

    queries = (await session.execute(
        select(SearchQuery.source, SearchQuery.origin, SearchQuery.error)
        .where(SearchQuery.evaluation_id == evaluation_id)
    )).all()
    sources: dict[str, dict[str, int]] = {}
    for source, _, error in queries:
        entry = sources.setdefault(source, {"searches": 0, "failed": 0})
        entry["searches"] += 1
        entry["failed"] += 1 if error else 0
    distinct_queries = (await session.execute(
        select(SearchQuery.origin, func.count(func.distinct(SearchQuery.query_text)))
        .where(SearchQuery.evaluation_id == evaluation_id).group_by(SearchQuery.origin)
    )).all()
    origins = {origin: count for origin, count in distinct_queries}
    sources_ok = sum(1 for entry in sources.values() if entry["failed"] < entry["searches"])

    years = (await session.execute(
        select(func.min(Paper.year), func.max(Paper.year)).join(RetrievalHit, RetrievalHit.paper_id == Paper.id)
        .where(in_scope)
    )).one()
    relevant = (await session.execute(
        select(func.count()).select_from(RetrievalHit).where(in_scope, RetrievalHit.llm_score >= 2)
    )).scalar_one()

    # Papers actually read: selected for reading AND holding an analysis (a failed reading has none).
    selected = select(RetrievalHit.paper_id).where(in_scope, RetrievalHit.selected_for_reading.is_(True))
    read_ids = select(PaperAnalysis.paper_id).where(PaperAnalysis.paper_id.in_(selected))
    read_tiers = dict((await session.execute(
        select(Paper.tier, func.count()).where(Paper.id.in_(read_ids)).group_by(Paper.tier)
    )).all())
    full_text = (await session.execute(
        select(func.count(func.distinct(Chunk.paper_id))).where(Chunk.paper_id.in_(read_ids))
    )).scalar_one()
    read = int(counters.get("read_now", 0)) + int(counters.get("read_reused", 0))

    blind_spots = [f"{name} was not searched." for name in ALL_SOURCES if name not in sources]
    blind_spots += [f"{name} failed for {entry['failed']} of {entry['searches']} searches."
                    for name, entry in sorted(sources.items()) if entry["failed"]]
    if read and not full_text:
        blind_spots.append("Every paper was read from its abstract only (no full text).")
    if counters.get("relevant_not_selected"):
        blind_spots.append(f"{counters['relevant_not_selected']} relevant papers were not read (reading limit).")
    if counters.get("relevant_unreadable"):
        blind_spots.append(f"{counters['relevant_unreadable']} relevant papers had no abstract or full text.")
    blind_spots += list(ALWAYS_BLIND)

    return {
        "sources": sources,
        "sources_ok": sources_ok,
        "queries_by_origin": origins,
        "years": {"from": years[0], "to": years[1]},
        "funnel": {
            "retrieved": int(counters.get("retrieved", 0)),
            "unique": int(counters.get("unique_papers", 0)),
            "screened": int(counters.get("screened", 0)),
            "relevant": relevant,
            "read": read,
            "read_failed": int(counters.get("read_failed", 0)),
        },
        "read_full_text": full_text,
        "read_abstract_only": max(read - full_text, 0),
        "tier_mix": {"peer_reviewed": read_tiers.get(1, 0), "preprint": read_tiers.get(2, 0)},
        "skeptic_rounds": [],
        "budgets_hit": [],
        "blind_spots": blind_spots,
    }


def coverage_note(coverage: dict[str, Any]) -> str:
    """One paragraph for the Novelty Investigator's input."""
    funnel = coverage["funnel"]
    years = coverage["years"]
    sources = ", ".join(sorted(coverage["sources"])) or "none"
    return (f"Sources searched: {sources}. {funnel['unique']} unique papers found "
            f"(years {years['from']}-{years['to']}), {funnel['screened']} screened, {funnel['relevant']} judged "
            f"relevant, {funnel['read']} read ({coverage['read_abstract_only']} from the abstract only). "
            f"Known blind spots: {' '.join(coverage['blind_spots'])}")
