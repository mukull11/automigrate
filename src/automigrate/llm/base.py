"""Common interface implemented by every LLM adapter."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod


class LLMError(RuntimeError):
    def __init__(self, message: str = "", *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMConfigurationError(LLMError):
    pass


def http_status_code(exc: BaseException) -> int | None:
    """Extract an HTTP status code from a provider SDK exception, if present."""

    for attribute in ("status_code", "http_status", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


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


# The blueprint calls this contract LLMClient; LLMAdapter is the historical name.
LLMClient = LLMAdapter
