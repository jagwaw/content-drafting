"""Settings from the environment, validated once at startup, plus fixed content limits."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from dotenv import load_dotenv

# Input limits (per row).
MAX_TOPIC_CHARS = 200
MAX_NOTES_CHARS = 1000

# Output structure limits (per draft).
MAX_CAPTION_CHARS = 2200
MIN_SLIDES = 5
MAX_SLIDES = 8
MIN_SCRIPT_WORDS = 80
MAX_SCRIPT_WORDS = 200

LlmProvider = Literal["anthropic", "openai"]
BackendName = Literal["csv", "sheet"]


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    llm_provider: LlmProvider
    llm_model: str
    # Secrets are excluded from repr so an accidental print or log can't leak them.
    anthropic_api_key: str | None = field(repr=False)
    openai_api_key: str | None = field(repr=False)
    openai_base_url: str | None
    max_llm_calls_per_hour: int
    max_rows_per_run: int
    backend: BackendName
    csv_path: Path
    sheet_id: str | None
    google_service_account_file: Path
    audit_log_dir: Path


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build settings from `env` (default: `.env` + process environment) and validate them.

    Taking `env` as an argument keeps tests free of global environment changes.
    """
    if env is None:
        load_dotenv()
        env = os.environ
    settings = Settings(
        llm_provider=cast(
            LlmProvider, _choice(env, "LLM_PROVIDER", "anthropic", {"anthropic", "openai"})
        ),
        llm_model=env.get("LLM_MODEL") or "claude-haiku-5-5",
        anthropic_api_key=env.get("ANTHROPIC_API_KEY") or None,
        openai_api_key=env.get("OPENAI_API_KEY") or None,
        openai_base_url=env.get("OPENAI_BASE_URL") or None,
        max_llm_calls_per_hour=_positive_int(env, "MAX_LLM_CALLS_PER_HOUR", 100),
        max_rows_per_run=_positive_int(env, "MAX_ROWS_PER_RUN", 10),
        backend=cast(BackendName, _choice(env, "BACKEND", "csv", {"csv", "sheet"})),
        csv_path=Path(env.get("CSV_PATH") or "data/demo_topics.csv"),
        sheet_id=env.get("SHEET_ID") or None,
        google_service_account_file=Path(
            env.get("GOOGLE_SERVICE_ACCOUNT_FILE") or "service_account.json"
        ),
        audit_log_dir=Path(env.get("AUDIT_LOG_DIR") or "audit_logs"),
    )
    _check_required(settings)
    return settings


def _choice(env: Mapping[str, str], name: str, default: str, allowed: set[str]) -> str:
    value = (env.get(name) or default).strip().lower()
    if value not in allowed:
        raise ConfigError(f"{name} must be one of {sorted(allowed)}, got {value!r}")
    return value


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be positive, got {value}")
    return value


def _check_required(settings: Settings) -> None:
    if settings.llm_provider == "anthropic" and not settings.anthropic_api_key:
        raise ConfigError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")
    if settings.llm_provider == "openai" and not settings.openai_api_key:
        raise ConfigError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
    if settings.backend == "sheet" and not settings.sheet_id:
        raise ConfigError("SHEET_ID is required when BACKEND=sheet")
