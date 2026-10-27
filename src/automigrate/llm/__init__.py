"""LLM adapters and the live model-chain factory."""

from __future__ import annotations

from automigrate.llm.base import LLMAdapter, LLMConfigurationError, LLMError
from automigrate.llm.chain import (
    DEFAULT_MODELS,
    ModelChainLLM,
    available_models,
    build_model_chain,
    create_provider_chain,
)
from automigrate.llm.keys import (
    MISSING_KEY_MESSAGE,
    MissingKeyError,
    resolve_api_key,
    select_default_provider,
)
from automigrate.llm.models import CheckResult, ModelListing, check_model, list_models
from automigrate.llm.openai import OpenAILLM
from automigrate.llm.providers import (
    PROVIDER_IDS,
    PROVIDER_LIST,
    PROVIDERS,
    Provider,
    get_provider,
)
from automigrate.llm.recording import RecordingLLM

API_KEY_MESSAGE = MISSING_KEY_MESSAGE


def require_api_key(provider: str | Provider | None = None) -> None:
    try:
        if provider is None:
            resolve_api_key(None)
        else:
            resolve_api_key(provider, None)
    except MissingKeyError as exc:
        raise LLMConfigurationError(str(exc)) from exc


def create_adapter(
    model: str | None = None, api_key: str | None = None
) -> LLMAdapter:
    resolved = resolve_api_key(api_key)
    return ModelChainLLM(build_model_chain(model), api_key=resolved)


__all__ = [
    "API_KEY_MESSAGE",
    "DEFAULT_MODELS",
    "LLMAdapter",
    "LLMConfigurationError",
    "LLMError",
    "MissingKeyError",
    "ModelChainLLM",
    "ModelListing",
    "CheckResult",
    "OpenAILLM",
    "PROVIDER_IDS",
    "PROVIDER_LIST",
    "PROVIDERS",
    "Provider",
    "RecordingLLM",
    "available_models",
    "build_model_chain",
    "check_model",
    "create_adapter",
    "create_provider_chain",
    "get_provider",
    "list_models",
    "require_api_key",
    "resolve_api_key",
    "select_default_provider",
]
