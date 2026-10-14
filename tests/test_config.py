from pathlib import Path

import pytest

from content_agent.config import ConfigError, load_settings

VALID_ENV = {"ANTHROPIC_API_KEY": "sk-test-secret"}


def test_defaults_apply_when_only_the_key_is_set() -> None:
    settings = load_settings(VALID_ENV)

    assert settings.llm_provider == "anthropic"
    assert settings.llm_model == "claude-haiku-5-5"
    assert settings.max_llm_calls_per_hour == 100
    assert settings.max_rows_per_run == 10
    assert settings.backend == "csv"
    assert settings.audit_log_dir == Path("audit_logs")


def test_empty_values_fall_back_to_defaults() -> None:
    settings = load_settings({**VALID_ENV, "MAX_ROWS_PER_RUN": "", "LLM_MODEL": ""})

    assert settings.max_rows_per_run == 10
    assert settings.llm_model == "claude-haiku-5-5"


def test_missing_anthropic_key_fails_fast() -> None:
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        load_settings({})


def test_openai_provider_requires_openai_key() -> None:
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        load_settings({"LLM_PROVIDER": "openai", "ANTHROPIC_API_KEY": "x"})


def test_openai_provider_accepts_base_url() -> None:
    settings = load_settings(
        {
            "LLM_PROVIDER": "openai",
            "OPENAI_API_KEY": "x",
            "OPENAI_BASE_URL": "https://api.groq.com/openai/v1",
        }
    )

    assert settings.openai_base_url == "https://api.groq.com/openai/v1"


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ConfigError, match="LLM_PROVIDER"):
        load_settings({**VALID_ENV, "LLM_PROVIDER": "gemini"})


def test_unknown_backend_is_rejected() -> None:
    with pytest.raises(ConfigError, match="BACKEND"):
        load_settings({**VALID_ENV, "BACKEND": "excel"})


def test_sheet_backend_requires_sheet_id() -> None:
    with pytest.raises(ConfigError, match="SHEET_ID"):
        load_settings({**VALID_ENV, "BACKEND": "sheet"})


@pytest.mark.parametrize("value", ["ten", "1.5"])
def test_non_integer_limit_is_rejected(value: str) -> None:
    with pytest.raises(ConfigError, match="must be an integer"):
        load_settings({**VALID_ENV, "MAX_ROWS_PER_RUN": value})


@pytest.mark.parametrize("value", ["0", "-3"])
def test_non_positive_limit_is_rejected(value: str) -> None:
    with pytest.raises(ConfigError, match="must be positive"):
        load_settings({**VALID_ENV, "MAX_LLM_CALLS_PER_HOUR": value})


def test_repr_never_shows_api_keys() -> None:
    settings = load_settings({**VALID_ENV, "OPENAI_API_KEY": "sk-other-secret"})

    assert "sk-test-secret" not in repr(settings)
    assert "sk-other-secret" not in repr(settings)
