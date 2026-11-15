"""Live check of the real Vertex AI embedder. Excluded by default; run with: pytest -m live

Needs the Google Cloud setup: EUREKA_EMBEDDING_MODEL, GOOGLE_CLOUD_PROJECT and
GOOGLE_CLOUD_LOCATION in .env, and `gcloud auth application-default login`.
Costs a tiny amount (three short texts).
"""

import asyncio

import pytest

from src.retrieval.embeddings import EMBEDDING_DIMENSIONS, VertexEmbedder, cosine_similarity

pytestmark = pytest.mark.live


def test_real_vertex_embeddings():
    embedder = VertexEmbedder.from_settings()

    async def go():
        documents = await embedder.embed_documents([
            "Measuring hallucination of small language models in question answering",
            "Protein folding with graph neural networks",
        ])
        query = await embedder.embed_query("hallucination in small language models")
        return documents, query

    (related, unrelated), query = asyncio.run(go())
    assert len(related) == len(unrelated) == len(query) == EMBEDDING_DIMENSIONS
    assert cosine_similarity(query, related) > cosine_similarity(query, unrelated)
