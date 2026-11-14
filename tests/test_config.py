"""Tests for src/config.py. They use plain dicts, never the real environment or .env."""

import pytest

from src.config import ConfigError, load_settings


def test_reads_model_and_uses_defaults():
    settings = load_settings({"EUREKA_LLM_MODEL": "model-x"})
    assert settings.llm_model == "model-x"
    assert settings.llm_effort == "medium"
    assert settings.llm_max_tokens == 16000


def test_reads_all_values():
    settings = load_settings(
        {"EUREKA_LLM_MODEL": "model-x", "EUREKA_LLM_EFFORT": "high", "EUREKA_LLM_MAX_TOKENS": "4096"}
    )
    assert (settings.llm_effort, settings.llm_max_tokens) == ("high", 4096)


def test_missing_model_is_an_error():
    with pytest.raises(ConfigError, match="EUREKA_LLM_MODEL"):
        load_settings({})


def test_invalid_effort_is_an_error():
    with pytest.raises(ConfigError, match="EUREKA_LLM_EFFORT"):
        load_settings({"EUREKA_LLM_MODEL": "model-x", "EUREKA_LLM_EFFORT": "extreme"})


@pytest.mark.parametrize("value", ["abc", "0", "-5"])
def test_invalid_max_tokens_is_an_error(value):
    with pytest.raises(ConfigError, match="EUREKA_LLM_MAX_TOKENS"):
        load_settings({"EUREKA_LLM_MODEL": "model-x", "EUREKA_LLM_MAX_TOKENS": value})
