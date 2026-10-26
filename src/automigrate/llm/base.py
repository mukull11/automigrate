"""Common interface implemented by every LLM adapter."""

from __future__ import annotations

from abc import ABC, abstractmethod


class LLMError(RuntimeError):
    pass


class LLMConfigurationError(LLMError):
    pass


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
