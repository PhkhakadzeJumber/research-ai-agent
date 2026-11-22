"""The single gateway to Claude.

Every call Eureka makes to Claude goes through this module (see CLAUDE.md),
so model choice, error handling, refusals and usage logging live in one place.

Two ways in:
- complete(): one direct call through the Anthropic SDK (simple, non-agent calls);
- run_structured(): one ADK agent call with a validated structured output, used by
  every agent (see the section "Agents through Google ADK" below).

What complete() does:
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
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Generic, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from src.config import RoleLlmSettings, Settings, get_role_llm_settings, get_settings

logger = logging.getLogger(__name__)

# Beta flag for the server-side fallback feature ("fallbacks": "default").
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LlmError(RuntimeError):
    """Base class for Eureka's LLM errors."""


class LlmAccountError(LlmError):
    """Anthropic refused the account itself: a usage or spending limit was reached, the credit ran out, the API
    key is invalid or lacks permission. Every further call would fail the same way, so a run must stop here and be
    resumed once access is back, never carry on "degraded" (the step 6a.2 live check concluded an evaluation with
    no evidence that way)."""


ACCOUNT_MARKERS = ("usage limit", "spend limit", "spending limit", "credit balance", "billing")


def is_account_error(error: Exception) -> bool:
    """True for refusals that concern the whole account, not this one request."""
    if isinstance(error, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return True
    return isinstance(error, anthropic.BadRequestError) and any(m in str(error).lower() for m in ACCOUNT_MARKERS)


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


# =====================================================================================
# Agents through Google ADK: run_structured()
# =====================================================================================
#
# Every agent with a structured output (Paper Reader, Novelty Investigator, ...) runs
# through run_structured(role, system=..., user_message=..., output_schema=...):
#
#   - The model is ADK's AnthropicLlm object for the role (never a bare "claude-..."
#     string, which ADK would route to Vertex AI). Model and effort come from
#     RoleLlmSettings (src/config.py).
#   - Adaptive thinking is always on: with an output schema and no thinking config,
#     ADK forces the hidden output tool, which current Claude models reject (400).
#   - The system prompt goes in as static_instruction: sent verbatim (no {placeholder}
#     processing) and in the cacheable position. With cache=True, ADK marks the stable
#     prefix so repeated calls are billed at the cache-read rate.
#   - The output is validated with Pydantic here (no output_key, so ADK doesn't
#     validate first). If it's invalid, ONE repair attempt is made by sending the
#     validation errors as a follow-up message in the same session: the conversation
#     is only appended to, never edited (required for Claude's reasoning blocks).
#   - Refusals raise LlmRefusal; output cut off at the token limit raises LlmTruncated.
#   - Every model call is returned as an LlmCallRecord (tokens, estimated cost,
#     latency, prompt version, stop reason). This module never touches the database
#     (it's the bottom layer); the caller stores the records (llm_calls table).

T = TypeVar("T", bound=BaseModel)

APP_NAME = "eureka"
REPAIR_ATTEMPTS = 1


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens. Cache writes (5-minute cache) cost 1.25x the input price."""

    input: Decimal
    output: Decimal
    cache_read: Decimal

    @property
    def cache_write(self) -> Decimal:
        return self.input * Decimal("1.25")


# Estimates from Anthropic's price list (checked 2026-11). Update when prices change;
# costs for models not listed are recorded as unknown (None), never guessed.
PRICES: dict[str, ModelPrice] = {
    "claude-opus-5-5": ModelPrice(Decimal("4"), Decimal("20"), Decimal("0.20")),
    "claude-sonnet-5-5": ModelPrice(Decimal("2"), Decimal("10"), Decimal("0.20")),
    "claude-haiku-5-5": ModelPrice(Decimal("0.10"), Decimal("0.50"), Decimal("0.01")),
}


@dataclass(frozen=True)
class LlmCallRecord:
    """One model call, ready to be stored in the llm_calls table (doc 10 §8)."""

    role: str
    model: str
    effort: str
    prompt_version: str
    attempt: int
    input_tokens: int
    """Uncached input tokens."""
    output_tokens: int
    """Visible output + thinking (both are billed as output)."""
    thinking_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    latency_ms: int
    stop_reason: str
    cost_usd: Decimal | None


class LlmTruncated(LlmError):
    """The output was cut off at the max_tokens limit, so it can't be used as a complete answer."""


class LlmOutputInvalid(LlmError):
    """The output didn't match the schema, even after the repair attempt."""

    def __init__(self, message: str, records: list[LlmCallRecord]) -> None:
        self.records = records
        super().__init__(message)


def estimate_cost(
    model: str, *, input_tokens: int, output_tokens: int, cache_read_tokens: int, cache_creation_tokens: int
) -> Decimal | None:
    """Estimated USD cost of one call, or None for models without a known price."""
    price = PRICES.get(model)
    if price is None:
        return None
    total = (
        input_tokens * price.input
        + output_tokens * price.output
        + cache_read_tokens * price.cache_read
        + cache_creation_tokens * price.cache_write
    ) / Decimal(1_000_000)
    return total.quantize(Decimal("0.000001"))


@dataclass(frozen=True)
class StructuredResult(Generic[T]):
    output: T
    records: list[LlmCallRecord]

    @property
    def cost_usd(self) -> Decimal | None:
        """Total estimated cost of all calls (None if any call's cost is unknown)."""
        costs = [record.cost_usd for record in self.records]
        return None if any(cost is None for cost in costs) else sum(costs, Decimal(0))


def anthropic_model(settings: RoleLlmSettings) -> Any:
    """ADK's Claude model object for a role (Anthropic API, not Vertex AI)."""
    from google.adk.models.anthropic_llm import AnthropicLlm

    return AnthropicLlm(model=settings.model, max_tokens=settings.max_tokens)


def structured_generation_config(settings: RoleLlmSettings) -> Any:
    """Adaptive thinking (required for structured output on current Claude models) + the role's effort."""
    from google.adk.models.anthropic_llm import AnthropicGenerateContentConfig
    from google.genai import types

    return AnthropicGenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=-1),  # -1 = adaptive
        effort=settings.effort,
        max_output_tokens=settings.max_tokens,
    )


def _record_from_event(
    event: Any, *, settings: RoleLlmSettings, prompt_version: str, attempt: int, latency_ms: int
) -> LlmCallRecord:
    usage = event.usage_metadata
    prompt = usage.prompt_token_count or 0
    cached = usage.cached_content_token_count or 0
    created = getattr(usage, "cache_creation_input_tokens", None) or 0
    thinking = usage.thoughts_token_count or 0
    visible = usage.candidates_token_count or 0
    uncached = max(prompt - cached - created, 0)
    model = getattr(event, "model_version", None) or settings.model
    stop = getattr(event.finish_reason, "name", None) or "UNKNOWN"
    return LlmCallRecord(
        role=settings.role, model=model, effort=settings.effort, prompt_version=prompt_version, attempt=attempt,
        input_tokens=uncached, output_tokens=visible + thinking, thinking_tokens=thinking,
        cache_read_tokens=cached, cache_creation_tokens=created, latency_ms=latency_ms, stop_reason=stop,
        cost_usd=estimate_cost(model, input_tokens=uncached, output_tokens=visible + thinking,
                               cache_read_tokens=cached, cache_creation_tokens=created),
    )


def _final_text(event: Any) -> str:
    parts = event.content.parts if event.content and event.content.parts else []
    return "".join(part.text for part in parts if part.text and not part.thought)


def _repair_message(error: Exception) -> str:
    if isinstance(error, ValidationError):
        problems = "; ".join(
            f"{'.'.join(str(p) for p in item['loc']) or '(root)'}: {item['msg']}" for item in error.errors()[:10]
        )
    else:
        problems = str(error)
    return (
        "Your previous answer could not be used because it doesn't match the required output structure: "
        f"{problems}. Answer again with the complete structured output, following the schema exactly."
    )


async def run_structured(
    role: str,
    *,
    system: str,
    prompt_version: str,
    user_message: str,
    output_schema: type[T],
    settings: RoleLlmSettings | None = None,
    model: Any | None = None,
    cache: bool = True,
) -> StructuredResult[T]:
    """Runs one ADK agent call for `role` and returns its validated output plus call records.

    Args:
        system: the system prompt (src/ai/prompts system_prompt(role).text).
        prompt_version: recorded with every call, e.g. 'shared_preamble@1+paper_reader@1'.
        user_message: the context packet (src/ai/context.py).
        output_schema: the Pydantic model the answer must match.
        settings, model: normally left empty; tests pass a fake ADK model here.
        cache: mark the stable prefix for prompt caching.

    Raises:
        LlmRefusal, LlmTruncated, LlmOutputInvalid (records attached), LlmError on API failures.
    """
    from google.adk.agents import LlmAgent
    from google.adk.agents.context_cache_config import ContextCacheConfig
    from google.adk.apps import App
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    settings = settings or get_role_llm_settings(role)
    agent = LlmAgent(
        name=f"{role}_agent",
        model=model or anthropic_model(settings),
        static_instruction=system,
        output_schema=output_schema,
        generate_content_config=structured_generation_config(settings),
    )
    app = App(name=APP_NAME, root_agent=agent,
              context_cache_config=ContextCacheConfig(min_tokens=0) if cache else None)
    sessions = InMemorySessionService()
    session = await sessions.create_session(app_name=APP_NAME, user_id="eureka", session_id=str(uuid.uuid4()))
    runner = Runner(app=app, session_service=sessions)

    records: list[LlmCallRecord] = []
    message = user_message
    last_error: Exception | None = None
    for attempt in range(1, REPAIR_ATTEMPTS + 2):
        started = time.perf_counter()
        final = None
        try:
            async for event in runner.run_async(
                user_id="eureka", session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part(text=message)]),
            ):
                if event.usage_metadata is not None and not event.partial:
                    record = _record_from_event(
                        event, settings=settings, prompt_version=prompt_version, attempt=attempt,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                    )
                    records.append(record)
                    logger.info(
                        "llm call role=%s model=%s effort=%s attempt=%d in=%d out=%d thinking=%d "
                        "cache_read=%d cache_write=%d ms=%d stop=%s cost=%s",
                        record.role, record.model, record.effort, record.attempt, record.input_tokens,
                        record.output_tokens, record.thinking_tokens, record.cache_read_tokens,
                        record.cache_creation_tokens, record.latency_ms, record.stop_reason, record.cost_usd,
                    )
                if event.is_final_response():
                    final = event
        except anthropic.APIError as error:
            if is_account_error(error):
                raise LlmAccountError(f"{role}: Anthropic refused the account ({type(error).__name__}): {error}") \
                    from error
            raise LlmError(f"{role}: API error ({type(error).__name__}): {error}") from error

        if final is None:
            raise LlmError(f"{role}: the model returned no final answer")
        stop = getattr(final.finish_reason, "name", None)
        if stop == "SAFETY":
            raise LlmRefusal(category=None, explanation=getattr(final, "error_message", None))
        if stop == "MAX_TOKENS":
            raise LlmTruncated(f"{role}: output cut off at the max_tokens limit ({settings.max_tokens})")

        try:
            return StructuredResult(output=output_schema.model_validate_json(_final_text(final)), records=records)
        except ValidationError as error:
            last_error = error
            logger.warning("%s: output failed validation (attempt %d): %s", role, attempt, str(error)[:300])
            message = _repair_message(error)

    raise LlmOutputInvalid(
        f"{role}: output didn't match {output_schema.__name__} after {REPAIR_ATTEMPTS + 1} attempts: {last_error}",
        records,
    )
