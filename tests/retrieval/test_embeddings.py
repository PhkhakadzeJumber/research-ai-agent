"""Tests for src/retrieval/embeddings.py and src/retrieval/ranking.py (no network, no Google account)."""

import asyncio
from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors

from src.config import ConfigError, load_embedding_settings
from src.retrieval.embeddings import (
    EMBEDDING_DIMENSIONS,
    TASK_DOCUMENT,
    TASK_QUERY,
    EmbeddingError,
    FakeEmbedder,
    VertexEmbedder,
    cosine_similarity,
)
from src.retrieval.http import RetryPolicy
from src.retrieval.ranking import citation_prior, recency_prior, relevance_score

NO_JITTER = RetryPolicy(max_attempts=3, base_delay=1.0, jitter=False)


def run(coro):
    return asyncio.run(coro)


# --- FakeEmbedder ------------------------------------------------------------------------------

def test_fake_embedder_is_deterministic_and_normalized():
    embedder = FakeEmbedder()
    first, again = run(embedder.embed_documents(["small language models", "small language models"]))
    assert first == again
    assert len(first) == EMBEDDING_DIMENSIONS
    assert abs(sum(v * v for v in first) - 1.0) < 1e-9


def test_fake_embedder_puts_related_texts_closer():
    embedder = FakeEmbedder()
    query = run(embedder.embed_query("hallucination in small language models"))
    related, unrelated = run(embedder.embed_documents([
        "Measuring hallucination of small language models in question answering",
        "Protein folding with graph neural networks",
    ]))
    assert cosine_similarity(query, related) > cosine_similarity(query, unrelated)


def test_cosine_similarity():
    assert cosine_similarity([1, 0], [1, 0]) == 1.0
    assert cosine_similarity([1, 0], [0, 1]) == 0.0
    assert cosine_similarity([0, 0], [1, 1]) == 0.0


# --- VertexEmbedder with a fake client ---------------------------------------------------------------

class FakeVertexClient:
    """Mimics client.aio.models.embed_content; can fail first with given errors."""

    def __init__(self, dimensions=EMBEDDING_DIMENSIONS, failures=(), wrong_count=False, truncated=False):
        self.calls = []
        self._dimensions = dimensions
        self._failures = list(failures)
        self._wrong_count = wrong_count
        self._truncated = truncated
        self.aio = SimpleNamespace(models=SimpleNamespace(embed_content=self._embed_content))

    async def _embed_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": list(contents), "config": dict(config)})
        if self._failures:
            raise self._failures.pop(0)
        count = len(contents) - 1 if self._wrong_count else len(contents)
        return SimpleNamespace(embeddings=[
            SimpleNamespace(values=[0.1] * self._dimensions,
                            statistics=SimpleNamespace(truncated=self._truncated, token_count=5))
            for _ in range(count)
        ])


def vertex(client, batch_size=2, sleep=None):
    async def no_wait(seconds):
        delays.append(seconds)

    delays = []
    embedder = VertexEmbedder("gemini-embedding-001", project="p", location="europe-west1",
                              batch_size=batch_size, client=client, retry=NO_JITTER, sleep=sleep or no_wait)
    return embedder, delays


def api_error(code, status):
    cls = genai_errors.ServerError if code >= 500 else genai_errors.ClientError
    return cls(code, {"error": {"code": code, "status": status, "message": "test"}})


def test_documents_are_sent_in_batches_with_the_right_settings():
    client = FakeVertexClient()
    embedder, _ = vertex(client, batch_size=2)
    vectors = run(embedder.embed_documents(["a", "b", "c", "d", "e"]))

    assert len(vectors) == 5
    assert [len(call["contents"]) for call in client.calls] == [2, 2, 1]
    config = client.calls[0]["config"]
    assert config == {"task_type": TASK_DOCUMENT, "output_dimensionality": EMBEDDING_DIMENSIONS, "auto_truncate": True}
    assert client.calls[0]["model"] == "gemini-embedding-001"


def test_query_uses_the_query_task_type():
    client = FakeVertexClient()
    embedder, _ = vertex(client)
    assert len(run(embedder.embed_query("an idea"))) == EMBEDDING_DIMENSIONS
    assert client.calls[0]["config"]["task_type"] == TASK_QUERY


def test_empty_input_makes_no_request():
    client = FakeVertexClient()
    embedder, _ = vertex(client)
    assert run(embedder.embed_documents([])) == []
    assert client.calls == []


def test_rate_limits_and_server_errors_are_retried():
    client = FakeVertexClient(failures=[api_error(429, "RESOURCE_EXHAUSTED"), api_error(503, "UNAVAILABLE")])
    embedder, delays = vertex(client)
    assert len(run(embedder.embed_documents(["a"]))) == 1
    assert len(client.calls) == 3
    assert delays == [1.0, 2.0]


def test_permanent_errors_are_not_retried():
    client = FakeVertexClient(failures=[api_error(403, "PERMISSION_DENIED")])
    embedder, _ = vertex(client)
    with pytest.raises(EmbeddingError, match="403 PERMISSION_DENIED"):
        run(embedder.embed_documents(["a"]))
    assert len(client.calls) == 1


def test_gives_up_after_the_last_attempt():
    client = FakeVertexClient(failures=[api_error(503, "UNAVAILABLE")] * 3)
    embedder, _ = vertex(client)
    with pytest.raises(EmbeddingError, match="after 3 attempt"):
        run(embedder.embed_documents(["a"]))


@pytest.mark.parametrize(
    "client, message",
    [
        (FakeVertexClient(dimensions=3072), "expected 768 dimensions"),
        (FakeVertexClient(wrong_count=True), "expected 2 embeddings"),
    ],
)
def test_unusable_responses_are_rejected(client, message):
    embedder, _ = vertex(client)
    with pytest.raises(EmbeddingError, match=message):
        run(embedder.embed_documents(["a", "b"]))


def test_truncated_inputs_are_still_returned(caplog):
    caplog.set_level("INFO")
    embedder, _ = vertex(FakeVertexClient(truncated=True))
    assert len(run(embedder.embed_documents(["a very long text"]))) == 1
    assert "truncated" in caplog.text


# --- settings -------------------------------------------------------------------------------------------

def test_embedding_settings():
    settings = load_embedding_settings({
        "EUREKA_EMBEDDING_MODEL": "gemini-embedding-001", "GOOGLE_CLOUD_PROJECT": "eureka-research",
        "GOOGLE_CLOUD_LOCATION": "europe-west1",
    })
    assert (settings.model, settings.project, settings.location, settings.batch_size) == (
        "gemini-embedding-001", "eureka-research", "europe-west1", 50)


@pytest.mark.parametrize(
    "environ, message",
    [
        ({}, "EUREKA_EMBEDDING_MODEL"),
        ({"EUREKA_EMBEDDING_MODEL": "m"}, "GOOGLE_CLOUD_PROJECT"),
        ({"EUREKA_EMBEDDING_MODEL": "m", "GOOGLE_CLOUD_PROJECT": "p"}, "GOOGLE_CLOUD_LOCATION"),
        ({"EUREKA_EMBEDDING_MODEL": "m", "GOOGLE_CLOUD_PROJECT": "p", "GOOGLE_CLOUD_LOCATION": "l",
          "EUREKA_EMBEDDING_BATCH_SIZE": "0"}, "EUREKA_EMBEDDING_BATCH_SIZE"),
    ],
)
def test_missing_or_invalid_embedding_settings(environ, message):
    with pytest.raises(ConfigError, match=message):
        load_embedding_settings(environ)


# --- ranking ------------------------------------------------------------------------------------------------

def test_priors():
    assert citation_prior(None) == 0.0 and citation_prior(0) == 0.0
    assert 0 < citation_prior(10) < citation_prior(1000) < citation_prior(10_000) == 1.0
    assert citation_prior(1_000_000) == 1.0
    assert recency_prior(2026, 2026) == 1.0
    assert recency_prior(2021, 2026) == 0.5
    assert recency_prior(2010, 2026) == 0.0
    assert recency_prior(None, 2026) == 0.0


def test_priors_never_beat_a_clearly_better_match():
    famous_rough_match = relevance_score(0.60, citation_count=50_000, year=2026, current_year=2026)
    obscure_exact_match = relevance_score(0.75, citation_count=0, year=2015, current_year=2026)
    assert obscure_exact_match > famous_rough_match  # priors add at most 0.10 in total
