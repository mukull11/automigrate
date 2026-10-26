"""ruff execution with JSON output parsing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from automigrate.tools.subprocess import SubprocessRunner, ToolError


@dataclass
class RuffResult:
    issue_count: int = 0
    issues: list[dict[str, Any]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.issue_count == 0


def parse_ruff_json(stdout: str) -> RuffResult:
    text = stdout.strip()
    if not text:
        return RuffResult()
    data = json.loads(text)
    if not isinstance(data, list):
        raise ToolError("ruff output was not a JSON list")
    return RuffResult(issue_count=len(data), issues=data)


class RuffRunner:
    def __init__(self, runner: SubprocessRunner) -> None:
        self.runner = runner

    def run(self, targets: list[str] | None = None) -> RuffResult:
        args = ["check", "--output-format=json"]
        args.extend(targets or ["."])
        result = self.runner.run("ruff", args)
        try:
            parsed = parse_ruff_json(result.stdout)
        except json.JSONDecodeError as exc:
            raise ToolError(f"could not parse ruff JSON output: {exc}") from exc
        return parsed
