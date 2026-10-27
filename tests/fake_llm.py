"""Test-only fake model client and API detector. Never imported by the CLI."""

from __future__ import annotations

import json
from pathlib import Path

from automigrate.apidiff.detector import ApiDiffBundle, InstalledDocument
from automigrate.apidiff.diff import diff_apis
from automigrate.apidiff.models import ApiDiff
from automigrate.apidiff.symbols import extract_symbols
from automigrate.llm.base import LLMAdapter

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rename_sample"


class FakeLLM(LLMAdapter):
    name = "fake"

    def __init__(
        self,
        *,
        plan_text: str | None = None,
        patch_text: str | None = None,
        diagnosis_text: str | None = None,
    ) -> None:
        self.plan_text = plan_text
        self.patch_text = patch_text
        self.diagnosis_text = diagnosis_text
        self.calls: list[tuple[str | None, int]] = []

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
            return self.plan_text or json.dumps(
                {"summary": "no change", "changes": [], "expected_effect": "none"}
            )
        if normalized == "PATCH":
            return self.patch_text or ""
        if normalized == "DIAGNOSE":
            return self.diagnosis_text or json.dumps(
                {"diagnosis": "MIGRATION_RELATED", "reason": "test fixture"}
            )
        raise ValueError(f"FakeLLM does not support stage: {stage}")


class StaticApiDetector:
    def __init__(
        self,
        diff: ApiDiff,
        documents: list[InstalledDocument] | None = None,
    ) -> None:
        self.diff = diff
        self.documents = documents or []

    def detect(
        self,
        dependency: str,
        current_version: str,
        target_version: str,
        workspace: str | Path,
    ) -> ApiDiffBundle:
        return ApiDiffBundle(diff=self.diff, documents=self.documents)


class LocalApiDetector:
    """Build a diff from two local package trees without network access."""

    def __init__(
        self,
        old_root: str | Path,
        new_root: str | Path,
        *,
        dependency: str = "lib",
    ) -> None:
        self.old_root = Path(old_root)
        self.new_root = Path(new_root)
        self.dependency = dependency

    def detect(
        self,
        dependency: str,
        current_version: str,
        target_version: str,
        workspace: str | Path,
    ) -> ApiDiffBundle:
        diff = diff_apis(
            extract_symbols(self.old_root),
            extract_symbols(self.new_root),
            dependency=dependency,
            current_version=current_version,
            target_version=target_version,
        )
        return ApiDiffBundle(diff=diff)


def fixture_plan() -> str:
    return (FIXTURES / "plan.json").read_text(encoding="utf-8")


def fixture_patch() -> str:
    return (FIXTURES / "patch.txt").read_text(encoding="utf-8")


def rename_detector() -> LocalApiDetector:
    return LocalApiDetector(
        FIXTURES / "fake_lib" / "old_version",
        FIXTURES / "fake_lib" / "new_version",
    )
