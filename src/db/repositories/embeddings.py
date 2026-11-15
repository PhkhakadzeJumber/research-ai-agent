"""Storing paper embeddings: every paper is embedded once per model, never paid for twice.

embed_papers(session, embedder, papers) embeds the papers that have no vector from this
embedder's model yet (title + abstract), stores vector and model name together, and
skips the rest. A paper embedded by a different model is re-embedded, because vectors
from different models can't be compared.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Paper
from src.retrieval.embeddings import Embedder


def paper_text(paper: Paper) -> str:
    """What represents a paper for embedding: its title, then its abstract if there is one."""
    return f"{paper.title}\n\n{paper.abstract}" if paper.abstract else paper.title


async def embed_papers(session: AsyncSession, embedder: Embedder, papers: Sequence[Paper]) -> int:
    """Embeds the papers missing a vector from this model. Returns how many were embedded."""
    missing = [paper for paper in papers if paper.embedding_model != embedder.model_id or paper.embedding is None]
    if not missing:
        return 0
    vectors = await embedder.embed_documents([paper_text(paper) for paper in missing])
    for paper, vector in zip(missing, vectors, strict=True):
        paper.embedding = vector
        paper.embedding_model = embedder.model_id
    await session.flush()
    return len(missing)
