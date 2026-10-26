from pathlib import Path

from automigrate.config import Settings, get_settings


def test_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.db_path == Path("automigrate.db")
    assert settings.max_iterations == 5
    assert settings.top_k == 6
    assert settings.subprocess_timeout == 120
    assert settings.llm_provider == "mock"


def test_env_override(monkeypatch) -> None:
    monkeypatch.setenv("AUTOMIGRATE_MAX_ITERATIONS", "3")
    monkeypatch.setenv("AUTOMIGRATE_LLM_PROVIDER", "openai")
    settings = Settings(_env_file=None)
    assert settings.max_iterations == 3
    assert settings.llm_provider == "openai"


def test_get_settings_overrides() -> None:
    settings = get_settings(max_iterations=2, _env_file=None)
    assert settings.max_iterations == 2
