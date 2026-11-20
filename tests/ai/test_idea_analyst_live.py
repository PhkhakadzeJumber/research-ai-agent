"""Live check of the Idea Analyst with real Claude. Excluded by default.

Run with:  pytest -m live tests/ai/test_idea_analyst_live.py -s

test_real_idea_readings: two ideas, a clear one and a deliberately vague one (doc 13:
vague ideas should get specific flags and questions). Two Opus calls, roughly 9 cents.

test_real_revision: the vague idea, then the user's answers applied by the revise call
(step 4.3). Two Opus calls, roughly 9 cents. Run one test with -k.
"""

import asyncio
import sys

import pytest

from src.ai.agents.idea_analyst import analyse_idea, revise_idea
from src.ai.context import IdeaForAnalysis, QuestionAnswer
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


def test_real_revision():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    async def read_then_revise():
        first = await analyse_idea(VAGUE)
        questions = first.output.clarifying_questions
        assert questions, "the vague idea should get questions"
        # The user answers the first question only: the approach.
        answers = (QuestionAnswer(questions[0].question, "LoRA fine-tuning of a small open model on Georgian data"),)
        return first, await revise_idea(VAGUE, first.output, answers)

    first, revised = asyncio.run(read_then_revise())
    print(f"\n--- before (${first.cost_usd}) ---\n{format_reading(first.output)}")
    print(f"\n--- after the answer (${revised.cost_usd}) ---\n{format_reading(revised.output)}")

    methods = revised.output.facets_of("method")
    assert any("lora" in f.text.lower() and f.stated_by_user for f in methods)  # the answer is the user's now
    assert revised.output.clarifying_questions == []
