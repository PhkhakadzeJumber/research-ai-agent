"""Tests for src/retrieval/http.py with fake HTTP responses: no network, no real waiting."""

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from src.retrieval.http import (
    USER_AGENT,
    RateLimiter,
    RetryPolicy,
    SourceHttpClient,
    SourceRequestError,
    parse_retry_after,
)

NO_JITTER = RetryPolicy(max_attempts=4, base_delay=1.0, max_delay=30.0, jitter=False)


class FakeSleep:
    """Records requested waits instead of actually waiting."""

    def __init__(self):
        self.delays = []

    async def __call__(self, seconds):
        self.delays.append(seconds)


def make_client(responses, *, retry=NO_JITTER, sleep=None):
    """A client whose transport replies with `responses` in order (a Response or an exception each)."""
    calls = []
    queue = list(responses)

    def handler(request):
        calls.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client = SourceHttpClient(
        "testsource",
        base_url="https://api.example.org",
        retry=retry,
        transport=httpx.MockTransport(handler),
        sleep=sleep or FakeSleep(),
    )
    return client, calls


def run(coro):
    return asyncio.run(coro)


async def get_json_and_close(client, *args, **kwargs):
    try:
        return await client.get_json(*args, **kwargs)
    finally:
        await client.aclose()


# --- success ----------------------------------------------------------------

def test_get_json_returns_parsed_body_and_sends_params_and_user_agent():
    client, calls = make_client([httpx.Response(200, json={"results": [1, 2]})])
    body = run(get_json_and_close(client, "/works", params={"search": "rag"}))

    assert body == {"results": [1, 2]}
    assert calls[0].url.path == "/works"
    assert calls[0].url.params["search"] == "rag"
    assert calls[0].headers["User-Agent"] == USER_AGENT


def test_get_text_returns_body_text():
    client, _ = make_client([httpx.Response(200, text="<feed/>")])

    async def go():
        try:
            return await client.get_text("/query")
        finally:
            await client.aclose()

    assert run(go()) == "<feed/>"


# --- retries ------------------------------------------------------------------

def test_retries_server_errors_with_exponential_backoff():
    sleep = FakeSleep()
    client, calls = make_client(
        [httpx.Response(503), httpx.Response(502), httpx.Response(200, json={"ok": True})], sleep=sleep
    )
    assert run(get_json_and_close(client, "/x")) == {"ok": True}
    assert len(calls) == 3
    assert sleep.delays == [1.0, 2.0]


def test_rate_limit_honors_retry_after_seconds():
    sleep = FakeSleep()
    client, _ = make_client(
        [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json={})], sleep=sleep
    )
    run(get_json_and_close(client, "/x"))
    assert sleep.delays == [7.0]


def test_retry_after_is_capped_at_max_delay():
    sleep = FakeSleep()
    client, _ = make_client(
        [httpx.Response(429, headers={"Retry-After": "3600"}), httpx.Response(200, json={})], sleep=sleep
    )
    run(get_json_and_close(client, "/x"))
    assert sleep.delays == [30.0]


def test_timeouts_are_retried():
    sleep = FakeSleep()
    client, calls = make_client(
        [httpx.ReadTimeout("slow"), httpx.Response(200, json={"ok": True})], sleep=sleep
    )
    assert run(get_json_and_close(client, "/x")) == {"ok": True}
    assert len(calls) == 2


def test_gives_up_after_max_attempts():
    client, calls = make_client([httpx.Response(503)] * 4)
    with pytest.raises(SourceRequestError) as error:
        run(get_json_and_close(client, "/x"))

    assert len(calls) == 4
    assert error.value.status == 503
    assert error.value.attempts == 4
    assert error.value.source == "testsource"
    assert "gave up after 4 attempts" in str(error.value)


def test_connection_errors_end_in_a_source_error():
    client, _ = make_client([httpx.ConnectError("down")] * 4)
    with pytest.raises(SourceRequestError, match="connection error"):
        run(get_json_and_close(client, "/x"))


def test_client_errors_are_not_retried():
    client, calls = make_client([httpx.Response(404)])
    with pytest.raises(SourceRequestError, match="HTTP 404") as error:
        run(get_json_and_close(client, "/x"))
    assert len(calls) == 1
    assert error.value.status == 404


def test_invalid_json_is_a_source_error():
    client, _ = make_client([httpx.Response(200, text="<html>not json</html>")])
    with pytest.raises(SourceRequestError, match="not valid JSON"):
        run(get_json_and_close(client, "/x"))


# --- backoff, Retry-After parsing, rate limiting ------------------------------

def test_backoff_doubles_and_is_capped():
    policy = RetryPolicy(base_delay=1.0, max_delay=5.0, jitter=False)
    assert [policy.backoff(n) for n in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 5.0]


def test_backoff_jitter_stays_within_bounds():
    policy = RetryPolicy(base_delay=2.0, max_delay=30.0, jitter=True)
    for _ in range(50):
        assert 2.0 <= policy.backoff(1) <= 3.0


def test_parse_retry_after():
    now = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    in_ten_seconds = format_datetime(now + timedelta(seconds=10), usegmt=True)

    assert parse_retry_after("12") == 12.0
    assert parse_retry_after(in_ten_seconds, now=now) == pytest.approx(10.0)
    assert parse_retry_after(format_datetime(now - timedelta(seconds=5), usegmt=True), now=now) == 0.0
    assert parse_retry_after("soon") is None
    assert parse_retry_after(None) is None


def test_rate_limiter_spaces_requests():
    fake_time = [100.0]
    waits = []

    async def sleep(seconds):
        waits.append(seconds)
        fake_time[0] += seconds

    limiter = RateLimiter(3.0, clock=lambda: fake_time[0], sleep=sleep)

    async def go():
        await limiter.acquire()   # first request: no wait
        fake_time[0] += 1.0       # 1 second passes
        await limiter.acquire()   # must wait the remaining 2 seconds
        fake_time[0] += 5.0       # long pause
        await limiter.acquire()   # no wait needed

    run(go())
    assert waits == [2.0]
