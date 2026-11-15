"""Eureka settings, read once from environment variables.

In development the variables come from the `.env` file in the repo root
(see `.env.example` for the names). In the cloud they come from the
environment directly (Secret Manager). ANTHROPIC_API_KEY is NOT stored here:
the Anthropic SDK reads it from the environment itself, so it never passes
through our code. Keys our own code must send (e.g. OPENALEX_API_KEY) are
stored with repr=False, so printing a settings object never shows them.

There are independent groups, so a missing value in one never blocks the others:
- Settings (get_settings): the LLM settings.
- RetrievalSettings (get_retrieval_settings): the literature-source settings.
- DatabaseSettings (get_database_settings): the database connections.
- EmbeddingSettings (get_embedding_settings): the Vertex AI embedding model.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv
from sqlalchemy.engine import URL, make_url

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


class ConfigError(RuntimeError):
    """A required setting is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    llm_model: str
    """Claude model ID used for LLM calls (EUREKA_LLM_MODEL)."""

    llm_effort: str
    """Default reasoning effort: low | medium | high | xhigh | max (EUREKA_LLM_EFFORT)."""

    llm_max_tokens: int
    """Default output token limit per call (EUREKA_LLM_MAX_TOKENS)."""


def load_settings(environ: Mapping[str, str]) -> Settings:
    """Builds Settings from a mapping of environment variables.

    Kept separate from get_settings() so tests can pass a plain dict
    instead of touching the real environment or the .env file.
    """
    model = environ.get("EUREKA_LLM_MODEL", "").strip()
    if not model:
        raise ConfigError(
            "EUREKA_LLM_MODEL is not set. Add it to your .env file "
            "(see .env.example for the expected variables)."
        )

    effort = environ.get("EUREKA_LLM_EFFORT", "medium").strip() or "medium"
    if effort not in EFFORT_LEVELS:
        raise ConfigError(
            f"EUREKA_LLM_EFFORT must be one of {', '.join(EFFORT_LEVELS)}, got {effort!r}."
        )

    raw_max_tokens = environ.get("EUREKA_LLM_MAX_TOKENS", "16000").strip() or "16000"
    try:
        max_tokens = int(raw_max_tokens)
    except ValueError:
        raise ConfigError(
            f"EUREKA_LLM_MAX_TOKENS must be a whole number, got {raw_max_tokens!r}."
        ) from None
    if max_tokens <= 0:
        raise ConfigError("EUREKA_LLM_MAX_TOKENS must be greater than 0.")

    return Settings(llm_model=model, llm_effort=effort, llm_max_tokens=max_tokens)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Returns the app settings, loading `.env` on first use.

    Variables already set in the real environment win over `.env`
    (that's how the cloud deployment overrides them).
    """
    load_dotenv()
    return load_settings(os.environ)


@dataclass(frozen=True)
class RetrievalSettings:
    openalex_api_key: str | None = field(default=None, repr=False)
    """Free personal OpenAlex key (OPENALEX_API_KEY). Optional, but without it all
    requests share a tiny daily budget per IP address. Get one at openalex.org/settings/api."""

    openalex_mailto: str | None = field(default=None, repr=False)
    """Optional contact email sent to OpenAlex (OPENALEX_MAILTO)."""


def load_retrieval_settings(environ: Mapping[str, str]) -> RetrievalSettings:
    """Builds RetrievalSettings from a mapping of environment variables (empty values = not set)."""
    return RetrievalSettings(
        openalex_api_key=environ.get("OPENALEX_API_KEY", "").strip() or None,
        openalex_mailto=environ.get("OPENALEX_MAILTO", "").strip() or None,
    )


@lru_cache(maxsize=1)
def get_retrieval_settings() -> RetrievalSettings:
    """Returns the retrieval settings, loading `.env` on first use."""
    load_dotenv()
    return load_retrieval_settings(os.environ)


DEFAULT_DB_HOST = "127.0.0.1"  # matches compose.yaml, which publishes the port on 127.0.0.1 only
DEFAULT_DB_PORT = 5434  # 5432/5433 are often taken by locally installed Postgres servers
DEFAULT_TEST_DB = "eureka_test"  # created by infra/db/init/01-extensions.sql


@dataclass(frozen=True)
class DatabaseSettings:
    url: str = field(repr=False)
    """SQLAlchemy URL of the main database (contains the password: never print it)."""

    test_url: str = field(repr=False)
    """SQLAlchemy URL of the test database: tests never touch the main one."""

    def redacted(self, test: bool = False) -> str:
        """The URL with the password hidden, safe for logs and messages."""
        return make_url(self.test_url if test else self.url).render_as_string(hide_password=True)


def load_database_settings(environ: Mapping[str, str]) -> DatabaseSettings:
    """Builds DatabaseSettings from environment variables.

    - DATABASE_URL (e.g. Cloud SQL in the cloud) wins if set.
    - Otherwise the URL is built from the POSTGRES_* variables that compose.yaml uses,
      so locally you only set POSTGRES_PASSWORD once.
    - TEST_DATABASE_URL wins for tests if set; otherwise it's the main URL with the
      database name replaced by POSTGRES_TEST_DB (default: eureka_test).
    """

    def value(name: str, default: str | None = None) -> str | None:
        return environ.get(name, "").strip() or default

    explicit = value("DATABASE_URL")
    if explicit:
        try:
            url = make_url(explicit)
        except Exception:  # SQLAlchemy raises ArgumentError for malformed URLs
            raise ConfigError("DATABASE_URL is not a valid database URL.") from None
    else:
        password = value("POSTGRES_PASSWORD")
        if not password:
            raise ConfigError(
                "The database isn't configured. For the local Docker database, set POSTGRES_PASSWORD "
                "in .env (see .env.example); in the cloud, set DATABASE_URL."
            )
        port = value("POSTGRES_PORT", str(DEFAULT_DB_PORT))
        if not port.isdigit():
            raise ConfigError(f"POSTGRES_PORT must be a number, got {port!r}.")
        url = URL.create(
            "postgresql+asyncpg",
            username=value("POSTGRES_USER", "eureka"),
            password=password,  # URL.create escapes special characters
            # 127.0.0.1, not "localhost": on Windows, "localhost" tries IPv6 (::1) first, and the
            # Docker port listens on IPv4 only, so every connection waited ~2 s for the fallback.
            host=value("POSTGRES_HOST", DEFAULT_DB_HOST),
            port=int(port),
            database=value("POSTGRES_DB", "eureka"),
        )

    explicit_test = value("TEST_DATABASE_URL")
    test_url = make_url(explicit_test) if explicit_test else url.set(database=value("POSTGRES_TEST_DB", DEFAULT_TEST_DB))
    if test_url.database == url.database and test_url.host == url.host and test_url.port == url.port:
        raise ConfigError("The test database must be different from the main database.")

    return DatabaseSettings(
        url=url.render_as_string(hide_password=False),
        test_url=test_url.render_as_string(hide_password=False),
    )


@lru_cache(maxsize=1)
def get_database_settings() -> DatabaseSettings:
    """Returns the database settings, loading `.env` on first use."""
    load_dotenv()
    return load_database_settings(os.environ)


@dataclass(frozen=True)
class EmbeddingSettings:
    model: str
    """Vertex AI embedding model (EUREKA_EMBEDDING_MODEL), e.g. gemini-embedding-001."""

    project: str
    """Google Cloud project ID (GOOGLE_CLOUD_PROJECT)."""

    location: str
    """Vertex AI region (GOOGLE_CLOUD_LOCATION), e.g. europe-west1."""

    batch_size: int
    """Texts per embedding request (EUREKA_EMBEDDING_BATCH_SIZE, default 50)."""


def load_embedding_settings(environ: Mapping[str, str]) -> EmbeddingSettings:
    """Builds EmbeddingSettings. Credentials are NOT here: Google's library finds them itself
    (`gcloud auth application-default login` locally, the service account in the cloud)."""

    def required(name: str, hint: str) -> str:
        value = environ.get(name, "").strip()
        if not value:
            raise ConfigError(f"{name} is not set. {hint} (see .env.example).")
        return value

    model = required("EUREKA_EMBEDDING_MODEL", "Add the embedding model to .env")
    project = required("GOOGLE_CLOUD_PROJECT", "Add your Google Cloud project ID to .env")
    location = required("GOOGLE_CLOUD_LOCATION", "Add the Vertex AI region to .env")
    raw_batch = environ.get("EUREKA_EMBEDDING_BATCH_SIZE", "").strip() or "50"
    if not raw_batch.isdigit() or int(raw_batch) < 1:
        raise ConfigError(f"EUREKA_EMBEDDING_BATCH_SIZE must be a whole number >= 1, got {raw_batch!r}.")
    return EmbeddingSettings(model=model, project=project, location=location, batch_size=int(raw_batch))


@lru_cache(maxsize=1)
def get_embedding_settings() -> EmbeddingSettings:
    """Returns the embedding settings, loading `.env` on first use."""
    load_dotenv()
    return load_embedding_settings(os.environ)
