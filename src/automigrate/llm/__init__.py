"""LLM adapters and adapter factory."""

from __future__ import annotations

from pathlib import Path

from automigrate.llm.anthropic import AnthropicLLM
from automigrate.llm.base import LLMAdapter, LLMConfigurationError, LLMError
from automigrate.llm.mock import MockLLM
from automigrate.llm.openai import OpenAILLM


def create_adapter(
    name: str,
    *,
    case_dir: str | Path | None = None,
) -> LLMAdapter:
    normalized = name.strip().lower()
    if normalized == "mock":
        return MockLLM(case_dir)
    if normalized == "anthropic":
        return AnthropicLLM()
    if normalized == "openai":
        return OpenAILLM()
    raise LLMConfigurationError(f"unknown LLM provider: {name}")


__all__ = [
    "AnthropicLLM",
    "LLMAdapter",
    "LLMConfigurationError",
    "LLMError",
    "MockLLM",
    "OpenAILLM",
    "create_adapter",
]
