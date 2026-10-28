"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WINDOWS_OUTPUTS_DIR = Path("D:/AutoMigrateOutputs")


def default_outputs_dir() -> Path:
    """Return the per-user outputs folder, independent of the cwd.

    On Windows the default is ``D:\\AutoMigrateOutputs`` when the drive exists;
    otherwise it falls back to ``%LOCALAPPDATA%\\AutoMigrate\\outputs``.  Every
    platform can override the location with ``AUTOMIGRATE_OUTPUTS_DIR``.
    """

    override = os.environ.get("AUTOMIGRATE_OUTPUTS_DIR", "").strip()
    if override:
        return Path(override).expanduser()

    if os.name == "nt":
        if WINDOWS_OUTPUTS_DIR.exists():
            return WINDOWS_OUTPUTS_DIR
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "AutoMigrate" / "outputs"

    return Path.home() / ".automigrate" / "outputs"


def default_db_path() -> Path:
    """Return the default SQLite database path inside the outputs folder."""

    return default_outputs_dir() / "automigrate.db"


def ensure_outputs_dir(path: str | Path | None = None) -> Path:
    """Create the outputs folder if needed and return it."""

    target = Path(path) if path is not None else default_outputs_dir()
    target.mkdir(parents=True, exist_ok=True)
    return target


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
        populate_by_name=True,
    )

    db_path: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("AUTOMIGRATE_DB", "db_path"),
    )
    outputs_dir: Path = Field(default_factory=default_outputs_dir)
    docs_dir: Path | None = None
    max_iterations: int = Field(default=5, ge=1)
    max_dependencies: int = Field(default=0, ge=0)
    top_k: int = Field(default=6, ge=1)
    score_threshold: float = 0.0
    subprocess_timeout: int = Field(default=120, ge=1)
    allow_undocumented: bool = False
    model: str | None = None

    @model_validator(mode="after")
    def _resolve_db_path(self) -> Settings:
        if self.db_path is None:
            self.db_path = self.outputs_dir / "automigrate.db"
        return self


def get_settings(**overrides: object) -> Settings:
    """Return settings, allowing explicit overrides for tests and callers."""

    return Settings(**overrides)
