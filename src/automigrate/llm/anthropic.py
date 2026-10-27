"""Anthropic Claude adapter using the native SDK."""

from __future__ import annotations

import os
from typing import Any

from automigrate.llm.base import (
    LLMAdapter,
    LLMConfigurationError,
    LLMError,
    http_status_code,
    resolve_model,
)

DEFAULT_MODEL = "claude-3-5-sonnet-latest"


class AnthropicLLM(LLMAdapter):
    name = "anthropic"

    def __init__(
        self,
        model: str | None = None,
        *,
        client: Any | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        provider: str | None = None,
    ) -> None:
        key = api_key.strip() if api_key is not None and api_key.strip() else None
        if client is None and key is None:
            environment_key = os.environ.get("ANTHROPIC_API_KEY")
            key = (
                environment_key.strip()
                if environment_key is not None and environment_key.strip()
                else None
            )
        if client is None and key is None:
            raise LLMConfigurationError("ANTHROPIC_API_KEY is not set")
        self.provider = provider or "anthropic"
        self.model = resolve_model(
            DEFAULT_MODEL, "AUTOMIGRATE_ANTHROPIC_MODEL", model
        )
        if client is not None:
            self._client = client
            return
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise LLMConfigurationError(
                "The anthropic package is not installed; install automigrate[anthropic]"
            ) from exc
        options: dict[str, Any] = {"api_key": key}
        if base_url:
            options["base_url"] = base_url
        self._client = Anthropic(**options)

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
            raise LLMError(
                f"Anthropic request failed: {exc}",
                status_code=http_status_code(exc),
            ) from exc
        parts = [block.text for block in message.content if getattr(block, "text", None)]
        return "\n".join(parts).strip()
