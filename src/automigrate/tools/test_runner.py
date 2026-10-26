"""pytest execution with JSON report parsing."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from automigrate.state import TestRunResult
from automigrate.tools.subprocess import SubprocessRunner


def _failed_items(report: dict) -> list[dict]:
    items = [
        item
        for item in report.get("tests", [])
        if item.get("outcome") in {"failed", "error"}
    ]
    items.extend(
        item
        for item in report.get("collectors", [])
        if item.get("outcome") in {"failed", "error"}
    )
    return items


def _first_traceback(report: dict) -> str | None:
    for test in _failed_items(report):
        for phase in ("call", "setup", "teardown"):
            phase_data = test.get(phase)
            if not isinstance(phase_data, dict):
                continue
            crash = phase_data.get("crash")
            if isinstance(crash, dict) and crash.get("longrepr"):
                return str(crash["longrepr"])
            if phase_data.get("longrepr"):
                return str(phase_data["longrepr"])
        if test.get("longrepr"):
            return str(test["longrepr"])
    return None


def parse_pytest_report(
    report: dict,
    *,
    duration_s: float = 0.0,
    ruff_issue_count: int = 0,
) -> TestRunResult:
    summary = report.get("summary", {})
    failed_items = _failed_items(report)
    collector_errors = [
        item
        for item in report.get("collectors", [])
        if item.get("outcome") in {"failed", "error"}
    ]
    failed_names = [
        str(item.get("nodeid", "")) for item in failed_items if item.get("nodeid")
    ]
    return TestRunResult(
        passed=int(summary.get("passed", 0)),
        failed=int(summary.get("failed", 0)),
        errors=int(summary.get("error", 0))
        + int(summary.get("errors", 0))
        + len(collector_errors),
        failed_test_names=failed_names,
        first_traceback=_first_traceback(report),
        ruff_issue_count=ruff_issue_count,
        duration_s=float(report.get("duration", duration_s) or duration_s),
    )


class PytestRunner:
    def __init__(self, runner: SubprocessRunner) -> None:
        self.runner = runner

    def run(self, targets: list[str] | None = None) -> TestRunResult:
        with tempfile.TemporaryDirectory(prefix="automigrate-pytest-") as temp_dir:
            report_path = Path(temp_dir) / "report.json"
            args = [
                "-q",
                "-p",
                "no:cacheprovider",
                "--json-report",
                f"--json-report-file={report_path}",
            ]
            if targets:
                args.extend(targets)
            result = self.runner.run("pytest", args)
            if not report_path.is_file():
                return TestRunResult(
                    errors=1,
                    first_traceback=result.stderr or result.stdout,
                    duration_s=result.duration_s,
                )
            data = json.loads(report_path.read_text(encoding="utf-8"))
        return parse_pytest_report(data, duration_s=result.duration_s)
