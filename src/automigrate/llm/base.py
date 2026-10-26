"""Common interface implemented by every LLM adapter."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod


class LLMError(RuntimeError):
    pass


class LLMConfigurationError(LLMError):
    pass


def resolve_model(
    default: str,
    provider_env: str,
    explicit: str | None = None,
) -> str:
    """Resolve a model name from explicit input, env vars, then the default.

    ``AUTOMIGRATE_MODEL`` is a provider-agnostic override, so a Google AI Studio
    model can be selected for the OpenAI-compatible endpoint without code changes.
    """

    return (
        explicit
        or os.environ.get("AUTOMIGRATE_MODEL")
        or os.environ.get(provider_env)
        or default
    )


class LLMAdapter(ABC):
    """Minimal adapter contract used by the agents."""

    name: str

    @abstractmethod
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        stage: str | None = None,
        iteration: int = 0,
    ) -> str:
        """Return the model's raw text response for a prompt."""
