"""Install two dependency versions and build a deterministic API diff."""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from automigrate.apidiff.diff import diff_apis
from automigrate.apidiff.models import ApiDiff
from automigrate.apidiff.symbols import extract_symbols
from automigrate.ingest.dependencies import import_module_roots, normalize_name
from automigrate.tools.subprocess import SubprocessRunner, ToolError

DOC_PREFIXES = ("readme", "changelog", "changes", "history", "news")
DOC_SUFFIXES = {".md", ".rst", ".txt", ".markdown"}


class ApiDiffError(RuntimeError):
    pass


@dataclass
class InstalledDocument:
    name: str
    text: str


@dataclass
class ApiDiffBundle:
    diff: ApiDiff
    documents: list[InstalledDocument] = field(default_factory=list)


class ApiDetector(Protocol):
    def detect(
        self,
        dependency: str,
        current_version: str,
        target_version: str,
        workspace: str | Path,
    ) -> ApiDiffBundle:
        """Return the API diff and any packaged documentation."""


def _read_top_levels(target: Path) -> set[str]:
    levels: set[str] = set()
    for info in target.glob("*.dist-info"):
        top_level = info / "top_level.txt"
        if not top_level.is_file():
            continue
        for line in top_level.read_text(encoding="utf-8", errors="replace").splitlines():
            name = line.strip()
            if name:
                levels.add(name)
    return levels


def _module_roots(target: Path, dependency: str) -> set[str]:
    roots = import_module_roots(dependency)
    if target.is_dir():
        for path in target.iterdir():
            normalized = normalize_name(path.stem if path.is_file() else path.name)
            if normalized.replace("-", "_") in {root.replace("-", "_") for root in roots}:
                roots.add(path.stem if path.is_file() else path.name)
    roots.update(_read_top_levels(target))
    return {root for root in roots if root}


def _symbols_for(target: Path, dependency: str):
    allowed = _module_roots(target, dependency)
    symbols = []
    for symbol in extract_symbols(target):
        top = symbol.module.split(".")[0]
        if top in allowed:
            symbols.append(symbol)
    return symbols


def _collect_documents(target: Path) -> list[InstalledDocument]:
    documents: list[InstalledDocument] = []
    seen: set[Path] = set()
    for path in sorted(target.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in DOC_SUFFIXES:
            continue
        if not path.name.lower().startswith(DOC_PREFIXES):
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if text.strip():
            documents.append(
                InstalledDocument(
                    name=f"installed-package/{path.name}",
                    text=text[:200_000],
                )
            )
    return documents


class PipApiDiffDetector:
    """Install each version with pip into an isolated directory."""

    def __init__(self, *, timeout: int = 300, pip: str = "pip") -> None:
        self.timeout = timeout
        self.pip = pip

    def _install(
        self,
        dependency: str,
        version: str,
        target: Path,
        workspace: str | Path,
    ) -> None:
        runner = SubprocessRunner(workspace, timeout=self.timeout)
        try:
            result = runner.run(
                self.pip,
                [
                    "install",
                    "--disable-pip-version-check",
                    "--no-input",
                    "--target",
                    str(target),
                    f"{dependency}=={version}",
                ],
            )
        except ToolError as exc:
            raise ApiDiffError(f"could not run pip: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            tail = detail[-1] if detail else "unknown pip error"
            raise ApiDiffError(
                f"pip could not fetch {dependency}=={version}: {tail}"
            )

    def detect(
        self,
        dependency: str,
        current_version: str,
        target_version: str,
        workspace: str | Path,
    ) -> ApiDiffBundle:
        with tempfile.TemporaryDirectory(prefix="automigrate-api-old-") as old_dir:
            with tempfile.TemporaryDirectory(prefix="automigrate-api-new-") as new_dir:
                old_target = Path(old_dir)
                new_target = Path(new_dir)
                self._install(dependency, current_version, old_target, workspace)
                self._install(dependency, target_version, new_target, workspace)
                if not any(old_target.iterdir()):
                    raise ApiDiffError(
                        f"pip reported success but installed nothing for "
                        f"{dependency}=={current_version}"
                    )
                if not any(new_target.iterdir()):
                    raise ApiDiffError(
                        f"pip reported success but installed nothing for "
                        f"{dependency}=={target_version}"
                    )
                old_symbols = _symbols_for(old_target, dependency)
                new_symbols = _symbols_for(new_target, dependency)
                if not old_symbols and not new_symbols:
                    raise ApiDiffError(
                        f"could not find any public Python modules for {dependency}; "
                        "the package may not expose importable code"
                    )
                diff = diff_apis(
                    old_symbols,
                    new_symbols,
                    dependency=dependency,
                    current_version=current_version,
                    target_version=target_version,
                )
                documents = _collect_documents(new_target)
        return ApiDiffBundle(diff=diff, documents=documents)


def python_executable() -> str:
    return sys.executable
