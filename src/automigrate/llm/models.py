"""Discover model ids from a provider's list-models endpoint.

Automated tests inject a fake client or HTTP transport; this module never
performs a live request during the test suite.  A failed listing falls back to
the small registry default list and returns a sanitized reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NamedTuple

from automigrate.llm.base import http_status_code
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


FATAL_MODEL_STATUSES = frozenset({401, 403, 404, 429})


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a one-token model availability check."""

    ok: bool
    status_code: int | None = None
    error: str | None = None

    def __bool__(self) -> bool:
        return self.ok

    @property
    def fatal(self) -> bool:
        return not self.ok and self.status_code in FATAL_MODEL_STATUSES


def _messages_url(provider: Provider, base_url: str | None) -> str:
    base = (base_url or provider.base_url or "").rstrip("/")
    if not base:
        raise ValueError("this provider needs an explicit base URL")
    if provider.sdk == "anthropic":
        return f"{base}/messages" if base.endswith("/v1") else f"{base}/v1/messages"
    return f"{base}/chat/completions"


def _check_payload(provider: Provider, model: str) -> dict[str, Any]:
    if provider.sdk == "anthropic":
        return {
            "model": model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        }
    return {
        "model": model,
        "max_tokens": 1,
        "temperature": 0,
        "messages": [{"role": "user", "content": "ping"}],
    }


def _check_with_client(provider: Provider, model: str, client: Any) -> None:
    chat = getattr(client, "chat", None)
    completions = getattr(chat, "completions", None)
    create = getattr(completions, "create", None)
    if callable(create):
        create(**_check_payload(provider, model))
        return
    messages = getattr(client, "messages", None)
    create = getattr(messages, "create", None)
    if callable(create):
        create(**_check_payload(provider, model))
        return
    post = getattr(client, "post", None)
    if callable(post):
        response = post(
            _messages_url(provider, None),
            headers=_headers(provider, client.api_key if hasattr(client, "api_key") else ""),
            json=_check_payload(provider, model),
        )
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
        return
    raise TypeError("fake client exposes no chat/messages/post method")


def check_model(
    provider: str | Provider,
    model: str,
    key: str | None,
    *,
    client: Any | None = None,
    transport: Any | None = None,
    base_url: str | None = None,
    timeout: float = 30.0,
) -> CheckResult:
    """Send the smallest valid request and report whether the key/model work.

    A 401, 403, 404, or 429 is fatal for the run.  Provider error text is
    returned with the key redacted; the key is never logged here.
    """

    selected = get_provider(provider)
    secret = key or ""
    if client is None and not secret.strip():
        return CheckResult(False, None, "no API key provided")
    try:
        if client is not None:
            _check_with_client(selected, model, client)
            return CheckResult(True)
        http = _http_module()
        with http.Client(timeout=timeout, transport=transport) as http_client:
            response = http_client.post(
                _messages_url(selected, base_url),
                headers=_headers(selected, secret),
                json=_check_payload(selected, model),
            )
            status = getattr(response, "status_code", None)
            if isinstance(status, int) and 200 <= status < 300:
                return CheckResult(True, status)
            body = getattr(response, "text", "")
            return CheckResult(False, status, redact_secret(str(body), secret))
    except Exception as exc:  # noqa: BLE001 - callers decide whether it is fatal
        status = http_status_code(exc)
        return CheckResult(False, status, redact_secret(str(exc), secret))
