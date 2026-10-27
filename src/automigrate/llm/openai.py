"""OpenAI-compatible adapter."""

from __future__ import annotations

import os
from typing import Any

from automigrate.llm.base import (
    LLMAdapter,
    LLMConfigurationError,
    LLMError,
    resolve_model,
)

DEFAULT_MODEL = "gpt-4o-mini"


class OpenAILLM(LLMAdapter):
    name = "openai"

    def __init__(
        self,
        model: str | None = None,
        *,
        client: Any | None = None,
        api_key: str | None = None,
    ) -> None:
        key = api_key.strip() if api_key is not None and api_key.strip() else None
        if client is None and key is None:
            environment_key = os.environ.get("OPENAI_API_KEY")
            key = (
                environment_key.strip()
                if environment_key is not None and environment_key.strip()
                else None
            )
        if client is None and key is None:
            raise LLMConfigurationError("OPENAI_API_KEY is not set")
        self.model = resolve_model(DEFAULT_MODEL, "AUTOMIGRATE_OPENAI_MODEL", model)
        if client is not None:
            self._client = client
            return
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMConfigurationError(
                "The openai package is not installed; install automigrate[openai]"
            ) from exc
        base_url = os.environ.get("OPENAI_BASE_URL")
        options: dict[str, Any] = {"api_key": key}
        if base_url:
            options["base_url"] = base_url
        self._client = OpenAI(**options)

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        stage: str | None = None,
        iteration: int = 0,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0,
            )
        except Exception as exc:
            raise LLMError(f"OpenAI request failed: {exc}") from exc
        return (response.choices[0].message.content or "").strip()
