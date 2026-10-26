"""Workspace-confined filesystem access."""

from __future__ import annotations

from pathlib import Path


class SandboxViolation(RuntimeError):
    pass


class ReadOnlyPath(SandboxViolation):
    pass


def is_test_path(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    if "tests" in parts or "test" in parts:
        return True
    name = path.name.lower()
    return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


class Sandbox:
    """All filesystem access for agents goes through this class."""

    def __init__(self, root: str | Path, *, allow_test_edits: bool = False) -> None:
        self.root = Path(root).resolve()
        self.allow_test_edits = allow_test_edits

    def resolve(self, relative: str | Path) -> Path:
        raw = Path(relative)
        if raw.is_absolute():
            raise SandboxViolation(f"absolute path rejected: {relative}")
        if any(part == ".." for part in raw.parts):
            raise SandboxViolation(f"parent traversal rejected: {relative}")
        candidate = (self.root / raw).resolve()
        if not candidate.is_relative_to(self.root):
            raise SandboxViolation(f"path escapes workspace: {relative}")
        return candidate

    def relative(self, path: str | Path) -> Path:
        resolved = self.resolve(path)
        return resolved.relative_to(self.root)

    def exists(self, relative: str | Path) -> bool:
        return self.resolve(relative).exists()

    def is_file(self, relative: str | Path) -> bool:
        return self.resolve(relative).is_file()

    def is_dir(self, relative: str | Path) -> bool:
        return self.resolve(relative).is_dir()

    def read_text(self, relative: str | Path) -> str:
        return self.resolve(relative).read_text(encoding="utf-8")

    def read_bytes(self, relative: str | Path) -> bytes:
        return self.resolve(relative).read_bytes()

    def write_text(self, relative: str | Path, content: str) -> Path:
        target = self._writable(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        return target

    def write_bytes(self, relative: str | Path, content: bytes) -> Path:
        target = self._writable(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def delete(self, relative: str | Path) -> None:
        target = self._writable(relative)
        if target.is_dir():
            raise SandboxViolation(f"refusing to delete directory: {relative}")
        target.unlink(missing_ok=True)

    def ensure_dir(self, relative: str | Path) -> Path:
        target = self.resolve(relative)
        if target.exists() and not target.is_dir():
            raise SandboxViolation(f"not a directory: {relative}")
        target.mkdir(parents=True, exist_ok=True)
        return target

    def list_files(self, pattern: str = "**/*") -> list[Path]:
        if ".." in Path(pattern).parts:
            raise SandboxViolation(f"parent traversal rejected: {pattern}")
        return sorted(
            path
            for path in self.root.glob(pattern)
            if path.is_file() and path.resolve().is_relative_to(self.root)
        )

    def _writable(self, relative: str | Path) -> Path:
        target = self.resolve(relative)
        if not self.allow_test_edits and is_test_path(target.relative_to(self.root)):
            raise ReadOnlyPath(f"test files are read-only: {relative}")
        return target
