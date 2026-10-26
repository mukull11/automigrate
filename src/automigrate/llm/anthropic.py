"""Anthropic Claude adapter."""

from __future__ import annotations

import os

from automigrate.llm.base import (
    LLMAdapter,
    LLMConfigurationError,
    LLMError,
    resolve_model,
)

DEFAULT_MODEL = "claude-3-5-sonnet-latest"


class AnthropicLLM(LLMAdapter):
    name = "anthropic"

    def __init__(self, model: str | None = None) -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise LLMConfigurationError("ANTHROPIC_API_KEY is not set")
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise LLMConfigurationError(
                "The anthropic package is not installed; install automigrate[anthropic]"
            ) from exc
        self.model = resolve_model(
            DEFAULT_MODEL, "AUTOMIGRATE_ANTHROPIC_MODEL", model
        )
        self._client = Anthropic(api_key=api_key)

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        stage: str | None = None,
        iteration: int = 0,
    ) -> str:
        try:
            message = self._client.messages.create(
                model=self.model,
                max_tokens=2048,
                temperature=0,
                system=system or "",
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            raise LLMError(f"Anthropic request failed: {exc}") from exc
        parts = [block.text for block in message.content if getattr(block, "text", None)]
        return "\n".join(parts).strip()
