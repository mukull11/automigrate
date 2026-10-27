"""Application configuration loaded from environment variables."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def default_outputs_dir() -> Path:
    """Return the project-level outputs folder, independent of the cwd."""

    return PROJECT_ROOT / "outputs"


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
    outputs_dir: Path = Field(default_factory=default_outputs_dir)
    docs_dir: Path | None = None
    max_iterations: int = Field(default=5, ge=1)
    top_k: int = Field(default=6, ge=1)
    score_threshold: float = 0.0
    subprocess_timeout: int = Field(default=120, ge=1)
    allow_undocumented: bool = False
    model: str | None = None


def get_settings(**overrides: object) -> Settings:
    """Return settings, allowing explicit overrides for tests and callers."""

    return Settings(**overrides)
