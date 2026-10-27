"""Resolve a per-provider API key from explicit input or the environment."""

from __future__ import annotations

import os

from automigrate.llm.providers import (
    DEFAULT_PROVIDER_ID,
    PROVIDER_LIST,
    PROVIDERS,
    Provider,
    UnknownProviderError,
    get_provider,
)

DOCS_PATH = "docs/real_llm.md"

MISSING_KEY_MESSAGE = (
    "No API key provided. Set OPENAI_API_KEY in the environment, pass --api-key "
    "on the CLI, or enter an API key in the Streamlit UI. See docs/real_llm.md."
)


class MissingKeyError(Exception):
    """Raised when neither an explicit key nor the provider's env key is set."""

    def __init__(
        self,
        provider: str | Provider | None = None,
        *,
        provider_id: str | None = None,
        list_providers: bool = False,
    ) -> None:
        if provider_id is not None:
            selected: Provider | None = PROVIDERS.get(provider_id)
        elif provider is None:
            selected = PROVIDERS.get("openai")
        else:
            try:
                selected = get_provider(provider)
            except UnknownProviderError:
                selected = None
        self.provider = selected
        self.provider_id = selected.id if selected else (provider_id or "")
        self.env_var = selected.key_env if selected else ""
        self.key_env = self.env_var
        if list_providers or selected is None:
            choices = ", ".join(
                f"{item.id} ({item.key_env})" for item in PROVIDER_LIST
            )
            message = (
                "No API key provided. Choose a provider and set its key "
                f"environment variable: {choices}. Or pass --api-key on the "
                f"CLI / enter a key in the Streamlit UI. See {DOCS_PATH}."
            )
        else:
            message = (
                f"No API key provided for {selected.display_name}. Set "
                f"{selected.key_env} in the environment, pass --api-key on the "
                f"CLI, or enter an API key in the Streamlit UI. See {DOCS_PATH}."
            )
        super().__init__(message)


def _environment_key(provider: Provider) -> str | None:
    value = os.environ.get(provider.key_env)
    if value is not None and value.strip():
        return value.strip()
    return None


def _coerce_arguments(
    provider: str | Provider | None, explicit: str | None
) -> tuple[Provider, str | None]:
    """Support both ``resolve_api_key(explicit)`` and the provider-aware form."""

    if not isinstance(provider, Provider):
        if (
            explicit is None
            and provider is not None
            and provider not in PROVIDERS
        ):
            # Legacy single-argument call: the value is the explicit key and
            # the default provider is OpenAI.
            explicit = provider
            provider = "openai"
    return get_provider(provider or "openai"), explicit


def resolve_api_key(
    provider: str | Provider | None = None,
    explicit: str | None = None,
) -> str:
    """Return the first non-empty key from explicit input, then the environment.

    ``provider`` may be a provider id or a :class:`Provider`.  For backwards
    compatibility, a single non-provider argument is treated as an explicit
    key for the default provider.

    The returned value is intended only for constructing a client. Callers must
    never log, persist, or place it in ``os.environ``.
    """

    selected, explicit_key = _coerce_arguments(provider, explicit)
    if explicit_key is not None and explicit_key.strip():
        return explicit_key.strip()
    environment = _environment_key(selected)
    if environment is not None:
        return environment
    raise MissingKeyError(selected)


def provider_key_from_env(provider: str | Provider) -> str | None:
    """Return a provider's environment key without raising when it is absent."""

    return _environment_key(get_provider(provider))


def first_provider_with_key() -> Provider | None:
    """Return the first registry provider whose environment key is set."""

    for item in PROVIDER_LIST:
        if _environment_key(item) is not None:
            return item
    return None


def select_default_provider() -> Provider:
    """Choose Gemini when configured, else the first configured provider.

    ``custom`` is excluded because it always needs a user-supplied base URL.
    Raises :class:`MissingKeyError` that lists every provider and env var.
    """

    gemini = PROVIDERS[DEFAULT_PROVIDER_ID]
    if _environment_key(gemini) is not None:
        return gemini
    for item in PROVIDER_LIST:
        if item.id == "custom":
            continue
        if _environment_key(item) is not None:
            return item
    raise MissingKeyError(list_providers=True)
