"""Shared pytest setup.

Async tests use the anyio pytest plugin (installed with anyio): mark a test or module
with `pytest.mark.anyio` and write it as `async def`. It runs on asyncio, like the app.

Safety net: outside tests marked `live`, building a real Claude client is refused. A test
that forgets to pass a fake model (tests/fakes.py) then fails loudly, instead of quietly
calling the paid API with the key from .env. (Added in step 4.5, after exactly that
happened: a screening step without a fake model made real, if tiny, Haiku calls.)
"""

import pytest

import src.llm


@pytest.fixture
def anyio_backend():
    return "asyncio"


class RealClaudeRefused(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def no_real_claude(request, monkeypatch):
    if request.node.get_closest_marker("live"):
        return

    def refuse(*args, **kwargs):
        raise RealClaudeRefused(
            "A default test tried to call the real Claude API. Pass a fake model (tests/fakes.py), "
            "or mark the test `live` if it is meant to cost money."
        )

    monkeypatch.setattr(src.llm, "anthropic_model", refuse)
    monkeypatch.setattr(src.llm, "get_client", refuse)
