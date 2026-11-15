"""Live check against the real OpenAlex API. Excluded from the default run; run with: pytest -m live

Uses a small part of the OpenAlex daily budget (about $0.001 per search).
"""

import asyncio

import pytest

from src.retrieval.records import SearchRequest
from src.retrieval.sources.openalex import OpenAlexSource

pytestmark = pytest.mark.live


def test_real_search_returns_valid_papers():
    async def go():
        async with OpenAlexSource.from_settings() as source:
            return await source.search(SearchRequest(query="retrieval augmented generation", limit=5))

    papers = asyncio.run(go())
    assert papers, "OpenAlex returned no papers"
    assert all(paper.ids.openalex_id and paper.title for paper in papers)
