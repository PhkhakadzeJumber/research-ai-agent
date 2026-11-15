"""Live check: one real search across OpenAlex and arXiv together. Run with: pytest -m live

Uses a small part of the OpenAlex daily budget (about $0.001); arXiv is free.
"""

import asyncio

import pytest

from src.retrieval.records import SearchRequest
from src.retrieval.search import LiteratureSearch

pytestmark = pytest.mark.live


def test_real_multi_source_search():
    async def go():
        async with LiteratureSearch.from_settings() as search:
            return await search.search(SearchRequest(query="retrieval augmented generation hallucination", limit=10))

    result = asyncio.run(go())
    assert not result.failed_sources, f"failed sources: {result.reports}"
    assert result.papers
    assert len(result.papers) <= result.retrieved_count
