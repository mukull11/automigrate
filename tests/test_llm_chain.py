import pytest

from automigrate.llm.base import LLMAdapter, LLMError
from automigrate.llm.chain import DEFAULT_MODELS, ModelChainLLM, build_model_chain


class StubAdapter(LLMAdapter):
    name = "stub"

    def __init__(self, model: str, *, fail: bool = False) -> None:
        self.model = model
        self.fail = fail
        self.calls = 0

    def complete(self, prompt, *, system=None, stage=None, iteration=0) -> str:
        self.calls += 1
        if self.fail:
            raise LLMError(f"{self.model} failed")
        return f"answer from {self.model}"


def test_default_models_are_the_documented_chain() -> None:
    assert DEFAULT_MODELS == (
        "openai/gpt-oss-120b",
        "qwen/qwen3.8-27b",
        "gemini-2.5-flash",
    )


def test_env_override_changes_the_chain(monkeypatch) -> None:
    monkeypatch.setenv("AUTOMIGRATE_MODELS", "one, two ,three")
    assert build_model_chain() == ["one", "two", "three"]


def test_selected_model_starts_the_chain(monkeypatch) -> None:
    monkeypatch.delenv("AUTOMIGRATE_MODELS", raising=False)
    chain = build_model_chain("gemini-2.5-flash")
    assert chain[0] == "gemini-2.5-flash"
    assert set(chain) == set(DEFAULT_MODELS)


def test_chain_falls_back_on_llm_error() -> None:
    adapters = {
        "one": StubAdapter("one", fail=True),
        "two": StubAdapter("two"),
    }
    chain = ModelChainLLM(["one", "two"], factory=lambda model: adapters[model])
    assert chain.complete("hi") == "answer from two"
    assert adapters["one"].calls == 1
    assert adapters["two"].calls == 1


def test_chain_advance_is_explicit_and_exhausts() -> None:
    adapters = {
        "one": StubAdapter("one"),
        "two": StubAdapter("two"),
    }
    chain = ModelChainLLM(["one", "two"], factory=lambda model: adapters[model])
    assert chain.current_model == "one"
    assert chain.advance_model() is True
    assert chain.current_model == "two"
    assert chain.advance_model() is False


def test_chain_raises_after_last_model() -> None:
    adapters = {"one": StubAdapter("one", fail=True)}
    chain = ModelChainLLM(["one"], factory=lambda model: adapters[model])
    with pytest.raises(LLMError):
        chain.complete("hi")
