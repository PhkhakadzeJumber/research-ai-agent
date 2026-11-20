"""The HTTP foundation every literature source adapter uses.

One SourceHttpClient per source (OpenAlex, arXiv, ...) gives:
- timeouts, so a slow API can't hang a search;
- retries with exponential backoff on rate limits (429), server errors (5xx),
  timeouts and connection errors, honoring the API's Retry-After header;
- a rate limiter, so requests to one source are spaced out (arXiv asks for
  about one request every 3 seconds) instead of getting us blocked;
- one error type, SourceRequestError, for every kind of failure. The search
  layer turns it into a SourceReport (doc 07 §5): failures are reported, not hidden;
- an optional response cache (src/retrieval/cache.py). Only responses that parse
  correctly are cached, so an error page is never "remembered".

Request parameters are never logged, because some sources take a contact email or a key there.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar

import httpx

from src.retrieval.cache import ResponseCache

logger = logging.getLogger(__name__)

T = TypeVar("T")

USER_AGENT = "eureka-research-agent/0.1 (+https://github.com/PhkhakadzeJumber/research-ai-agent)"

# Status codes worth retrying: the request was fine, the server just couldn't serve it right now.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class SourceRequestError(Exception):
    """A request to a literature source failed for good (after any retries)."""

    def __init__(self, source: str, message: str, *, status: int | None = None, attempts: int = 1) -> None:
        self.source = source
        self.message = message
        """What went wrong, without the source name (used in SourceReport.error)."""
        self.status = status
        self.attempts = attempts
        super().__init__(f"{source}: {message}")


@dataclass(frozen=True)
class RetryPolicy:
    """How persistent to be when a request fails in a retryable way."""

    max_attempts: int = 4
    """Total tries, including the first one."""

    base_delay: float = 1.0
    """Seconds before the first retry; doubles on each further retry."""

    max_delay: float = 30.0
    """Upper limit for any single wait, including a server's Retry-After."""

    jitter: bool = True
    """Adds a little randomness to the wait, so parallel requests don't retry in lockstep."""

    rate_limited_delay: float = 0.0
    """Minimum wait after HTTP 429 when the server gives no Retry-After. Some sources (arXiv)
    throttle for longer than the normal backoff, so retrying after 1-4 s just fails again."""

    def backoff(self, attempt: int) -> float:
        """Wait before retry number `attempt` (1 = first retry)."""
        delay = self.base_delay * 2 ** (attempt - 1)
        if self.jitter:
            delay += random.uniform(0, self.base_delay / 2)
        return min(delay, self.max_delay)


class RateLimiter:
    """Ensures at least `min_interval` seconds between the starts of consecutive requests."""

    def __init__(self, min_interval: float, *, clock: Clock = time.monotonic, sleep: Sleep = asyncio.sleep) -> None:
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._last_start: float | None = None

    async def acquire(self) -> None:
        async with self._lock:
            if self._last_start is not None:
                wait = self._last_start + self.min_interval - self._clock()
                if wait > 0:
                    await self._sleep(wait)
            self._last_start = self._clock()


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        raise ValueError("response was not valid JSON") from None


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Reads a Retry-After header: either seconds ('7') or an HTTP date. Returns seconds or None."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    seconds = (when - (now or datetime.now(timezone.utc))).total_seconds()
    return max(seconds, 0.0)


class SourceHttpClient:
    """An HTTP client for one literature source. Use one instance per source and reuse it."""

    def __init__(
        self,
        source: str,
        *,
        base_url: str,
        min_interval: float = 0.0,
        timeout: float = 20.0,
        retry: RetryPolicy | None = None,
        headers: Mapping[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
        cache: ResponseCache | None = None,
    ) -> None:
        """
        Args:
            source: Short name used in errors and logs, e.g. "openalex".
            base_url: The API's base URL; request paths are relative to it.
            min_interval: Minimum seconds between requests to this source.
            timeout: Seconds before a single request attempt gives up.
            retry: Retry behavior (defaults to RetryPolicy()).
            headers: Extra headers; a descriptive User-Agent is always sent.
            transport, sleep, clock: Normally left empty. Tests pass fakes here
                so no network or real waiting is involved.
            cache: Optional response cache used by get_json / get_parsed.
        """
        self.source = source
        self.retry = retry or RetryPolicy()
        self._cache = cache
        self._sleep = sleep
        self._limiter = RateLimiter(min_interval, clock=clock, sleep=sleep)
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout),
            headers={"User-Agent": USER_AGENT, **(headers or {})},
            transport=transport,
            follow_redirects=True,
        )

    async def __aenter__(self) -> SourceHttpClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_parsed(
        self, path: str, params: Mapping[str, Any] | None, parse: Callable[[str], T]
    ) -> T:
        """GET an endpoint and parse its text with `parse`, using the cache if there is one.

        `parse` must raise SourceRequestError (or ValueError) for unusable responses.
        A cached response that no longer parses is ignored and fetched again; a fresh
        response is cached only after it parsed successfully.
        """
        params = dict(params or {})
        if self._cache is not None:
            cached = await self._cache.get(self.source, path, params)
            if cached is not None:
                try:
                    return parse(cached)
                except (SourceRequestError, ValueError):
                    logger.warning("cached response for source=%s path=%s no longer parses; refetching",
                                   self.source, path)

        response = await self._get(path, params)
        try:
            result = parse(response.text)
        except ValueError as error:
            raise SourceRequestError(self.source, str(error), status=response.status_code) from None
        if self._cache is not None:
            await self._cache.put(self.source, path, params, response.text)
        return result

    async def get_json(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        """GET a JSON endpoint (cached if a cache is set). Raises SourceRequestError on failure or invalid JSON."""
        return await self.get_parsed(path, params, _parse_json)

    async def get_text(self, path: str, params: Mapping[str, Any] | None = None) -> str:
        """GET an endpoint's raw text, never cached (use get_parsed for cached, validated responses)."""
        response = await self._get(path, params)
        return response.text

    async def _get(self, path: str, params: Mapping[str, Any] | None) -> httpx.Response:
        attempts = self.retry.max_attempts
        for attempt in range(1, attempts + 1):
            await self._limiter.acquire()
            try:
                response = await self._client.get(path, params=params)
            except httpx.TimeoutException:
                problem, status, retry_after = "request timed out", None, None
            except httpx.TransportError as error:
                problem, status, retry_after = f"connection error ({type(error).__name__})", None, None
            else:
                if response.is_success:
                    return response
                status = response.status_code
                if status not in RETRYABLE_STATUS:
                    raise SourceRequestError(self.source, f"HTTP {status}", status=status, attempts=attempt)
                problem = f"HTTP {status}"
                retry_after = parse_retry_after(response.headers.get("Retry-After"))

            if attempt == attempts:
                raise SourceRequestError(
                    self.source, f"{problem} (gave up after {attempt} attempts)", status=status, attempts=attempt
                )

            delay = self.retry.backoff(attempt) if retry_after is None else min(retry_after, self.retry.max_delay)
            if status == 429 and retry_after is None:
                delay = max(delay, min(self.retry.rate_limited_delay, self.retry.max_delay))
            logger.warning(
                "source request retry source=%s path=%s problem=%s attempt=%d/%d wait=%.1fs",
                self.source, path, problem, attempt, attempts, delay,
            )
            await self._sleep(delay)

        raise AssertionError("unreachable")  # the loop always returns or raises
