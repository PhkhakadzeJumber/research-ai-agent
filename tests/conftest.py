"""Shared pytest setup.

Async tests use the anyio pytest plugin (installed with anyio): mark a test or module
with `pytest.mark.anyio` and write it as `async def`. It runs on asyncio, like the app.
"""

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"
