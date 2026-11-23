"""Tests for src/config.py. They use plain dicts, never the real environment or .env."""

import os

import pytest
from sqlalchemy.engine import make_url

from src.config import (
    ConfigError,
    load_billing_settings,
    load_database_settings,
    load_env_file,
    load_retrieval_settings,
    load_settings,
)


def test_database_url_is_built_from_postgres_variables_with_defaults():
    settings = load_database_settings({"POSTGRES_PASSWORD": "pw"})
    assert settings.url == "postgresql+asyncpg://eureka:pw@127.0.0.1:5434/eureka"
    assert settings.test_url == "postgresql+asyncpg://eureka:pw@127.0.0.1:5434/eureka_test"


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


# --- billing (docs/18; step P2) -------------------------------------------------------------------------------

def test_billing_settings_default_to_the_documented_plan():
    s = load_billing_settings({})
    assert (s.credit_usd, s.price_evaluate, s.price_discover, s.price_package) == (0.04, 25, 60, 5)
    assert (s.pro_monthly_credits, s.free_evaluations, s.free_monthly_budget_usd, s.run_cap_factor) == (100, 1, 20.0, 1.5)


def test_billing_settings_can_be_changed_and_are_checked():
    s = load_billing_settings({"EUREKA_PRICE_EVALUATE": "30", "EUREKA_FREE_EVALUATIONS": "0",
                               "EUREKA_CREDIT_USD": "0.05"})
    assert (s.price_evaluate, s.free_evaluations, s.credit_usd) == (30, 0, 0.05)
    for name, value in [("EUREKA_PRICE_DISCOVER", "0"), ("EUREKA_PRICE_PACKAGE", "five"), ("EUREKA_CREDIT_USD", "0"),
                        ("EUREKA_RUN_CAP_FACTOR", "0.8"), ("EUREKA_FREE_MONTHLY_BUDGET_USD", "-1")]:
        with pytest.raises(ConfigError, match=name):
            load_billing_settings({name: value})


# --- the cloud (step E) ----------------------------------------------------------------------------------------

def test_a_neon_style_database_url_is_converted_for_asyncpg():
    neon = "postgresql://eureka:secret@ep-cool-name-123.eu-central-1.aws.neon.tech/eureka?sslmode=require&channel_binding=require"
    settings = load_database_settings({"DATABASE_URL": neon})
    url = make_url(settings.url)
    assert url.drivername == "postgresql+asyncpg" and dict(url.query) == {"ssl": "require"}
    assert url.host == "ep-cool-name-123.eu-central-1.aws.neon.tech" and url.password == "secret"
    local = load_database_settings({"DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5434/eureka"})
    assert make_url(local.url).query == {}  # an asyncpg URL without sslmode stays as it is


def test_env_file_path_comes_from_eureka_env_file(monkeypatch, tmp_path):
    env = tmp_path / "secret.env"
    env.write_text("EUREKA_TEST_ONLY_SETTING=from-the-file\n", encoding="utf-8")
    monkeypatch.setenv("EUREKA_ENV_FILE", str(env))
    monkeypatch.delenv("EUREKA_TEST_ONLY_SETTING", raising=False)
    load_env_file()
    assert os.environ["EUREKA_TEST_ONLY_SETTING"] == "from-the-file"
    monkeypatch.delenv("EUREKA_TEST_ONLY_SETTING")
