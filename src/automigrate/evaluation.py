"""Benchmark execution and metrics for the self-contained cases."""

from __future__ import annotations

import csv
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from automigrate.graph import run_migration
from automigrate.tools.git import tree_checksum

CASE_REQUIRED_FIELDS = {
    "dependency",
    "from_version",
    "to_version",
    "expected_status",
    "expected_min_tests_fixed",
}


@dataclass
class CaseDefinition:
    name: str
    path: Path
    dependency: str
    from_version: str
    to_version: str
    expected_status: str
    expected_diagnosis: str | None
    expected_min_tests_fixed: int

    @property
    def repo(self) -> Path:
        return self.path / "repo"

    @property
    def docs(self) -> Path:
        return self.path / "docs"


@dataclass
class CaseResult:
    case: CaseDefinition
    actual_status: str
    actual_diagnosis: str | None
    baseline_passed: int
    baseline_failed: int
    final_passed: int
    final_failed: int
    iterations: int
    files_modified: int
    duration_s: float
    tests_unchanged: bool
    repo_unchanged: bool
    flag_reason: str
    passed: bool
    session_id: str

    def as_row(self) -> dict[str, Any]:
        return {
            "case": self.case.name,
            "dependency": self.case.dependency,
            "expected_status": self.case.expected_status,
            "actual_status": self.actual_status,
            "expected_diagnosis": self.case.expected_diagnosis or "",
            "actual_diagnosis": self.actual_diagnosis or "",
            "baseline_passed": self.baseline_passed,
            "baseline_failed": self.baseline_failed,
            "final_passed": self.final_passed,
            "final_failed": self.final_failed,
            "iterations": self.iterations,
            "files_modified": self.files_modified,
            "duration_s": round(self.duration_s, 3),
            "tests_unchanged": self.tests_unchanged,
            "repo_unchanged": self.repo_unchanged,
            "passed": self.passed,
            "flag_reason": self.flag_reason,
        }


def load_case(case_dir: str | Path) -> CaseDefinition:
    path = Path(case_dir)
    data: dict[str, str] = {}
    for line in (path / "case.yaml").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        data[key.strip()] = value.strip().strip("\"'")
    missing = CASE_REQUIRED_FIELDS - data.keys()
    if missing:
        raise ValueError(f"{path.name}/case.yaml is missing fields: {sorted(missing)}")
    diagnosis = data.get("expected_diagnosis") or None
    return CaseDefinition(
        name=path.name,
        path=path,
        dependency=data["dependency"],
        from_version=data["from_version"],
        to_version=data["to_version"],
        expected_status=data["expected_status"],
        expected_diagnosis=diagnosis,
        expected_min_tests_fixed=int(data["expected_min_tests_fixed"]),
    )


def discover_cases(cases_dir: str | Path) -> list[CaseDefinition]:
    root = Path(cases_dir)
    return [
        load_case(path)
        for path in sorted(root.iterdir())
        if path.is_dir() and (path / "case.yaml").is_file()
    ]


def _test_files(original: Path, workspace: Path) -> bool:
    tests_dir = original / "tests"
    if not tests_dir.is_dir():
        return True
    ignored = {"__pycache__", ".pytest_cache", ".ruff_cache"}
    for path in tests_dir.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(original)
        if any(part in ignored for part in relative.parts):
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        counterpart = workspace / relative
        if not counterpart.is_file():
            return False
        if path.read_bytes() != counterpart.read_bytes():
            return False
    return True


def _modified_files(original: Path, workspace: Path) -> int:
    ignored = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}

    def snapshot(root: Path) -> dict[str, bytes]:
        entries: dict[str, bytes] = {}
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if not path.is_file():
                continue
            if any(part in ignored for part in relative.parts):
                continue
            if path.suffix in {".pyc", ".pyo"}:
                continue
            entries[relative.as_posix()] = path.read_bytes()
        return entries

    before = snapshot(original)
    after = snapshot(workspace)
    return len(
        {
            name
            for name in set(before) | set(after)
            if before.get(name) != after.get(name)
        }
    )


def run_case(case: CaseDefinition, llm: str = "mock") -> CaseResult:
    started = time.perf_counter()
    repo_before = tree_checksum(case.repo)
    with tempfile.TemporaryDirectory(prefix=f"automigrate-eval-{case.name}-") as tmp:
        result = run_migration(
            case.repo,
            case.dependency,
            case.from_version,
            case.to_version,
            docs_dir=case.docs,
            llm=llm,
            outputs_dir=Path(tmp) / "outputs",
            db_path=Path(tmp) / "automigrate.db",
        )
        duration = time.perf_counter() - started
        workspace = result.workspace_path
        state = result.state
        tests_unchanged = _test_files(case.repo, workspace)
        repo_unchanged = tree_checksum(case.repo) == repo_before
        files_modified = _modified_files(case.repo, workspace)

    actual_status = state.status.value
    actual_diagnosis = state.diagnosis.value if state.diagnosis else None
    status_ok = actual_status == case.expected_status
    diagnosis_ok = (
        case.expected_diagnosis is None or actual_diagnosis == case.expected_diagnosis
    )
    fixed = max(0, state.latest.passed - state.baseline.passed)
    fixed_ok = fixed >= case.expected_min_tests_fixed
    unresolved = state.latest.failed + state.latest.errors
    honest_outcome = case.expected_status == "SUCCESSFUL" or unresolved > 0
    passed = (
        status_ok
        and diagnosis_ok
        and fixed_ok
        and tests_unchanged
        and repo_unchanged
        and honest_outcome
    )
    return CaseResult(
        case=case,
        actual_status=actual_status,
        actual_diagnosis=actual_diagnosis,
        baseline_passed=state.baseline.passed,
        baseline_failed=state.baseline.failed + state.baseline.errors,
        final_passed=state.latest.passed,
        final_failed=unresolved,
        iterations=state.iteration,
        files_modified=files_modified,
        duration_s=duration,
        tests_unchanged=tests_unchanged,
        repo_unchanged=repo_unchanged,
        flag_reason=state.flag_reason or "",
        passed=passed,
        session_id=result.session_id,
    )


def _rate(passed: int, failed: int) -> float:
    total = passed + failed
    return passed / total if total else 0.0


def _metrics(results: list[CaseResult]) -> dict[str, Any]:
    total = len(results)
    if not total:
        return {}
    baseline_passed = sum(item.baseline_passed for item in results)
    baseline_failed = sum(item.baseline_failed for item in results)
    final_passed = sum(item.final_passed for item in results)
    final_failed = sum(item.final_failed for item in results)
    return {
        "cases": total,
        "cases_passed": sum(1 for item in results if item.passed),
        "migration_success_rate": sum(
            1 for item in results if item.actual_status == "SUCCESSFUL"
        )
        / total,
        "initial_test_pass_rate": _rate(baseline_passed, baseline_failed),
        "final_test_pass_rate": _rate(final_passed, final_failed),
        "average_iterations": sum(item.iterations for item in results) / total,
        "total_files_modified": sum(item.files_modified for item in results),
        "human_intervention_rate": sum(
            1 for item in results if item.actual_status != "SUCCESSFUL"
        )
        / total,
        "unresolved_failures": final_failed,
        "execution_time_s": round(sum(item.duration_s for item in results), 3),
    }


def _write_results(results: list[CaseResult], results_dir: Path) -> dict[str, Any]:
    results_dir.mkdir(parents=True, exist_ok=True)
    metrics = _metrics(results)
    rows = [item.as_row() for item in results]

    with (results_dir / "metrics.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# AutoMigrate benchmark results",
        "",
        f"Cases passed: {metrics['cases_passed']}/{metrics['cases']}",
        f"Migration success rate: {metrics['migration_success_rate']:.0%}",
        f"Initial test pass rate: {metrics['initial_test_pass_rate']:.0%}",
        f"Final test pass rate: {metrics['final_test_pass_rate']:.0%}",
        f"Average iterations: {metrics['average_iterations']:.2f}",
        f"Files modified: {metrics['total_files_modified']}",
        f"Human intervention rate: {metrics['human_intervention_rate']:.0%}",
        f"Unresolved failures: {metrics['unresolved_failures']}",
        f"Execution time: {metrics['execution_time_s']:.2f}s",
        "",
        "| Case | Expected | Actual | Diagnosis | Baseline | Final | Iterations | Passed |",
        "|------|----------|--------|-----------|----------|-------|------------|--------|",
    ]
    for item in results:
        lines.append(
            f"| {item.case.name} | {item.case.expected_status} | {item.actual_status} "
            f"| {item.actual_diagnosis or '-'} "
            f"| {item.baseline_passed}/{item.baseline_failed} "
            f"| {item.final_passed}/{item.final_failed} "
            f"| {item.iterations} | {'yes' if item.passed else 'no'} |"
        )
    (results_dir / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return metrics


def run_evaluation(
    cases_dir: str | Path = Path("benchmarks/cases"),
    *,
    llm: str = "mock",
    results_dir: str | Path = Path("benchmarks/results"),
) -> tuple[list[CaseResult], dict[str, Any]]:
    cases = discover_cases(cases_dir)
    results = [run_case(case, llm=llm) for case in cases]
    metrics = _write_results(results, Path(results_dir))
    return results, metrics
