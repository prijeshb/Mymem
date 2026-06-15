"""
Config loader — single source of truth for all settings.

Loads config.yaml first, then overrides with environment variables.
Fails closed: raises on startup if required secrets are missing for
the configured provider.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class PathsConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    raw: Path = Path("raw")
    wiki: Path = Path("wiki")
    outputs: Path = Path("outputs")
    db: Path = Path("data/mymem.db")


class ModelsConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    compile:       str = "gemma3:12b"
    qa:            str = "gemma3:12b"
    lint:          str = "gemma3:4b"
    classify:      str = "gemma3:4b"
    merge:         str = "gemma3:12b"
    introspect:    str = "gemma3:12b"
    export_slides: str = "gemma3:4b"
    export_charts: str = "gemma3:4b"
    embed:         str = "nomic-embed-text"


class OllamaConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)
    # Respects the standard OLLAMA_HOST env var (used by Ollama clients and Docker setups)
    base_url: str = Field(
        default="http://localhost:11434",
        validation_alias=AliasChoices("base_url", "OLLAMA_HOST"),
    )
    timeout_s: int = 120


class ObservabilityConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["rich", "json"] = "rich"
    log_file: Path | None = Path("data/mymem.log")
    trace_llm_calls: bool = True
    cost_alert_usd: float = 1.0


class SecurityConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    scan_ingested_content: bool = True
    prompt_injection_guard: bool = True
    max_file_size_mb: int = 50


class PipelineConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    compile_batch_size: int = 5
    index_on_compile: bool = True
    skip_unchanged: bool = True
    max_concepts: int = 3  # max wiki pages created per source (1 = one page per source)


# ---------------------------------------------------------------------------
# Root settings
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MYMEM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    provider: Literal[
        "anthropic", "ollama", "openai", "nvidia", "groq", "openrouter"
    ] = "ollama"

    # Secrets — from .env only, never config.yaml
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    groq_api_key: str | None = Field(default=None, alias="GROQ_API_KEY")
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    nvidia_api_key: str | None = Field(default=None, alias="NVIDIA_API_KEY")
    # Accept both the canonical name and the common OPEN_ROUTER_API_KEY misspelling.
    openrouter_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENROUTER_API_KEY", "OPEN_ROUTER_API_KEY"),
    )
    eval_reference_provider: Literal["groq", "gemini", "nvidia", "openrouter"] = "groq"

    # Sub-configs populated by load_config()
    paths: PathsConfig = Field(default_factory=PathsConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)

    @field_validator("anthropic_api_key", "openai_api_key", "nvidia_api_key", mode="before")
    @classmethod
    def _strip_whitespace(cls, v: str | None) -> str | None:
        return v.strip() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _fail_closed_on_missing_secret(self) -> Settings:
        """Refuse to start if the configured provider has no API key."""
        if self.provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError(
                "provider=anthropic but ANTHROPIC_API_KEY is not set. "
                "Add it to .env or switch provider to ollama in config.yaml."
            )
        if self.provider == "openai" and not self.openai_api_key:
            raise ValueError(
                "provider=openai but OPENAI_API_KEY is not set. "
                "Add it to .env or switch provider to ollama in config.yaml."
            )
        if self.provider == "nvidia" and not self.nvidia_api_key:
            raise ValueError(
                "provider=nvidia but NVIDIA_API_KEY is not set. "
                "Add it to .env or switch provider to ollama in config.yaml."
            )
        if self.provider == "groq" and not self.groq_api_key:
            raise ValueError(
                "provider=groq but GROQ_API_KEY is not set. "
                "Add it to .env or switch provider to ollama in config.yaml."
            )
        if self.provider == "openrouter" and not self.openrouter_api_key:
            raise ValueError(
                "provider=openrouter but OPENROUTER_API_KEY is not set. "
                "Add it to .env or switch provider to ollama in config.yaml."
            )
        return self

    def ensure_dirs(self) -> None:
        """Create all configured directories if they don't exist."""
        for path in (self.paths.raw, self.paths.wiki, self.paths.outputs):
            path.mkdir(parents=True, exist_ok=True)
        self.paths.db.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Loader — merges config.yaml into Settings
# ---------------------------------------------------------------------------

def _load_yaml(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    with config_path.open() as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def get_settings(config_path: str = "config.yaml") -> Settings:
    """
    Load settings once and cache. Call get_settings.cache_clear() in tests.
    Priority: env vars > config.yaml > defaults.
    """
    raw = _load_yaml(Path(config_path))

    # Flatten nested YAML into Settings constructor kwargs.
    # Env vars (via pydantic-settings) will override these.
    kwargs: dict = {}

    if "provider" in raw:
        kwargs["provider"] = raw["provider"]

    for section, model_cls in [
        ("paths", PathsConfig),
        ("models", ModelsConfig),
        ("ollama", OllamaConfig),
        ("observability", ObservabilityConfig),
        ("security", SecurityConfig),
        ("pipeline", PipelineConfig),
    ]:
        if section in raw:
            kwargs[section] = model_cls(**raw[section])

    # env vars (ANTHROPIC_API_KEY, OPENAI_API_KEY) are picked up by
    # pydantic-settings automatically from .env / shell — do not pass them
    # here, as an explicit None would override the .env file value.
    return Settings(**kwargs)
