"""Allowlisted subprocess execution for pytest, ruff, and git."""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from automigrate.tools.filesystem import Sandbox

ALLOWED_COMMANDS = frozenset({"pytest", "ruff", "git", "pip"})


class ToolError(RuntimeError):
    pass


class DisallowedCommand(ToolError):
    pass


class ToolUnavailable(ToolError):
    pass


class CommandTimeout(ToolError):
    pass


@dataclass
class CommandResult:
    command: str
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_s: float


class SubprocessRunner:
    def __init__(self, root: str | Path, *, timeout: int = 120) -> None:
        self.root = Path(root).resolve()
        self.sandbox = Sandbox(self.root)
        self.timeout = timeout

    def run(
        self,
        command: str,
        args: list[str] | tuple[str, ...],
        *,
        cwd: str | Path | None = None,
    ) -> CommandResult:
        if command not in ALLOWED_COMMANDS:
            raise DisallowedCommand(f"command not allowlisted: {command}")
        clean_args = list(args)
        if any(not isinstance(arg, str) or "\x00" in arg for arg in clean_args):
            raise DisallowedCommand("arguments must be plain strings")
        executable = shutil.which(command)
        if executable is None:
            raise ToolUnavailable(f"required tool not found on PATH: {command}")
        working_dir = self.sandbox.resolve(cwd) if cwd is not None else self.root
        if not working_dir.is_dir():
            raise DisallowedCommand(f"working directory is not inside workspace: {cwd}")

        started = time.perf_counter()
        try:
            completed = subprocess.run(
                [executable, *clean_args],
                cwd=str(working_dir),
                shell=False,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CommandTimeout(
                f"{command} timed out after {self.timeout}s"
            ) from exc
        duration = time.perf_counter() - started
        return CommandResult(
            command=command,
            args=clean_args,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            duration_s=duration,
        )
