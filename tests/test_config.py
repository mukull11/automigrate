from pathlib import Path

from automigrate.config import Settings, get_settings


def test_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.db_path == Path("automigrate.db")
    assert settings.max_iterations == 5
    assert settings.top_k == 6
    assert settings.subprocess_timeout == 120
    assert settings.allow_undocumented is False
    assert settings.model is None


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
