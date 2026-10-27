"""Ordered model fallback chain used by live migrations."""

from __future__ import annotations

import os
from collections.abc import Callable

from automigrate.llm.base import LLMAdapter, LLMError

DEFAULT_MODELS = (
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "gemini-2.5-flash",
)


def available_models() -> list[str]:
    """Return the model dropdown options, honoring AUTOMIGRATE_MODELS."""

    override = os.environ.get("AUTOMIGRATE_MODELS", "")
    models = [item.strip() for item in override.split(",") if item.strip()]
    if models:
        return models
    return list(DEFAULT_MODELS)


def build_model_chain(requested: str | None = None) -> list[str]:
    models = available_models()
    selected = (requested or "").strip()
    if not selected:
        return models
    if selected in models:
        index = models.index(selected)
        return models[index:] + models[:index]
    return [selected, *models]


class ModelChainLLM(LLMAdapter):
    """Try each model in order; advance explicitly after a rejected response."""

    name = "model-chain"

    def __init__(
        self,
        models: list[str],
        *,
        factory: Callable[[str], LLMAdapter] | None = None,
        api_key: str | None = None,
    ) -> None:
        if not models:
            raise LLMError("no models configured")
        self.models = list(models)
        self._index = 0
        self._api_key = api_key
        self._factory = factory or (
            lambda model: _default_factory(model, api_key)
        )
        self._adapters: dict[str, LLMAdapter] = {}

    @property
    def current_model(self) -> str:
        return self.models[self._index]

    @property
    def model_names(self) -> list[str]:
        return list(self.models)

    def _current(self) -> LLMAdapter:
        model = self.current_model
        adapter = self._adapters.get(model)
        if adapter is None:
            adapter = self._factory(model)
            self._adapters[model] = adapter
        return adapter

    def advance_model(self) -> bool:
        if self._index + 1 >= len(self.models):
            return False
        self._index += 1
        return True

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        stage: str | None = None,
        iteration: int = 0,
    ) -> str:
        while True:
            adapter = self._current()
            try:
                return adapter.complete(
                    prompt, system=system, stage=stage, iteration=iteration
                )
            except LLMError:
                if not self.advance_model():
                    raise


def _default_factory(model: str, api_key: str | None = None) -> LLMAdapter:
    from automigrate.llm.openai import OpenAILLM

    return OpenAILLM(model=model, api_key=api_key)
