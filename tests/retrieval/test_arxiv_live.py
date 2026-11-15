"""Live check against the real arXiv API. Excluded from the default run; run with: pytest -m live

arXiv is free and needs no key; the adapter waits 3 seconds between requests as arXiv asks.
"""

import asyncio

import pytest

from src.retrieval.records import SearchRequest
from src.retrieval.sources.arxiv import ArxivSource

pytestmark = pytest.mark.live


def test_real_search_returns_valid_papers():
    async def go():
        async with ArxivSource() as source:
            return await source.search(SearchRequest(query="retrieval augmented generation hallucination", limit=5))

    papers = asyncio.run(go())
    assert papers, "arXiv returned no papers"
    assert all(paper.ids.arxiv_id and paper.title and paper.tier == 2 for paper in papers)
