"""Deterministic mock LLM used by tests and benchmark evaluation."""

from __future__ import annotations

import json
import re
from pathlib import Path

from automigrate.llm.base import LLMAdapter

EXPECTED_DIAGNOSIS_RE = re.compile(
    r"^\s*expected_diagnosis\s*:\s*[\"']?([A-Z_]+)", re.MULTILINE
)


class MockLLM(LLMAdapter):
    name = "mock"

    def __init__(
        self,
        case_dir: str | Path | None = None,
        *,
        plan_text: str | None = None,
        patch_text: str | None = None,
        diagnosis_text: str | None = None,
    ) -> None:
        self.case_dir = Path(case_dir) if case_dir else None
        self.plan_text = plan_text
        self.patch_text = patch_text
        self.diagnosis_text = diagnosis_text
        self.calls: list[tuple[str | None, int]] = []

    def _read(self, *names: str) -> str | None:
        if self.case_dir is None:
            return None
        for name in names:
            path = self.case_dir / name
            if path.is_file():
                return path.read_text(encoding="utf-8")
        return None

    def _expected_diagnosis(self) -> str:
        if self.case_dir is None:
            return "INSUFFICIENT_EVIDENCE"
        case_file = self.case_dir / "case.yaml"
        if case_file.is_file():
            match = EXPECTED_DIAGNOSIS_RE.search(case_file.read_text(encoding="utf-8"))
            if match:
                return match.group(1)
        return "INSUFFICIENT_EVIDENCE"

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        stage: str | None = None,
        iteration: int = 0,
    ) -> str:
        self.calls.append((stage, iteration))
        normalized = (stage or "").upper()
        if normalized == "PLAN":
            text = self.plan_text or self._read(
                f"plan_{iteration}.json", "plan.json"
            )
            if text is not None:
                return text
            return json.dumps(
                {
                    "summary": "No safe change identified",
                    "changes": [],
                    "expected_effect": "No change",
                }
            )
        if normalized == "PATCH":
            text = self.patch_text or self._read(
                f"patch_{iteration}.txt", "patch.txt"
            )
            return text or ""
        if normalized == "DIAGNOSE":
            text = self.diagnosis_text or self._read(
                f"diagnosis_{iteration}.json", "diagnosis.json"
            )
            if text is not None:
                return text
            return json.dumps(
                {
                    "diagnosis": self._expected_diagnosis(),
                    "reason": "Deterministic mock diagnosis from the benchmark case.",
                }
            )
        raise ValueError(f"MockLLM does not support stage: {stage}")
