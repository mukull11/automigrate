from types import SimpleNamespace

import pytest

from automigrate.llm.base import LLMError
from automigrate.llm.factory import build_llm_adapter
from automigrate.llm.providers import get_provider


def test_without_provider_uses_default_adapter(monkeypatch) -> None:
    captured = {}

    def fake_create_adapter(model=None, api_key=None):
        captured["model"] = model
        captured["api_key"] = api_key
        return "adapter"

    monkeypatch.setattr("automigrate.llm.create_adapter", fake_create_adapter)
    adapter = build_llm_adapter(model="m", api_key="k")
    assert adapter == "adapter"
    assert captured == {"model": "m", "api_key": "k"}


def test_provider_chain_rotates_requested_model(monkeypatch) -> None:
    provider = get_provider("openai")
    captured = {}
    callback = lambda **kwargs: None  # noqa: E731

    monkeypatch.setattr("automigrate.llm.factory.get_provider", lambda value: provider)
    monkeypatch.setattr(
        "automigrate.llm.factory.resolve_api_key", lambda selected, key: "resolved"
    )
    monkeypatch.setattr(
        "automigrate.llm.factory.list_models",
        lambda *args, **kwargs: SimpleNamespace(models=["a", "b", "c"]),
    )
    monkeypatch.setattr(
        "automigrate.llm.factory.check_model",
        lambda *args, **kwargs: SimpleNamespace(
            fatal=False, status_code=200, error=None
        ),
    )

    def fake_chain(selected, models, **kwargs):
        captured["provider"] = selected
        captured["models"] = models
        captured["kwargs"] = kwargs
        return "chain"

    monkeypatch.setattr("automigrate.llm.factory.create_provider_chain", fake_chain)
    adapter = build_llm_adapter(
        provider="openai",
        model="b",
        api_key="key",
        base_url="https://example.test",
        on_attempt=callback,
    )

    assert adapter == "chain"
    assert captured["models"] == ["b", "c", "a"]
    assert captured["kwargs"]["api_key"] == "resolved"
    assert captured["kwargs"]["base_url"] == "https://example.test"
    assert captured["kwargs"]["on_attempt"] is callback


def test_requested_model_not_in_list_is_prepended(monkeypatch) -> None:
    provider = get_provider("openai")
    captured = {}

    monkeypatch.setattr("automigrate.llm.factory.get_provider", lambda value: provider)
    monkeypatch.setattr(
        "automigrate.llm.factory.resolve_api_key", lambda selected, key: "resolved"
    )
    monkeypatch.setattr(
        "automigrate.llm.factory.list_models",
        lambda *args, **kwargs: SimpleNamespace(models=["a"]),
    )
    monkeypatch.setattr(
        "automigrate.llm.factory.check_model",
        lambda *args, **kwargs: SimpleNamespace(
            fatal=False, status_code=200, error=None
        ),
    )

    def fake_chain(selected, models, **kwargs):
        captured["models"] = models
        return "chain"

    monkeypatch.setattr("automigrate.llm.factory.create_provider_chain", fake_chain)
    build_llm_adapter(provider="openai", model="custom")
    assert captured["models"] == ["custom", "a"]


def test_no_models_raises(monkeypatch) -> None:
    provider = get_provider("openai")
    monkeypatch.setattr("automigrate.llm.factory.get_provider", lambda value: provider)
    monkeypatch.setattr(
        "automigrate.llm.factory.resolve_api_key", lambda selected, key: "resolved"
    )
    monkeypatch.setattr(
        "automigrate.llm.factory.list_models",
        lambda *args, **kwargs: SimpleNamespace(models=[]),
    )
    with pytest.raises(LLMError, match="returned no models"):
        build_llm_adapter(provider="openai")


def test_fatal_model_check_raises(monkeypatch) -> None:
    provider = get_provider("openai")
    monkeypatch.setattr("automigrate.llm.factory.get_provider", lambda value: provider)
    monkeypatch.setattr(
        "automigrate.llm.factory.resolve_api_key", lambda selected, key: "resolved"
    )
    monkeypatch.setattr(
        "automigrate.llm.factory.list_models",
        lambda *args, **kwargs: SimpleNamespace(models=["a"]),
    )
    monkeypatch.setattr(
        "automigrate.llm.factory.check_model",
        lambda *args, **kwargs: SimpleNamespace(
            fatal=True, status_code=404, error="nope"
        ),
    )
    with pytest.raises(LLMError, match="rejected model a"):
        build_llm_adapter(provider="openai")
