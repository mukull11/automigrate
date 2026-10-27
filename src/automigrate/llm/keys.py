"""Resolve the OpenAI-compatible API key from explicit input or the environment."""

from __future__ import annotations

import os

MISSING_KEY_MESSAGE = (
    "No API key provided. Set OPENAI_API_KEY in the environment, pass --api-key "
    "on the CLI, or enter an API key in the Streamlit UI. See docs/real_llm.md."
)


class MissingKeyError(Exception):
    """Raised when neither an explicit key nor OPENAI_API_KEY is available."""

    def __init__(self) -> None:
        super().__init__(MISSING_KEY_MESSAGE)


def resolve_api_key(explicit: str | None) -> str:
    """Return the first non-empty key from explicit input, then the environment.

    The returned value is intended only for constructing a client. Callers must
    never log, persist, or place it in ``os.environ``.
    """

    if explicit is not None and explicit.strip():
        return explicit.strip()
    environment = os.environ.get("OPENAI_API_KEY")
    if environment is not None and environment.strip():
        return environment.strip()
    raise MissingKeyError()
