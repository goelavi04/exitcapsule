"""Runtime configuration.

Why: provider, sandbox, Python versions, concurrency and thresholds must all be
changeable without code edits (brief §15.1). Values come from env vars / `.env`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    # Gemma
    gemma_provider: Literal["google", "ollama", "fake"] = "google"
    gemma_model: str = ""  # empty -> auto-discover a Gemma 4 model at startup
    gemini_api_key: SecretStr | None = None
    ollama_host: str = "http://localhost:11434"
    gemma_temperature: float = Field(0.15, ge=0.0, le=1.0)
    gemma_max_concurrency: int = Field(3, ge=1)
    gemma_max_retries: int = Field(2, ge=0)

    # Metadata
    github_token: SecretStr | None = None
    metadata_ttl_hours: int = 24

    # Execution
    sandbox: Literal["local", "docker"] = "local"
    python_versions: list[str] = ["3.12"]
    workers: int = Field(3, ge=1)
    heal_attempts: int = Field(3, ge=0)
    step_timeout_s: int = 600
    test_timeout_s: int = 120

    # Storage
    # Deliberately not called `home`: that would read the HOME env var.
    exitcapsule_home: Path = Path(".exitcapsule")

    @property
    def home(self) -> Path:
        return self.exitcapsule_home

    @property
    def runs_dir(self) -> Path:
        return self.home / "runs"

    @property
    def cache_dir(self) -> Path:
        return self.home / "cache"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
