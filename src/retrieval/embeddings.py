"""Embeddings: turning texts into vectors, so "how close is this paper to this idea?"
becomes a number (cosine similarity), even when the texts share no words.

- Embedder: the interface. Two methods, because embedding models treat them
  differently: documents (papers, passages) and queries (an idea, a search).
- VertexEmbedder: Google's Vertex AI embedding API via the google-genai library.
  Batched, with retries on rate limits and server errors, and checks on every
  response. Credentials are found by Google's library itself
  (`gcloud auth application-default login` locally): no key in .env or code.
- FakeEmbedder: deterministic vectors for tests. No network, no cost. Texts that
  share words get similar vectors, so ranking can be tested meaningfully.

EMBEDDING_DIMENSIONS is fixed because the database columns have a fixed size
(migration 0004). Vectors from different models are never compared: every stored
vector records the model that made it (doc 06 §2.4).

Model choice: gemini-embedding-001 at 768 dimensions. It's the Gemini embedding
model that google-genai sends in batches on Vertex AI (the others go one text per
request). The batch-size limit is a server-side detail, so it's a setting
(EUREKA_EMBEDDING_BATCH_SIZE); the first real run confirms it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
from collections.abc import Sequence
from typing import Any, Protocol

from src.config import get_embedding_settings
from src.retrieval.http import RetryPolicy

logger = logging.getLogger(__name__)

EMBEDDING_DIMENSIONS = 768
TASK_DOCUMENT = "RETRIEVAL_DOCUMENT"
TASK_QUERY = "RETRIEVAL_QUERY"
RETRYABLE_CODES = frozenset({408, 429, 500, 502, 503, 504})


class EmbeddingError(RuntimeError):
    """Embedding failed for good (after retries), or the response was unusable."""


class Embedder(Protocol):
    model_id: str
    dimensions: int

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """1.0 = same direction, 0.0 = unrelated. Used in tests; the database computes it in production."""
    dot = sum(x * y for x, y in zip(a, b))
    norms = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norms if norms else 0.0


# --- fake embedder (tests) ---------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9]+")


class FakeEmbedder:
    """Deterministic "bag of words" vectors: each word adds to one hashed dimension.
    Texts sharing words point in similar directions. For tests only."""

    model_id = "fake-hashed-words-v1"

    def __init__(self, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        self.dimensions = dimensions
        self.calls: list[tuple[str, int]] = []  # (task, number of texts), for assertions

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in _WORD.findall(text.lower()):
            index = int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "big") % self.dimensions
            vector[index] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append((TASK_DOCUMENT, len(texts)))
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        self.calls.append((TASK_QUERY, 1))
        return self._vector(text)


# --- Vertex AI embedder ----------------------------------------------------------------------------

class VertexEmbedder:
    """Embeddings from Vertex AI. Create once and reuse."""

    def __init__(
        self,
        model: str,
        *,
        project: str,
        location: str,
        dimensions: int = EMBEDDING_DIMENSIONS,
        batch_size: int = 50,
        max_concurrency: int = 4,
        retry: RetryPolicy | None = None,
        client: Any | None = None,
        sleep: Any = asyncio.sleep,
    ) -> None:
        """
        Args:
            model, project, location: from EmbeddingSettings.
            dimensions: vector size requested from the model (must match the database).
            batch_size: texts per request; max_concurrency: requests running at once.
            client, sleep: normally left empty; tests pass fakes (no network, no waiting).
        """
        self.model_id = model
        self.dimensions = dimensions
        self.batch_size = batch_size
        self._project = project
        self._location = location
        self._client = client
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._retry = retry or RetryPolicy()
        self._sleep = sleep

    @classmethod
    def from_settings(cls) -> VertexEmbedder:
        settings = get_embedding_settings()
        return cls(settings.model, project=settings.project, location=settings.location,
                   batch_size=settings.batch_size)

    def _get_client(self) -> Any:
        if self._client is None:
            from google import genai  # imported here: only needed when real embeddings are used

            self._client = genai.Client(vertexai=True, project=self._project, location=self._location)
        return self._client

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, same order. Texts are sent in batches, several batches at once."""
        texts = list(texts)
        if not texts:
            return []
        batches = [texts[i:i + self.batch_size] for i in range(0, len(texts), self.batch_size)]
        results = await asyncio.gather(*(self._embed(batch, TASK_DOCUMENT) for batch in batches))
        return [vector for batch_vectors in results for vector in batch_vectors]

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([text], TASK_QUERY))[0]

    async def _embed(self, texts: list[str], task: str) -> list[list[float]]:
        from google.genai import errors as genai_errors

        config = {"task_type": task, "output_dimensionality": self.dimensions, "auto_truncate": True}
        attempts = self._retry.max_attempts
        async with self._semaphore:
            for attempt in range(1, attempts + 1):
                try:
                    response = await self._get_client().aio.models.embed_content(
                        model=self.model_id, contents=texts, config=config
                    )
                    return self._vectors_from(response, expected=len(texts))
                except genai_errors.APIError as error:
                    if error.code not in RETRYABLE_CODES or attempt == attempts:
                        raise EmbeddingError(
                            f"embedding failed ({error.code} {error.status}) after {attempt} attempt(s)"
                        ) from error
                    delay = self._retry.backoff(attempt)
                    logger.warning("embeddings: %s, retry %d/%d in %.1fs", error.code, attempt, attempts, delay)
                    await self._sleep(delay)
        raise AssertionError("unreachable")

    def _vectors_from(self, response: Any, *, expected: int) -> list[list[float]]:
        embeddings = list(getattr(response, "embeddings", None) or [])
        if len(embeddings) != expected:
            raise EmbeddingError(f"expected {expected} embeddings, got {len(embeddings)}")
        vectors = []
        truncated = 0
        for embedding in embeddings:
            values = list(embedding.values or [])
            if len(values) != self.dimensions:
                raise EmbeddingError(f"expected {self.dimensions} dimensions, got {len(values)}")
            statistics = getattr(embedding, "statistics", None)
            if statistics is not None and getattr(statistics, "truncated", False):
                truncated += 1
            vectors.append(values)
        if truncated:
            logger.info("embeddings: %d of %d texts were truncated to the model's input limit", truncated, expected)
        return vectors
