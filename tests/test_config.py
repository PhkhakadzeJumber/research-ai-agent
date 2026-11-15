"""Tests for src/config.py. They use plain dicts, never the real environment or .env."""

import pytest

from src.config import ConfigError, load_database_settings, load_retrieval_settings, load_settings


def test_database_url_is_built_from_postgres_variables_with_defaults():
    settings = load_database_settings({"POSTGRES_PASSWORD": "pw"})
    assert settings.url == "postgresql+asyncpg://eureka:pw@localhost:5434/eureka"
    assert settings.test_url == "postgresql+asyncpg://eureka:pw@localhost:5434/eureka_test"


def test_database_password_with_special_characters_is_escaped():
    settings = load_database_settings({"POSTGRES_PASSWORD": "p@ss:w/rd"})
    assert "p%40ss%3Aw%2Frd" in settings.url


def test_database_url_wins_and_test_url_is_derived():
    settings = load_database_settings({
        "DATABASE_URL": "postgresql+asyncpg://user:secret@cloudhost:5432/prod",
        "POSTGRES_PASSWORD": "ignored",
    })
    assert settings.url == "postgresql+asyncpg://user:secret@cloudhost:5432/prod"
    assert settings.test_url.endswith("/eureka_test")


def test_database_secrets_never_show_when_printed():
    settings = load_database_settings({"POSTGRES_PASSWORD": "TOPSECRET"})
    assert "TOPSECRET" not in repr(settings)
    assert "TOPSECRET" not in settings.redacted() and "***" in settings.redacted()
    assert "TOPSECRET" not in settings.redacted(test=True)


@pytest.mark.parametrize(
    "environ, message",
    [
        ({}, "POSTGRES_PASSWORD"),
        ({"POSTGRES_PASSWORD": "pw", "POSTGRES_PORT": "abc"}, "POSTGRES_PORT"),
        ({"POSTGRES_PASSWORD": "pw", "POSTGRES_TEST_DB": "eureka"}, "different"),
        ({"DATABASE_URL": "not a url"}, "DATABASE_URL"),
    ],
)
def test_invalid_database_settings(environ, message):
    with pytest.raises(ConfigError, match=message):
        load_database_settings(environ)


def test_retrieval_settings_are_optional_and_hide_secrets():
    empty = load_retrieval_settings({})
    assert empty.openalex_api_key is None and empty.openalex_mailto is None

    settings = load_retrieval_settings({"OPENALEX_API_KEY": " secret-key ", "OPENALEX_MAILTO": ""})
    assert settings.openalex_api_key == "secret-key"
    assert settings.openalex_mailto is None
    assert "secret-key" not in repr(settings)


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
