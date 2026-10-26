"""Common interface implemented by every LLM adapter."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMResponse(Protocol):
    text: str


@runtime_checkable
class LLMAdapter(Protocol):
    """Minimal adapter contract used by the agents."""

    name: str

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        """Return the model's raw text response for a prompt."""
        ...
