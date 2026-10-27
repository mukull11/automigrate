import re
from pathlib import Path

import pytest

from automigrate.llm.keys import MissingKeyError, resolve_api_key

ROOT = Path(__file__).resolve().parents[1]


def test_explicit_key_wins_over_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    assert resolve_api_key("explicit-key") == "explicit-key"


def test_whitespace_explicit_key_falls_back_to_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    assert resolve_api_key("   ") == "environment-key"
    assert resolve_api_key(None) == "environment-key"


def test_missing_key_names_openai_api_key_and_docs(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(MissingKeyError) as excinfo:
        resolve_api_key(None)
    message = str(excinfo.value)
    assert "OPENAI_API_KEY" in message
    assert "docs/real_llm.md" in message


def test_missing_key_message_does_not_contain_key_value(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    test_key = "test-key-that-must-not-leak"
    with pytest.raises(MissingKeyError) as excinfo:
        resolve_api_key("   ")
    assert test_key not in str(excinfo.value)


def test_ui_has_no_os_environ_assignment() -> None:
    source = (ROOT / "app" / "ui.py").read_text(encoding="utf-8")
    assert not re.search(r"os\.environ\s*\[[^\]]*\]\s*=", source)
    assert not re.search(r"os\.environ\.(?:setdefault|update|pop|clear)\s*\(", source)
    assert not re.search(r"os\.environ\s*=\s*", source)
