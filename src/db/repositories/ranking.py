"""Ranking the papers of one search scope by relevance to a query.

rank_scope(...) for one scope (an investigation's landscape phase, or one evaluation):
  1. embeds the query and any paper in the scope that has no vector from this model yet;
  2. lets Postgres compute cosine similarity with pgvector;
  3. stores the similarity in retrieval_hits.embed_score;
  4. returns the hits ordered by relevance_score (similarity + small citation/recency priors).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import timeutil
from src.db.models import Paper, RetrievalHit
from src.db.repositories.embeddings import embed_papers
from src.retrieval.embeddings import Embedder
from src.retrieval.ranking import RankWeights, relevance_score


@dataclass(frozen=True)
class RankedHit:
    hit: RetrievalHit
    paper: Paper
    similarity: float
    score: float


async def rank_scope(
    session: AsyncSession,
    embedder: Embedder,
    *,
    investigation_id: uuid.UUID,
    query: str,
    evaluation_id: uuid.UUID | None = None,
    weights: RankWeights = RankWeights(),
) -> list[RankedHit]:
    """Ranks the scope's papers against `query`, most relevant first."""
    in_scope = (
        select(RetrievalHit, Paper)
        .join(Paper, Paper.id == RetrievalHit.paper_id)
        .where(
            RetrievalHit.investigation_id == investigation_id,
            RetrievalHit.evaluation_id.is_not_distinct_from(evaluation_id),
        )
    )
    rows = (await session.execute(in_scope)).all()
    if not rows:
        return []

    await embed_papers(session, embedder, [paper for _, paper in rows])
    query_vector = await embedder.embed_query(query)

    # 1 - cosine distance = cosine similarity, computed by pgvector in the database.
    similarity = (1 - Paper.embedding.cosine_distance(query_vector)).label("similarity")
    scored = (await session.execute(
        in_scope.add_columns(similarity).where(Paper.embedding_model == embedder.model_id)
    )).all()

    current_year = timeutil.now().year
    ranked = []
    for hit, paper, value in scored:
        hit.embed_score = float(value)
        ranked.append(RankedHit(
            hit=hit, paper=paper, similarity=float(value),
            score=relevance_score(float(value), citation_count=paper.citation_count, year=paper.year,
                                  current_year=current_year, weights=weights),
        ))
    await session.flush()
    ranked.sort(key=lambda item: item.score, reverse=True)
    return ranked
