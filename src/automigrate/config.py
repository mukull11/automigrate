"""Application configuration loaded from environment variables."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for AutoMigrate.

    Values are read from ``AUTOMIGRATE_*`` environment variables or a local
    ``.env`` file. API keys are intentionally not modelled here; adapters read
    them directly from the environment and never persist them.
    """

    model_config = SettingsConfigDict(
        env_prefix="AUTOMIGRATE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_path: Path = Path("automigrate.db")
    outputs_dir: Path = Path("outputs")
    docs_dir: Path | None = None
    max_iterations: int = Field(default=5, ge=1)
    top_k: int = Field(default=6, ge=1)
    score_threshold: float = 0.0
    subprocess_timeout: int = Field(default=120, ge=1)
    model: str | None = None


def get_settings(**overrides: object) -> Settings:
    """Return settings, allowing explicit overrides for tests and callers."""

    return Settings(**overrides)
