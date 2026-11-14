"""Tests for src/llm.py with a fake Anthropic client: no network, no cost."""

import asyncio
from types import SimpleNamespace

import pytest

from src.config import Settings
from src.llm import FALLBACK_BETA, LlmRefusal, complete

SETTINGS = Settings(llm_model="model-x", llm_effort="medium", llm_max_tokens=1000)


def make_response(*, text="Hello", stop_reason="end_turn", stop_details=None, model="model-x"):
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        model=model,
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=SimpleNamespace(input_tokens=12, output_tokens=3, cache_read_input_tokens=None),
        _request_id="req_test",
    )


class FakeClient:
    """Records the request and returns a prepared response, like client.beta.messages.create."""

    def __init__(self, response):
        self.requests = []
        self._response = response
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **request):
        self.requests.append(request)
        return self._response


def run(coro):
    return asyncio.run(coro)


def test_returns_text_and_usage():
    client = FakeClient(make_response(text="OK"))
    result = run(complete("Say OK", settings=SETTINGS, client=client))

    assert result.text == "OK"
    assert (result.input_tokens, result.output_tokens, result.cache_read_tokens) == (12, 3, 0)
    assert result.request_id == "req_test"
    assert result.truncated is False


def test_request_uses_settings_and_fallback():
    client = FakeClient(make_response())
    run(complete("Hi", settings=SETTINGS, client=client))

    request = client.requests[0]
    assert request["model"] == "model-x"
    assert request["max_tokens"] == 1000
    assert request["output_config"] == {"effort": "medium"}
    assert request["fallbacks"] == "default"
    assert request["betas"] == [FALLBACK_BETA]
    assert request["messages"] == [{"role": "user", "content": "Hi"}]
    assert "system" not in request


def test_arguments_override_settings():
    client = FakeClient(make_response())
    run(complete("Hi", system="Be brief.", effort="high", max_tokens=50, settings=SETTINGS, client=client))

    request = client.requests[0]
    assert request["system"] == "Be brief."
    assert request["output_config"] == {"effort": "high"}
    assert request["max_tokens"] == 50


def test_refusal_raises():
    details = SimpleNamespace(category="cyber", explanation="Declined.")
    client = FakeClient(make_response(text="", stop_reason="refusal", stop_details=details))

    with pytest.raises(LlmRefusal) as error:
        run(complete("Hi", settings=SETTINGS, client=client))
    assert error.value.category == "cyber"


def test_truncated_output_is_flagged():
    client = FakeClient(make_response(stop_reason="max_tokens"))
    result = run(complete("Hi", settings=SETTINGS, client=client))
    assert result.truncated is True


def test_reports_model_that_answered():
    client = FakeClient(make_response(model="fallback-model"))
    result = run(complete("Hi", settings=SETTINGS, client=client))
    assert result.model == "fallback-model"
