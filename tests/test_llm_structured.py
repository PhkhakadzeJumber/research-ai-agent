"""Tests for run_structured() in src/llm.py.

A fake ADK model replaces Claude: the REAL ADK pipeline runs (agent, app, runner,
session, output schema, repair retry), just without network or cost.
"""

import asyncio
import json
from decimal import Decimal

import pytest
from google.genai import types

from src.ai.schemas.paper_analysis import PaperAnalysisOutput
from src.config import ConfigError, RoleLlmSettings, load_role_llm_settings
from tests.fakes import ScriptedLlm, answer
from src.llm import (
    LlmOutputInvalid,
    LlmRefusal,
    LlmTruncated,
    estimate_cost,
    run_structured,
)

SETTINGS = RoleLlmSettings(role="paper_reader", model="claude-opus-5-5", effort="low", max_tokens=16000)
VALID = json.dumps({"contribution_type": "method", "methods": ["Ragas"]})


def run(llm, **kwargs):
    return asyncio.run(run_structured(
        "paper_reader", system="SYSTEM PROMPT TEXT", prompt_version="shared_preamble@1+paper_reader@1",
        user_message="<paper id=\"p_1\">...</paper>", output_schema=PaperAnalysisOutput,
        settings=SETTINGS, model=llm, **kwargs,
    ))


# --- the normal case -------------------------------------------------------------------------------

def test_valid_output_is_returned_with_a_call_record():
    llm = ScriptedLlm.with_answers(answer(VALID))
    result = run(llm)

    assert isinstance(result.output, PaperAnalysisOutput)
    assert result.output.methods == ["Ragas"]
    [record] = result.records
    assert (record.role, record.model, record.effort, record.attempt) == ("paper_reader", "claude-opus-5-5", "low", 1)
    assert record.prompt_version == "shared_preamble@1+paper_reader@1"
    assert (record.input_tokens, record.cache_read_tokens) == (800, 2200)  # uncached = 3000 - 2200
    assert (record.output_tokens, record.thinking_tokens) == (500, 100)  # thinking is billed as output
    assert record.stop_reason == "STOP"
    assert record.cost_usd == estimate_cost("claude-opus-5-5", input_tokens=800, output_tokens=500,
                                            cache_read_tokens=2200, cache_creation_tokens=0)
    assert result.cost_usd == record.cost_usd


def test_request_carries_system_prompt_adaptive_thinking_effort_and_schema():
    llm = ScriptedLlm.with_answers(answer(VALID))
    run(llm)
    config = llm.requests[0].config

    assert "SYSTEM PROMPT TEXT" in str(config.system_instruction)
    assert config.thinking_config.thinking_budget == -1  # adaptive: required for structured output on Claude
    assert config.effort == "low"
    assert config.max_output_tokens == 16000
    assert config.response_schema is PaperAnalysisOutput
    user_text = llm.requests[0].contents[-1].parts[0].text
    assert user_text.startswith('<paper id="p_1">')


def test_system_prompt_is_sent_verbatim_without_placeholder_processing():
    llm = ScriptedLlm.with_answers(answer(VALID))
    asyncio.run(run_structured(
        "paper_reader", system="Return JSON like {\"key\": \"value\"} for {state_var}.", prompt_version="v",
        user_message="x", output_schema=PaperAnalysisOutput, settings=SETTINGS, model=llm,
    ))
    assert '{"key": "value"} for {state_var}' in str(llm.requests[0].config.system_instruction)


# --- repair and failure paths -----------------------------------------------------------------------

def test_invalid_output_gets_one_repair_attempt_in_the_same_conversation():
    llm = ScriptedLlm.with_answers(answer('{"contribution_type": "breakthrough"}'), answer(VALID))
    result = run(llm)

    assert result.output.contribution_type == "method"
    assert [r.attempt for r in result.records] == [1, 2]
    repair_request = llm.requests[1]
    texts = [part.text for content in repair_request.contents for part in content.parts if part.text]
    assert texts[0].startswith('<paper id="p_1">')  # the original message is still there (append-only)
    assert "could not be used" in texts[-1] and "contribution_type" in texts[-1]


def test_output_still_invalid_after_the_repair_attempt():
    llm = ScriptedLlm.with_answers(answer("not json at all"), answer('{"unexpected": 1}'))
    with pytest.raises(LlmOutputInvalid, match="after 2 attempts") as error:
        run(llm)
    assert [r.attempt for r in error.value.records] == [1, 2]  # the cost of both attempts is kept


def test_refusal_raises():
    with pytest.raises(LlmRefusal):
        run(ScriptedLlm.with_answers(answer("", types.FinishReason.SAFETY)))


def test_cut_off_output_raises_instead_of_being_used():
    with pytest.raises(LlmTruncated, match="max_tokens"):
        run(ScriptedLlm.with_answers(answer('{"contribution_type": "met', types.FinishReason.MAX_TOKENS)))


# --- cost estimates ---------------------------------------------------------------------------------------

def test_cost_estimate():
    # Opus 5.5: $4 input, $20 output, $0.20 cache read, $5 cache write per million tokens
    cost = estimate_cost("claude-opus-5-5", input_tokens=1_000_000, output_tokens=1_000_000,
                         cache_read_tokens=1_000_000, cache_creation_tokens=1_000_000)
    assert cost == Decimal("29.200000")
    assert estimate_cost("some-unknown-model", input_tokens=1, output_tokens=1, cache_read_tokens=0,
                         cache_creation_tokens=0) is None  # never guessed


# --- per-role settings ----------------------------------------------------------------------------------------

BASE = {"EUREKA_LLM_MODEL": "claude-opus-5-5"}


def test_role_defaults():
    reader = load_role_llm_settings("paper_reader", BASE)
    assert (reader.model, reader.effort, reader.max_tokens) == ("claude-opus-5-5", "low", 16000)
    assert load_role_llm_settings("skeptic", BASE).effort == "high"
    assert load_role_llm_settings("unknown_role", BASE).effort == "medium"  # falls back to EUREKA_LLM_EFFORT


def test_role_overrides():
    settings = load_role_llm_settings("paper_reader", {
        **BASE, "EUREKA_LLM_MODEL_PAPER_READER": "claude-haiku-5-5", "EUREKA_LLM_EFFORT_PAPER_READER": "medium",
    })
    assert (settings.model, settings.effort) == ("claude-haiku-5-5", "medium")


def test_invalid_role_effort_is_rejected():
    with pytest.raises(ConfigError, match="EUREKA_LLM_EFFORT_PAPER_READER"):
        load_role_llm_settings("paper_reader", {**BASE, "EUREKA_LLM_EFFORT_PAPER_READER": "extreme"})


def test_role_output_limits():
    assert load_role_llm_settings("scout", BASE).max_tokens == 20000  # 8-12 candidates in one answer
    assert load_role_llm_settings("scout", {**BASE, "EUREKA_LLM_MAX_TOKENS_SCOUT": "18000"}).max_tokens == 18000
    assert load_role_llm_settings("novelty", {**BASE, "EUREKA_LLM_MAX_TOKENS": "12000"}).max_tokens == 12000
    with pytest.raises(ConfigError, match="EUREKA_LLM_MAX_TOKENS_SCOUT"):
        load_role_llm_settings("scout", {**BASE, "EUREKA_LLM_MAX_TOKENS_SCOUT": "lots"})
