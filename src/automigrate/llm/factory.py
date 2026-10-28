"""Shared LLM adapter construction for the graph and auto-migration modes."""

from __future__ import annotations

from collections.abc import Callable

from automigrate.llm.base import LLMAdapter, LLMError
from automigrate.llm.chain import create_provider_chain
from automigrate.llm.keys import resolve_api_key
from automigrate.llm.models import check_model, list_models
from automigrate.llm.providers import get_provider


def build_llm_adapter(
    *,
    model: str | None = None,
    provider: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    on_attempt: Callable[..., None] | None = None,
) -> LLMAdapter:
    """Build the provider/model chain used by migrations and triage.

    With no provider this preserves the historical default-provider adapter.
    With a provider it discovers the provider's models, checks the first one,
    and builds a single-provider fallback chain.
    """

    from automigrate.llm import create_adapter

    if provider is None:
        return create_adapter(model, api_key=api_key)

    selected = get_provider(provider)
    key = resolve_api_key(selected, api_key)
    adapter_base_url = base_url or selected.base_url
    listing = list_models(selected, key, base_url=adapter_base_url)
    models = list(listing.models)
    requested = (model or "").strip()
    if requested:
        if requested in models:
            index = models.index(requested)
            models = models[index:] + models[:index]
        else:
            models = [requested, *models]
    if not models:
        raise LLMError(f"{selected.display_name} returned no models")
    checked_model = models[0]
    check = check_model(selected, checked_model, key, base_url=adapter_base_url)
    if check.fatal:
        raise LLMError(
            f"{selected.display_name} rejected model {checked_model} "
            f"(HTTP {check.status_code}): {check.error or 'unknown error'}"
        )
    return create_provider_chain(
        selected,
        models,
        api_key=key,
        base_url=adapter_base_url,
        on_attempt=on_attempt,
    )
