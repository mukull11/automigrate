"""Git operations restricted to a workspace copy."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from automigrate.tools.subprocess import SubprocessRunner

IGNORE_PATTERNS = shutil.ignore_patterns(
    ".git",
    "__pycache__",
    "*.pyc",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".venv",
    "venv",
    "build",
    "dist",
    "*.egg-info",
)
GIT_IDENTITY = [
    "-c",
    "user.name=AutoMigrate",
    "-c",
    "user.email=automigrate@example.com",
]


class GitError(RuntimeError):
    pass


class GitRepo:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.runner = SubprocessRunner(self.path)

    def _run(self, args: list[str], *, check: bool = True):
        result = self.runner.run("git", args)
        if check and result.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} failed ({result.returncode}): "
                f"{result.stderr or result.stdout}"
            )
        return result

    def init(self) -> None:
        self._run(["init", "-b", "main"])

    def add_all(self) -> None:
        self._run(["add", "-A"])

    def commit(self, message: str) -> str:
        self._run([*GIT_IDENTITY, "commit", "-m", message, "--allow-empty"])
        return self.head_sha()

    def head_sha(self) -> str:
        return self._run(["rev-parse", "HEAD"]).stdout.strip()

    def has_commits(self) -> bool:
        result = self._run(["rev-parse", "--verify", "HEAD"], check=False)
        return result.returncode == 0

    def status_porcelain(self) -> str:
        return self._run(["status", "--porcelain"]).stdout

    def log_oneline(self) -> list[str]:
        return self._run(["log", "--oneline"]).stdout.splitlines()

    def diff_commit(self, sha: str) -> str:
        return self._run(["show", "--format=", "--patch", sha]).stdout

    def diff_worktree(self) -> str:
        return self._run(["diff", "--", "."]).stdout

    def reset_hard(self, sha: str) -> None:
        self._run(["reset", "--hard", sha])


def prepare_workspace(source: str | Path, destination: str | Path) -> GitRepo:
    """Copy a repository into a fresh workspace and commit the baseline."""

    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if not source_path.is_dir():
        raise FileNotFoundError(f"repository does not exist: {source_path}")
    if destination_path.exists():
        if any(destination_path.iterdir()):
            raise FileExistsError(f"workspace is not empty: {destination_path}")
        destination_path.rmdir()
    shutil.copytree(source_path, destination_path, ignore=IGNORE_PATTERNS, symlinks=False)
    repo = GitRepo(destination_path)
    repo.init()
    exclude = destination_path / ".git" / "info" / "exclude"
    exclude.write_text(
        "__pycache__/\n*.pyc\n*.pyo\n.pytest_cache/\n.ruff_cache/\n.mypy_cache/\n",
        encoding="utf-8",
    )
    repo.add_all()
    repo.commit("automigrate: baseline")
    return repo


def tree_checksum(root: str | Path) -> str:
    """Hash file paths and contents so callers can prove a repo was untouched."""

    root_path = Path(root)
    digest = hashlib.sha256()
    for path in sorted(root_path.rglob("*")):
        if not path.is_file() or ".git" in path.parts:
            continue
        relative = path.relative_to(root_path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(path.read_bytes())
        digest.update(b"\x00")
    return digest.hexdigest()

REPOSITORY_URL_PREFIXES = ("http://", "https://", "git@", "ssh://", "git://")


def is_repository_url(value: str) -> bool:
    """Return True when the input looks like a git repository URL."""

    text = value.strip()
    if not text:
        return False
    if text.lower().startswith(REPOSITORY_URL_PREFIXES):
        return True
    return text.endswith(".git")


def _authenticated_url(url: str, token: str | None) -> str:
    if not token or not url.lower().startswith("https://github.com/"):
        return url
    remainder = url[len("https://") :]
    return f"https://x-access-token:{token}@{remainder}"


def clone_repository(
    url: str,
    destination: str | Path,
    *,
    timeout: int = 300,
) -> Path:
    """Clone ``url`` into ``destination`` using an optional GITHUB_TOKEN."""

    destination_path = Path(destination).resolve()
    if destination_path.exists() and any(destination_path.iterdir()):
        raise GitError(f"clone destination is not empty: {destination_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("GITHUB_TOKEN")
    remote = _authenticated_url(url, token)
    runner = SubprocessRunner(destination_path.parent, timeout=timeout)
    try:
        result = runner.run("git", ["clone", "--depth", "1", remote, str(destination_path)])
    except Exception as exc:  # noqa: BLE001 - sanitize before re-raising
        message = str(exc)
        if token:
            message = message.replace(token, "***")
        raise GitError(f"could not clone repository: {message}") from None
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        if token:
            detail = detail.replace(token, "***")
        raise GitError(f"could not clone repository: {detail or 'git clone failed'}")
    return destination_path
