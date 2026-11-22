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
- RoleLlmSettings (get_role_llm_settings): model and effort per agent role.
- ApiSettings (get_api_settings): the HTTP API (which frontend origins may call it).
- BillingSettings (get_billing_settings): plans and credit prices (docs/18).
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


# Default reasoning effort per agent role (doc 08 §2). Effort controls how hard Claude
# thinks: more effort = better on hard judgments, more tokens and time.
ROLE_DEFAULT_EFFORT = {
    "idea_analyst": "medium",
    "scout": "high",
    "paper_reader": "low",
    "relevance_screening": "low",
    "opportunity_mining": "low",
    "novelty": "high",
    "skeptic": "high",
    "feasibility_quick": "medium",
    "value_feasibility": "high",
    "proposal": "medium",
}


# Default output token limit per role, where EUREKA_LLM_MAX_TOKENS is too small. The Scout writes
# 8-12 candidates with facets in one answer. Calls aren't streamed, and the Anthropic SDK refuses
# non-streaming calls above about 21k output tokens, so 20k is the ceiling here.
ROLE_DEFAULT_MAX_TOKENS = {
    "scout": 20000,
    "proposal": 20000,  # the research package's written sections (step 7)
}


@dataclass(frozen=True)
class RoleLlmSettings:
    role: str
    model: str
    effort: str
    max_tokens: int


def load_role_llm_settings(role: str, environ: Mapping[str, str]) -> RoleLlmSettings:
    """The model and effort for one agent role.

    Defaults: EUREKA_LLM_MODEL, the role's default effort (ROLE_DEFAULT_EFFORT, else
    EUREKA_LLM_EFFORT) and EUREKA_LLM_MAX_TOKENS. Per-role overrides, e.g. a cheaper
    Paper Reader: EUREKA_LLM_MODEL_PAPER_READER, EUREKA_LLM_EFFORT_PAPER_READER. The output limit:
    EUREKA_LLM_MAX_TOKENS_<ROLE>, else ROLE_DEFAULT_MAX_TOKENS, else EUREKA_LLM_MAX_TOKENS.
    """
    base = load_settings(environ)
    suffix = role.upper()
    model = environ.get(f"EUREKA_LLM_MODEL_{suffix}", "").strip() or base.llm_model
    effort = environ.get(f"EUREKA_LLM_EFFORT_{suffix}", "").strip() or ROLE_DEFAULT_EFFORT.get(role, base.llm_effort)
    if effort not in EFFORT_LEVELS:
        raise ConfigError(f"EUREKA_LLM_EFFORT_{suffix} must be one of {', '.join(EFFORT_LEVELS)}, got {effort!r}.")
    raw_max = environ.get(f"EUREKA_LLM_MAX_TOKENS_{suffix}", "").strip()
    if raw_max:
        if not raw_max.isdigit() or int(raw_max) <= 0:
            raise ConfigError(f"EUREKA_LLM_MAX_TOKENS_{suffix} must be a whole number above 0, got {raw_max!r}.")
        max_tokens = int(raw_max)
    else:
        max_tokens = ROLE_DEFAULT_MAX_TOKENS.get(role, base.llm_max_tokens)
    return RoleLlmSettings(role=role, model=model, effort=effort, max_tokens=max_tokens)


def get_role_llm_settings(role: str) -> RoleLlmSettings:
    """The model and effort for one agent role, loading `.env` on first use."""
    get_settings()  # loads .env and validates the base settings
    return load_role_llm_settings(role, os.environ)


DEFAULT_FRONTEND_ORIGIN = "http://localhost:3000"  # the Next.js dev server


DEFAULT_MAX_BUDGET_USD = 20.0


@dataclass(frozen=True)
class ApiSettings:
    frontend_origins: tuple[str, ...]
    """Origins the browser may call the API from (CORS), from FRONTEND_ORIGIN: one URL, or several separated by
    commas. Default: the local Next.js dev server."""

    max_budget_usd: float = DEFAULT_MAX_BUDGET_USD
    """The most one investigation may be given to spend (EUREKA_MAX_BUDGET_USD), so no request can ask for more."""

    cookie_secure: bool = True
    """The login cookie is sent over HTTPS only (EUREKA_COOKIE_SECURE). Browsers accept it on http://localhost too;
    set false only for a local browser that doesn't."""


def load_api_settings(environ: Mapping[str, str]) -> ApiSettings:
    raw = environ.get("FRONTEND_ORIGIN", "").strip() or DEFAULT_FRONTEND_ORIGIN
    origins = tuple(origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip())
    bad = [origin for origin in origins if not origin.startswith(("http://", "https://"))]
    if bad or not origins:
        raise ConfigError(f"FRONTEND_ORIGIN must be http(s) URLs separated by commas, e.g. {DEFAULT_FRONTEND_ORIGIN}; "
                          f"got {', '.join(bad) or 'nothing'}.")
    raw_max = environ.get("EUREKA_MAX_BUDGET_USD", "").strip() or str(DEFAULT_MAX_BUDGET_USD)
    try:
        max_budget = float(raw_max)
    except ValueError:
        max_budget = 0.0
    if max_budget <= 0:
        raise ConfigError(f"EUREKA_MAX_BUDGET_USD must be a number of dollars above 0, e.g. 20; got {raw_max!r}.")
    raw_secure = environ.get("EUREKA_COOKIE_SECURE", "").strip().lower() or "true"
    if raw_secure not in ("true", "false"):
        raise ConfigError(f"EUREKA_COOKIE_SECURE must be true or false; got {raw_secure!r}.")
    return ApiSettings(frontend_origins=origins, max_budget_usd=max_budget, cookie_secure=raw_secure == "true")


@lru_cache(maxsize=1)
def get_api_settings() -> ApiSettings:
    """Returns the API settings, loading `.env` on first use."""
    load_dotenv()
    return load_api_settings(os.environ)


# --- plans and credits (docs/18; step P2) ---------------------------------------------------------------------

@dataclass(frozen=True)
class BillingSettings:
    """What credits are worth and what each action costs. All optional: the defaults are docs/18's numbers."""

    credit_usd: float = 0.04
    """Our AI cost one credit pays for (EUREKA_CREDIT_USD). Runs are charged ceil(cost / this), at most the price."""
    price_evaluate: int = 25
    """Credits for "Evaluate my idea" (EUREKA_PRICE_EVALUATE)."""
    price_discover: int = 60
    """Credits for "Find me an idea" (EUREKA_PRICE_DISCOVER)."""
    price_package: int = 5
    """Credits for a research package (EUREKA_PRICE_PACKAGE)."""
    pro_monthly_credits: int = 100
    """Credits a Pro account gets on the 1st of each month (EUREKA_PRO_MONTHLY_CREDITS); they expire at month end."""
    free_evaluations: int = 1
    """Full evaluations a free account may start each calendar month (EUREKA_FREE_EVALUATIONS)."""
    free_monthly_budget_usd: float = 20.0
    """The most all free accounts together may cost in a month (EUREKA_FREE_MONTHLY_BUDGET_USD); when reached, free
    evaluations wait until next month."""
    run_cap_factor: float = 1.5
    """A run may cost up to this times its price in dollars before it stops (EUREKA_RUN_CAP_FACTOR); we absorb the
    overrun above the price."""


def load_billing_settings(environ: Mapping[str, str]) -> BillingSettings:
    defaults = BillingSettings()
    values: dict[str, float | int] = {}
    for name, attribute, kind in (
            ("EUREKA_CREDIT_USD", "credit_usd", float), ("EUREKA_PRICE_EVALUATE", "price_evaluate", int),
            ("EUREKA_PRICE_DISCOVER", "price_discover", int), ("EUREKA_PRICE_PACKAGE", "price_package", int),
            ("EUREKA_PRO_MONTHLY_CREDITS", "pro_monthly_credits", int),
            ("EUREKA_FREE_EVALUATIONS", "free_evaluations", int),
            ("EUREKA_FREE_MONTHLY_BUDGET_USD", "free_monthly_budget_usd", float),
            ("EUREKA_RUN_CAP_FACTOR", "run_cap_factor", float)):
        raw = environ.get(name, "").strip()
        if not raw:
            continue
        try:
            value = kind(raw)
        except ValueError:
            value = -1
        floor = 0 if attribute in ("free_evaluations", "free_monthly_budget_usd") else 1 if kind is int else 0
        if value < floor or (attribute in ("credit_usd", "run_cap_factor") and value <= 0):
            raise ConfigError(f"{name} must be a {'whole ' if kind is int else ''}number"
                              f"{' of at least ' + str(floor) if floor else ' above 0'}; got {raw!r}.")
        values[attribute] = value
    if values.get("run_cap_factor", defaults.run_cap_factor) < 1:
        raise ConfigError("EUREKA_RUN_CAP_FACTOR must be at least 1 (a run must be able to cost its price).")
    return BillingSettings(**values)


@lru_cache(maxsize=1)
def get_billing_settings() -> BillingSettings:
    """Returns the billing settings, loading `.env` on first use."""
    load_dotenv()
    return load_billing_settings(os.environ)
