"""OpenAI adapter."""

from __future__ import annotations

import os

from automigrate.llm.base import LLMAdapter, LLMConfigurationError, LLMError

DEFAULT_MODEL = "gpt-4o-mini"


class OpenAILLM(LLMAdapter):
    name = "openai"

    def __init__(self, model: str | None = None) -> None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise LLMConfigurationError("OPENAI_API_KEY is not set")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMConfigurationError(
                "The openai package is not installed; install automigrate[openai]"
            ) from exc
        self.model = model or os.environ.get("AUTOMIGRATE_OPENAI_MODEL", DEFAULT_MODEL)
        self._client = OpenAI(api_key=api_key)

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
