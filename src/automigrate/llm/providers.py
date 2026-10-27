"""Registry of supported LLM providers.

Every provider is described by a stable id, a human-readable name, the
environment variable that holds its API key, the endpoint/SDK it uses, and a
small fallback model list.  Model names are normally discovered from the
provider's list-models endpoint; the fallback list is only used when that
request fails.

Base URLs below are marked "verify against provider docs" because vendors do
change their OpenAI-compatible endpoints.  Do not silently guess a new URL:
update this registry and the documentation together.
"""

from __future__ import annotations

from dataclasses import dataclass

OPENAI_SDK = "openai"
ANTHROPIC_SDK = "anthropic"


@dataclass(frozen=True)
class Provider:
    """One selectable provider."""

    id: str
    display_name: str
    key_env: str
    base_url: str | None
    sdk: str
    default_models: tuple[str, ...]

    # Friendly aliases used by callers and tests.
    @property
    def name(self) -> str:
        return self.display_name

    @property
    def key_env_var(self) -> str:
        return self.key_env

    @property
    def env_var(self) -> str:
        return self.key_env

    @property
    def models(self) -> tuple[str, ...]:
        return self.default_models

    @property
    def is_openai_compatible(self) -> bool:
        return self.sdk == OPENAI_SDK


# Order matters: it is used when no provider is chosen and more than one key
# is present in the environment.
PROVIDER_LIST: tuple[Provider, ...] = (
    Provider(
        id="gemini",
        display_name="Google Gemini",
        key_env="GEMINI_API_KEY",
        # verify against provider docs
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        sdk=OPENAI_SDK,
        default_models=(
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-2.0-flash",
        ),
    ),
    Provider(
        id="openai",
        display_name="OpenAI",
        key_env="OPENAI_API_KEY",
        # verify against provider docs
        base_url="https://api.openai.com/v1",
        sdk=OPENAI_SDK,
        default_models=("gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"),
    ),
    Provider(
        id="anthropic",
        display_name="Anthropic Claude",
        key_env="ANTHROPIC_API_KEY",
        # verify against provider docs
        base_url="https://api.anthropic.com",
        sdk=ANTHROPIC_SDK,
        default_models=(
            "claude-3-5-sonnet-latest",
            "claude-3-5-haiku-latest",
            "claude-3-opus-latest",
        ),
    ),
    Provider(
        id="groq",
        display_name="Groq",
        key_env="GROQ_API_KEY",
        # verify against provider docs
        base_url="https://api.groq.com/openai/v1",
        sdk=OPENAI_SDK,
        default_models=(
            "openai/gpt-oss-120b",
            "qwen/qwen3.8-27b",
            "llama-3.3-70b-versatile",
        ),
    ),
    Provider(
        id="qwen",
        display_name="Alibaba DashScope (Qwen)",
        key_env="DASHSCOPE_API_KEY",
        # verify against provider docs
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        sdk=OPENAI_SDK,
        default_models=("qwen-plus", "qwen-turbo", "qwen-max"),
    ),
    Provider(
        id="deepseek",
        display_name="DeepSeek",
        key_env="DEEPSEEK_API_KEY",
        # verify against provider docs
        base_url="https://api.deepseek.com/v1",
        sdk=OPENAI_SDK,
        default_models=("deepseek-chat", "deepseek-reasoner"),
    ),
    Provider(
        id="xai",
        display_name="xAI Grok",
        key_env="XAI_API_KEY",
        # verify against provider docs
        base_url="https://api.x.ai/v1",
        sdk=OPENAI_SDK,
        default_models=("grok-2-latest", "grok-beta"),
    ),
    Provider(
        id="mistral",
        display_name="Mistral AI",
        key_env="MISTRAL_API_KEY",
        # verify against provider docs
        base_url="https://api.mistral.ai/v1",
        sdk=OPENAI_SDK,
        default_models=(
            "mistral-large-latest",
            "mistral-small-latest",
            "open-mistral-nemo",
        ),
    ),
    Provider(
        id="openrouter",
        display_name="OpenRouter",
        key_env="OPENROUTER_API_KEY",
        # verify against provider docs
        base_url="https://openrouter.ai/api/v1",
        sdk=OPENAI_SDK,
        default_models=(
            "openai/gpt-4o-mini",
            "anthropic/claude-3.5-sonnet",
            "google/gemini-2.5-flash",
        ),
    ),
    Provider(
        id="custom",
        display_name="Custom OpenAI-compatible endpoint",
        key_env="AUTOMIGRATE_CUSTOM_API_KEY",
        base_url=None,
        sdk=OPENAI_SDK,
        default_models=(),
    ),
)

PROVIDERS: dict[str, Provider] = {provider.id: provider for provider in PROVIDER_LIST}
PROVIDER_IDS: tuple[str, ...] = tuple(provider.id for provider in PROVIDER_LIST)
DEFAULT_PROVIDER_ID = "gemini"


class UnknownProviderError(ValueError):
    """Raised when a provider id is not present in the registry."""

    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id
        known = ", ".join(PROVIDER_IDS)
        super().__init__(f"Unknown provider {provider_id!r}. Choose one of: {known}")


def get_provider(provider: str | Provider | None = None) -> Provider:
    """Return a registry entry by id, or the default provider."""

    if isinstance(provider, Provider):
        return provider
    provider_id = provider or DEFAULT_PROVIDER_ID
    try:
        return PROVIDERS[provider_id]
    except KeyError as exc:
        raise UnknownProviderError(provider_id) from exc


def all_providers() -> tuple[Provider, ...]:
    return PROVIDER_LIST


def env_keys() -> dict[str, str]:
    """Map provider id to its key environment variable (never its value)."""

    return {provider.id: provider.key_env for provider in PROVIDER_LIST}
