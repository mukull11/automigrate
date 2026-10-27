"""LLM adapters and the live model-chain factory."""

from __future__ import annotations

import os

from automigrate.llm.base import LLMAdapter, LLMConfigurationError, LLMError
from automigrate.llm.chain import (
    DEFAULT_MODELS,
    ModelChainLLM,
    available_models,
    build_model_chain,
)
from automigrate.llm.openai import OpenAILLM
from automigrate.llm.recording import RecordingLLM

API_KEY_MESSAGE = (
    "OPENAI_API_KEY is not set. AutoMigrate only runs live models; set "
    "OPENAI_API_KEY (and optional OPENAI_BASE_URL) and see docs/real_llm.md."
)


def require_api_key() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise LLMConfigurationError(API_KEY_MESSAGE)


def create_adapter(model: str | None = None) -> LLMAdapter:
    require_api_key()
    return ModelChainLLM(build_model_chain(model))


__all__ = [
    "API_KEY_MESSAGE",
    "DEFAULT_MODELS",
    "LLMAdapter",
    "LLMConfigurationError",
    "LLMError",
    "ModelChainLLM",
    "OpenAILLM",
    "RecordingLLM",
    "available_models",
    "build_model_chain",
    "create_adapter",
    "require_api_key",
]
