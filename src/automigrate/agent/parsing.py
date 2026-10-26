"""Strict helpers for parsing untrusted LLM output."""

from __future__ import annotations

import json
import re
from typing import Any

FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class LLMParseError(ValueError):
    pass


def strip_code_fences(text: str) -> str:
    stripped = text.strip()
    match = FENCE_RE.search(stripped)
    if match:
        return match.group(1).strip()
    return stripped


def parse_json_object(text: str) -> dict[str, Any]:
    candidate = strip_code_fences(text)
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMParseError("response did not contain a JSON object")
    try:
        data = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMParseError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMParseError("response JSON was not an object")
    return data
