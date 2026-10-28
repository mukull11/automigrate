import os

import pytest

from automigrate.config import (
    Settings,
    default_db_path,
    default_outputs_dir,
    get_settings,
)


def test_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.db_path == default_db_path()
    assert settings.max_iterations == 5
    assert settings.max_dependencies == 0
    assert settings.top_k == 6
    assert settings.subprocess_timeout == 120
    assert settings.allow_undocumented is False
    assert settings.model is None


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific fallback")
def test_outputs_fallback_when_d_drive_is_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AUTOMIGRATE_OUTPUTS_DIR", raising=False)
    monkeypatch.setattr(
        "automigrate.config.WINDOWS_OUTPUTS_DIR", tmp_path / "missing-drive"
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert (
        default_outputs_dir()
        == tmp_path / "local" / "AutoMigrate" / "outputs"
    )


def test_env_override(monkeypatch) -> None:
    monkeypatch.setenv("AUTOMIGRATE_MAX_ITERATIONS", "3")
    monkeypatch.setenv("AUTOMIGRATE_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("AUTOMIGRATE_ALLOW_UNDOCUMENTED", "true")
    settings = Settings(_env_file=None)
    assert settings.max_iterations == 3
    assert settings.model == "gemini-2.5-flash"
    assert settings.allow_undocumented is True


def test_get_settings_overrides() -> None:
    settings = get_settings(max_iterations=2, _env_file=None)
    assert settings.max_iterations == 2
