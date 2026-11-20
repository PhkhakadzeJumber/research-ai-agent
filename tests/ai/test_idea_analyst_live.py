"""Live check of the Idea Analyst with real Claude. Excluded by default.

Run with:  pytest -m live tests/ai/test_idea_analyst_live.py -s

Two ideas: a clear one, and a deliberately vague one (doc 13: vague ideas should get
specific flags and questions). Cost: two Opus calls at effort medium, roughly 10 cents.
"""

import asyncio
import sys

import pytest

from src.ai.agents.idea_analyst import analyse_idea
from src.ai.context import IdeaForAnalysis
from scripts.evaluate import format_reading

pytestmark = pytest.mark.live

CLEAR = IdeaForAnalysis(
    idea=("Evaluate whether retrieval-augmented generation with a Wikipedia retriever reduces the hallucination "
          "rate of language models under 3B parameters on Georgian open-domain question answering, measured "
          "by answer accuracy and the share of unsupported answers against a closed-book baseline."),
    complexity="beginner", field="NLP", constraints=(("available time", "10 weeks"), ("compute", "one GPU")),
)
VAGUE = IdeaForAnalysis(idea="make LLMs better at low-resource languages", complexity="beginner")


def analyse_both():
    async def both():
        return await asyncio.gather(analyse_idea(CLEAR), analyse_idea(VAGUE))

    return asyncio.run(both())


def test_real_idea_readings():
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to cp1252, which has no "→"
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    clear, vague = analyse_both()
    for name, reading in (("clear", clear), ("vague", vague)):
        cost = reading.cost_usd
        print(f"\n--- {name} idea: {len(reading.records)} call(s), ${cost} ---\n{format_reading(reading.output)}")

    # the vague idea gets more flags and questions than the clear one
    assert vague.output.vagueness_flags and vague.output.clarifying_questions
    assert len(vague.output.vagueness_flags) >= len(clear.output.vagueness_flags)
    # the clear idea's stated facets come through, as the user's own
    clear_text = " ".join(f.text.lower() for f in clear.output.facets)
    assert "georgian" in clear_text and "retrieval" in clear_text
    assert clear.output.proposed_key_facets() == []
    # the vague idea's gaps are filled as proposals, never as the user's: the user named no method
    assert not any(f.stated_by_user for f in vague.output.facets_of("method"))
    assert vague.output.proposed_key_facets()
