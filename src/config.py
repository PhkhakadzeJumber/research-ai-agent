"""Eureka settings, read once from environment variables.

In development the variables come from the `.env` file in the repo root
(see `.env.example` for the names). In the cloud they come from the
environment directly (Secret Manager). Secrets such as ANTHROPIC_API_KEY are
NOT stored here: the Anthropic SDK reads the key from the environment itself,
so it never passes through our code or our logs.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

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
