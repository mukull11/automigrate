"""Ordered model fallback chain used by live migrations."""

from __future__ import annotations

import os
from collections.abc import Callable

from automigrate.llm.base import LLMAdapter, LLMError
from automigrate.llm.providers import Provider, get_provider

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
    """Try each model in order; advance explicitly after a rejected response.

    A chain is always built for one provider.  It never switches providers on
    its own; callers that need another provider must create a new chain.
    """

    name = "model-chain"

    def __init__(
        self,
        models: list[str],
        *,
        factory: Callable[[str], LLMAdapter] | None = None,
        api_key: str | None = None,
        provider: str | Provider | None = None,
        on_attempt: Callable[..., None] | None = None,
    ) -> None:
        if not models:
            raise LLMError("no models configured")
        self.models = list(models)
        self._index = 0
        self._api_key = api_key
        self.provider = get_provider(provider).id if provider is not None else None
        self._on_attempt = on_attempt
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

    def _notify(
        self,
        model: str,
        status_code: int | None,
        error: str | None,
        *,
        success: bool,
    ) -> None:
        if self._on_attempt is None:
            return
        self._on_attempt(
            provider=self.provider,
            model=model,
            status_code=status_code,
            error=error,
            success=success,
        )

    @staticmethod
    def _should_fallback(exc: LLMError) -> bool:
        # 404 (unknown model) and 429 (rate limit) are model-specific.  A
        # transport error without a status is also safe to retry.  Authentication
        # failures (401/403) are not fixed by changing the model.
        return exc.status_code in (None, 404, 429)

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
            model = self.current_model
            try:
                text = adapter.complete(
                    prompt, system=system, stage=stage, iteration=iteration
                )
            except LLMError as exc:
                self._notify(
                    model, exc.status_code, str(exc), success=False
                )
                if not self._should_fallback(exc) or not self.advance_model():
                    raise
                continue
            self._notify(model, None, None, success=True)
            return text


def _default_factory(
    model: str,
    api_key: str | None = None,
    *,
    provider: Provider | None = None,
) -> LLMAdapter:
    from automigrate.llm.anthropic import AnthropicLLM
    from automigrate.llm.openai import OpenAILLM

    selected = provider or get_provider(None)
    if selected.sdk == "anthropic":
        return AnthropicLLM(
            model=model,
            api_key=api_key,
            base_url=selected.base_url,
            provider=selected.id,
        )
    return OpenAILLM(
        model=model,
        api_key=api_key,
        base_url=selected.base_url,
        provider=selected.id,
    )


def create_provider_chain(
    provider: str | Provider,
    models: list[str],
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    on_attempt: Callable[..., None] | None = None,
    client_factory: Callable[[str], LLMAdapter] | None = None,
) -> ModelChainLLM:
    """Build a single-provider fallback chain from discovered model ids."""

    selected = get_provider(provider)
    adapter_base = base_url or selected.base_url
    factory = client_factory or (
        lambda model: _factory_with_base(
            selected, model, api_key, adapter_base
        )
    )
    return ModelChainLLM(
        models,
        factory=factory,
        api_key=api_key,
        provider=selected,
        on_attempt=on_attempt,
    )


def _factory_with_base(
    provider: Provider,
    model: str,
    api_key: str | None,
    base_url: str | None,
) -> LLMAdapter:
    from automigrate.llm.anthropic import AnthropicLLM
    from automigrate.llm.openai import OpenAILLM

    if provider.sdk == "anthropic":
        return AnthropicLLM(
            model=model,
            api_key=api_key,
            base_url=base_url,
            provider=provider.id,
        )
    return OpenAILLM(
        model=model,
        api_key=api_key,
        base_url=base_url,
        provider=provider.id,
    )
