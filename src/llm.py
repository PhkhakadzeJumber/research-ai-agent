"""The single gateway to Claude.

Every call Eureka makes to Claude goes through this module (see CLAUDE.md),
so model choice, error handling, refusals and usage logging live in one place.
Later, the ADK model objects for each agent role will be built here too.

What a call does:
- uses the model ID and default effort from settings (EUREKA_LLM_* env vars);
- enables Anthropic's server-side fallback ("default"), so if the model
  declines a request for policy reasons, a fallback model can answer instead;
- raises LlmRefusal if the request is still declined, so callers can't
  mistake a refusal for an answer;
- returns the text together with token usage, latency and the request ID,
  and logs them (never the prompt contents, never the API key).

Network errors, rate limits (429) and server errors (5xx) are retried by the
Anthropic SDK itself (2 retries by default) before an exception reaches us.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import anthropic

from src.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Beta flag for the server-side fallback feature ("fallbacks": "default").
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LlmError(RuntimeError):
    """Base class for Eureka's LLM errors."""


class LlmRefusal(LlmError):
    """Claude (and any fallback model) declined the request."""

    def __init__(self, category: str | None, explanation: str | None) -> None:
        self.category = category
        self.explanation = explanation
        super().__init__(f"Claude declined the request (category: {category or 'unknown'}).")


@dataclass(frozen=True)
class LlmResult:
    """The outcome of one Claude call."""

    text: str
    """The model's answer (all text blocks joined)."""

    model: str
    """The model that actually answered (can differ from the requested one after a fallback)."""

    stop_reason: str | None
    """Why the model stopped: end_turn, max_tokens, ..."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    latency_ms: int
    request_id: str | None
    """Anthropic's request ID, useful when reporting a problem."""

    @property
    def truncated(self) -> bool:
        """True if the answer was cut off by the max_tokens limit."""
        return self.stop_reason == "max_tokens"


_client: anthropic.AsyncAnthropic | None = None


def get_client() -> anthropic.AsyncAnthropic:
    """Returns the shared Anthropic client, creating it on first use.

    get_settings() runs first because it loads `.env` into the environment,
    where the SDK finds ANTHROPIC_API_KEY.
    """
    global _client
    if _client is None:
        get_settings()
        _client = anthropic.AsyncAnthropic()
    return _client


async def complete(
    prompt: str,
    *,
    system: str | None = None,
    role: str = "default",
    effort: str | None = None,
    max_tokens: int | None = None,
    settings: Settings | None = None,
    client: Any | None = None,
) -> LlmResult:
    """Sends one prompt to Claude and returns its answer.

    Args:
        prompt: The user message.
        system: Optional system prompt (the agent's instructions).
        role: Which part of Eureka is calling (e.g. "paper_reader"). Used in logs now,
            and later to pick a model and effort per role.
        effort: low | medium | high | xhigh | max. Defaults to EUREKA_LLM_EFFORT.
        max_tokens: Output limit. Defaults to EUREKA_LLM_MAX_TOKENS.
        settings, client: Normally left empty. Tests pass fakes here so no
            real API call is made.

    Raises:
        LlmRefusal: the request was declined.
        anthropic.APIError: the API call failed after the SDK's retries.
    """
    settings = settings or get_settings()
    client = client or get_client()

    request: dict[str, Any] = {
        "model": settings.llm_model,
        "max_tokens": max_tokens or settings.llm_max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {"effort": effort or settings.llm_effort},
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
    }
    if system:
        request["system"] = system

    started = time.perf_counter()
    response = await client.beta.messages.create(**request)
    latency_ms = int((time.perf_counter() - started) * 1000)

    request_id = getattr(response, "_request_id", None)

    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None)
        explanation = getattr(details, "explanation", None)
        logger.warning(
            "llm refusal role=%s model=%s category=%s request_id=%s",
            role, response.model, category, request_id,
        )
        raise LlmRefusal(category, explanation)

    usage = response.usage
    result = LlmResult(
        text="".join(block.text for block in response.content if block.type == "text"),
        model=response.model,
        stop_reason=response.stop_reason,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", None) or 0,
        latency_ms=latency_ms,
        request_id=request_id,
    )

    logger.info(
        "llm call role=%s model=%s in=%d out=%d cached=%d ms=%d stop=%s request_id=%s",
        role, result.model, result.input_tokens, result.output_tokens,
        result.cache_read_tokens, result.latency_ms, result.stop_reason, result.request_id,
    )
    if result.truncated:
        logger.warning("llm output truncated at max_tokens role=%s request_id=%s", role, request_id)

    return result
