"""Live check of run_structured() with real Claude through ADK. Excluded by default.

Run with:  pytest -m live tests/test_llm_structured_live.py -s

It reads the Ragas abstract (from the arXiv fixture) with the real Paper Reader prompt,
output schema and context packet, then verifies Claude's quotes with the real verifier.
The same call runs twice, to see whether prompt caching applies on the second call.
Cost: a few cents (two short Paper Reader calls).
"""

import asyncio
from pathlib import Path

import pytest

from src.ai.context import PaperForReading, render_paper
from src.ai.evidence.verifier import verify_analysis
from src.ai.prompts import system_prompt
from src.ai.schemas.paper_analysis import PaperAnalysisOutput
from src.llm import run_structured
from src.retrieval.sources.arxiv import parse_feed

pytestmark = pytest.mark.live

FEED = Path(__file__).parent / "fixtures" / "arxiv" / "search.xml"


def test_real_structured_paper_reading():
    ragas = next(p for p in parse_feed(FEED.read_text(encoding="utf-8")) if p.ids.arxiv_id == "2309.15217")
    packet = render_paper(PaperForReading(paper_id="p_ragas", title=ragas.title, abstract=ragas.abstract,
                                          year=ragas.year, venue=ragas.venue, authors=ragas.authors))
    system = system_prompt("paper_reader")

    async def read_twice():
        results = []
        for _ in range(2):
            results.append(await run_structured(
                "paper_reader", system=system.text, prompt_version=system.version_id,
                user_message=packet, output_schema=PaperAnalysisOutput,
            ))
        return results

    first, second = asyncio.run(read_twice())

    for label, result in (("first", first), ("second", second)):
        record = result.records[-1]
        print(f"\n{label} call: attempts={len(result.records)} in={record.input_tokens} out={record.output_tokens} "
              f"thinking={record.thinking_tokens} cache_read={record.cache_read_tokens} "
              f"cache_write={record.cache_creation_tokens} ms={record.latency_ms} cost=${result.cost_usd}")

    report = verify_analysis(first.output, abstract=ragas.abstract)
    print(f"output: {first.output.model_dump_json(indent=1)}")
    print(f"quotes verified: {len(report.verified_quotes)}/{len(report.quotes)}; "
          f"missing names: {[c.name for c in report.missing_names]}")
    for check in report.unverified_quotes:
        print(f"  UNVERIFIED {check.location}: {check.reason} | {check.text!r}")

    assert isinstance(first.output, PaperAnalysisOutput)
    assert first.output.contribution_type != "unknown"
    assert report.quotes, "expected at least one quoted statement"
    assert report.verification_rate >= 0.8
