"""Discover model ids from a provider's list-models endpoint.

Automated tests inject a fake client or HTTP transport; this module never
performs a live request during the test suite.  A failed listing falls back to
the small registry default list and returns a sanitized reason.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from automigrate.llm.providers import Provider, get_provider

# Model ids containing one of these fragments are not usable for chat planning.
_NON_TEXT_MARKERS = (
    "embed",
    "whisper",
    "tts",
    "text-to-speech",
    "dall-e",
    "image",
    "images",
    "audio",
    "realtime",
    "transcribe",
    "moderation",
    "rerank",
    "clip",
)
_NON_TEXT_TYPES = {
    "embedding",
    "embeddings",
    "image",
    "audio",
    "moderation",
    "rerank",
}


class ModelListing(NamedTuple):
    """Sorted model ids plus an optional, key-free failure reason."""

    models: list[str]
    error: str | None = None

    @property
    def used_defaults(self) -> bool:
        return self.error is not None


def redact_secret(text: str, secret: str | None) -> str:
    """Remove an API key from arbitrary provider error text."""

    cleaned = text or ""
    if secret is not None and secret.strip():
        cleaned = cleaned.replace(secret, "[REDACTED]")
    return cleaned.replace("\r", " ").replace("\n", " ").strip()[:500]


def _is_text_model(model_id: str, entry: Any = None) -> bool:
    if entry is not None:
        if isinstance(entry, dict):
            for key in ("type", "model_type", "kind"):
                value = entry.get(key)
                if isinstance(value, str) and value.lower() in _NON_TEXT_TYPES:
                    return False
        else:
            for attribute in ("type", "model_type", "kind"):
                value = getattr(entry, attribute, None)
                if isinstance(value, str) and value.lower() in _NON_TEXT_TYPES:
                    return False
    lowered = model_id.lower()
    return not any(marker in lowered for marker in _NON_TEXT_MARKERS)


def _entry_id(entry: Any) -> str | None:
    if isinstance(entry, dict):
        value = entry.get("id") or entry.get("name") or entry.get("model")
    else:
        value = getattr(entry, "id", None) or getattr(entry, "name", None)
        if value is None:
            value = getattr(entry, "model", None)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().removeprefix("models/")


def _entries(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "models", "items", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    for attribute in ("data", "models", "items", "results"):
        value = getattr(payload, attribute, None)
        if isinstance(value, list):
            return value
    return []


def _model_ids(payload: Any) -> list[str]:
    ids: set[str] = set()
    for entry in _entries(payload):
        model_id = _entry_id(entry)
        if model_id is not None and _is_text_model(model_id, entry):
            ids.add(model_id)
    return sorted(ids)


def _headers(provider: Provider, key: str) -> dict[str, str]:
    if provider.sdk == "anthropic":
        return {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _models_url(provider: Provider, base_url: str | None) -> str:
    base = (base_url or provider.base_url or "").rstrip("/")
    if not base:
        raise ValueError("this provider needs an explicit base URL")
    suffix = "/v1/models" if provider.sdk == "anthropic" else "/models"
    if provider.sdk == "anthropic" and base.endswith("/v1"):
        suffix = "/models"
    return f"{base}{suffix}"


def _client_listing(
    provider: Provider,
    key: str,
    *,
    client: Any,
    base_url: str | None,
) -> list[str]:
    models = getattr(client, "models", None)
    list_method = getattr(models, "list", None)
    if callable(list_method):
        return _model_ids(list_method())
    get_method = getattr(client, "get", None)
    if callable(get_method):
        response = get_method(
            _models_url(provider, base_url),
            headers=_headers(provider, key),
        )
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
        return _model_ids(response.json())
    raise TypeError("fake client exposes neither models.list() nor get()")


def _http_module() -> Any:
    try:
        import httpx2 as http  # newer openai releases vendor httpx under this name
    except ImportError:  # pragma: no cover - older environments
        import httpx as http
    return http


def _http_listing(
    provider: Provider,
    key: str,
    *,
    base_url: str | None,
    transport: Any,
    timeout: float,
) -> list[str]:
    http = _http_module()

    with http.Client(timeout=timeout, transport=transport) as client:
        response = client.get(
            _models_url(provider, base_url),
            headers=_headers(provider, key),
        )
        response.raise_for_status()
        return _model_ids(response.json())


def list_models(
    provider: str | Provider,
    key: str | None,
    *,
    client: Any | None = None,
    transport: Any | None = None,
    base_url: str | None = None,
    timeout: float = 30.0,
) -> ModelListing:
    """Return ``(sorted_model_ids, error)`` for a provider.

    The list endpoint is used when a key is available.  Any failure returns the
    provider's registry defaults and a reason that never contains the key.
    """

    selected = get_provider(provider)
    defaults = sorted(set(selected.default_models))
    if key is None or not key.strip():
        return ModelListing(defaults, "no API key provided")
    try:
        if client is not None:
            ids = _client_listing(
                selected, key, client=client, base_url=base_url
            )
        else:
            ids = _http_listing(
                selected,
                key,
                base_url=base_url,
                transport=transport,
                timeout=timeout,
            )
        if not ids:
            raise ValueError("provider returned no text/chat models")
        return ModelListing(ids, None)
    except Exception as exc:  # noqa: BLE001 - listing failures are non-fatal
        reason = redact_secret(str(exc) or type(exc).__name__, key)
        return ModelListing(defaults, reason)
