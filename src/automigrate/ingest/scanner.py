"""Repository scanning: Python files, declarations, and dependency usages."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from automigrate.ingest.analyzer import find_usages
from automigrate.ingest.dependencies import (
    SKIP_DIRS,
    DependencyDeclaration,
    detect_declarations,
    find_declaration,
    import_module_roots,
)
from automigrate.state import Usage


class ScanResult(BaseModel):
    repo_path: str
    python_files: list[str] = Field(default_factory=list)
    declarations: list[DependencyDeclaration] = Field(default_factory=list)
    dependency: str | None = None
    declared: bool = False
    usages: list[Usage] = Field(default_factory=list)


def find_python_files(root: str | Path) -> list[Path]:
    """Return Python files under ``root``, skipping vendored and generated dirs."""

    root_path = Path(root)
    files: list[Path] = []
    for path in root_path.rglob("*.py"):
        relative_parts = path.relative_to(root_path).parts
        if any(part in SKIP_DIRS or part.startswith(".") for part in relative_parts):
            continue
        if path.is_file():
            files.append(path)
    return sorted(files)


def scan_repository(
    root: str | Path,
    dependency: str | None = None,
) -> ScanResult:
    """Scan a repository, optionally collecting usages for one dependency."""

    root_path = Path(root)
    files = find_python_files(root_path)
    declarations = detect_declarations(root_path)
    result = ScanResult(
        repo_path=str(root_path),
        python_files=[path.as_posix() for path in files],
        declarations=declarations,
    )
    if dependency is None:
        return result

    declaration = find_declaration(declarations, dependency)
    result.dependency = dependency
    result.declared = declaration is not None
    if declaration is None:
        return result

    modules = import_module_roots(declaration.name)
    result.usages = find_usages(files, declaration.name, root_path, modules)
    return result
