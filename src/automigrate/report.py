"""Render migration reports from persisted session, test, and diff records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from automigrate import db

FLAGGED_STATUSES = {"REQUIRES_HUMAN_REVIEW", "FAILED", "PARTIAL"}


class ReportError(RuntimeError):
    pass


@dataclass
class ReportResult:
    session_id: str
    status: str
    report_path: Path
    report_json_path: Path
    markdown: str
    data: dict[str, Any]


def _unresolved(latest: dict[str, Any] | None) -> list[str]:
    if latest is None:
        return []
    raw = latest.get("failed_tests_json") or "[]"
    try:
        names = json.loads(raw)
    except json.JSONDecodeError:
        return [raw]
    return [str(name) for name in names]


def _tests_table(tests: list[dict[str, Any]]) -> list[str]:
    if not tests:
        return ["No test runs recorded."]
    lines = [
        "| Iteration | Passed | Failed | Errors | Ruff | Duration (s) |",
        "|-----------|--------|--------|--------|------|--------------|",
    ]
    for run in tests:
        lines.append(
            f"| {run['iteration']} | {run['passed']} | {run['failed']} "
            f"| {run['errors']} | {run['ruff_issues']} "
            f"| {float(run['duration_s']):.2f} |"
        )
    return lines


def _changes_table(changes: list[dict[str, Any]]) -> list[str]:
    if not changes:
        return ["No source changes recorded."]
    lines = [
        "| Iteration | File | Description | Commit |",
        "|-----------|------|-------------|--------|",
    ]
    for change in changes:
        sha = (change.get("commit_sha") or "")[:8]
        description = (change.get("description") or "").replace("|", "\\|")
        lines.append(
            f"| {change['iteration']} | {change['file']} | {description} | {sha} |"
        )
    return lines


def build_report(conn, session_id: str) -> ReportResult:
    """Build report content for one session without writing it."""

    summary = db.get_session_summary(conn, session_id)
    if summary is None:
        raise ReportError(f"unknown session: {session_id}")

    tests = db.list_test_runs(conn, session_id)
    changes = db.list_migration_changes(conn, session_id)
    baseline = tests[0] if tests else None
    latest = tests[-1] if tests else None
    unresolved = _unresolved(latest)
    flagged = summary["status"] in FLAGGED_STATUSES

    lines = [
        f"# Migration report: {session_id}",
        "",
        f"- Repository: {summary['repo_name']} (`{summary['repo_path']}`)",
        f"- Dependency: {summary['dependency']} "
        f"{summary['source_version']} -> {summary['target_version']}",
        f"- Status: {summary['status']}",
        f"- Iterations: {summary['iterations']}",
        f"- Started: {summary['started_at']}",
        f"- Ended: {summary['ended_at'] or 'not recorded'}",
        f"- Flag reason: {summary['final_reason'] or 'none'}",
        f"- Workspace: outputs/{session_id}/workspace",
        f"- Rolled back to baseline: {'yes' if flagged else 'no'}",
        "",
        "## Tests",
        "",
        *_tests_table(tests),
        "",
        "## Affected files",
        "",
        *_changes_table(changes),
        "",
        "## Unresolved failures",
        "",
    ]
    if unresolved:
        lines.extend(f"- {name}" for name in unresolved)
    else:
        lines.append("None.")
    if latest and latest.get("traceback"):
        lines.extend(["", "### Latest traceback", "", "```text", latest["traceback"], "```"])

    lines.extend(["", "## Diff", ""])
    diffs = [
        (change.get("diff") or "").rstrip()
        for change in changes
        if (change.get("diff") or "").strip()
    ]
    if diffs:
        if flagged:
            lines.append(
                "The workspace was rolled back to the baseline; this diff is the "
                "unverified patch that was discarded."
            )
            lines.append("")
        lines.extend(["```diff", "\n\n".join(diffs), "```"])
    else:
        lines.append("No source changes recorded.")
    lines.extend(["", "## Citations", ""])
    lines.append("Citation IDs are not persisted by this schema version.")

    markdown = "\n".join(lines).rstrip() + "\n"
    data = {
        "session_id": session_id,
        "status": summary["status"],
        "repository": summary["repo_name"],
        "repo_path": summary["repo_path"],
        "dependency": summary["dependency"],
        "from_version": summary["source_version"],
        "to_version": summary["target_version"],
        "iterations": summary["iterations"],
        "flag_reason": summary["final_reason"],
        "started_at": summary["started_at"],
        "ended_at": summary["ended_at"],
        "rolled_back": flagged,
        "baseline": baseline,
        "latest": latest,
        "tests": tests,
        "changes": changes,
        "unresolved_failures": unresolved,
    }
    outputs_dir = Path("outputs")
    report_path = outputs_dir / session_id / "report.md"
    return ReportResult(
        session_id=session_id,
        status=summary["status"],
        report_path=report_path,
        report_json_path=report_path.with_suffix(".json"),
        markdown=markdown,
        data=data,
    )


def write_report(conn, session_id: str, outputs_dir: str | Path = "outputs") -> ReportResult:
    """Render and persist report.md/report.json for a session."""

    result = build_report(conn, session_id)
    directory = Path(outputs_dir) / session_id
    directory.mkdir(parents=True, exist_ok=True)
    report_path = directory / "report.md"
    report_json_path = directory / "report.json"
    report_path.write_text(result.markdown, encoding="utf-8")
    report_json_path.write_text(
        json.dumps(result.data, indent=2, default=str) + "\n", encoding="utf-8"
    )
    db.save_report(
        conn,
        session_id,
        result.status,
        json.dumps(result.data, default=str),
        str(report_path),
    )
    result.report_path = report_path
    result.report_json_path = report_json_path
    return result
