"""Check repository attribute names against two installed package versions."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from automigrate.apidiff.models import ApiDiff, ApiSymbol
from automigrate.ingest.dependencies import import_module_roots
from automigrate.ingest.scanner import find_python_files

_CHECK_SCRIPT = """
import importlib
import json
import sys

payload = json.load(sys.stdin)
target = sys.argv[1]
sys.path.insert(0, target)
result = {}
for module, names in payload.items():
    try:
        imported = importlib.import_module(module)
    except BaseException as exc:
        result[module] = {"__error__": f"{type(exc).__name__}: {exc}"}
        continue
    result[module] = {name: bool(hasattr(imported, name)) for name in names}
print(json.dumps(result))
"""


@dataclass(frozen=True)
class NameReference:
    module: str
    name: str
    file: str
    line: int


@dataclass(frozen=True)
class NameCheck:
    module: str
    name: str
    present_old: bool
    present_new: bool
    references: tuple[NameReference, ...] = field(default_factory=tuple)

    @property
    def qualified_name(self) -> str:
        return f"{self.module}.{self.name}"

    @property
    def removed(self) -> bool:
        return self.present_old and not self.present_new


def _dependency_roots(dependency: str) -> set[str]:
    return {
        root.lower().replace("-", "_")
        for root in import_module_roots(dependency)
    }


def _is_dependency_module(module: str, roots: set[str]) -> bool:
    top = module.split(".")[0].lower().replace("-", "_")
    return bool(top) and top in roots


class _NameCollector(ast.NodeVisitor):
    def __init__(
        self,
        *,
        relative_path: str,
        roots: set[str],
    ) -> None:
        self.relative_path = relative_path
        self.roots = roots
        self.bindings: dict[str, str] = {}
        self._found: dict[tuple[str, str, str, int], NameReference] = {}

    def _add(self, node: ast.AST, module: str, name: str) -> None:
        if not module or not name:
            return
        line = getattr(node, "lineno", 1)
        key = (module, name, self.relative_path, line)
        if key in self._found:
            return
        self._found[key] = NameReference(
            module=module,
            name=name,
            file=self.relative_path,
            line=line,
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if not _is_dependency_module(alias.name, self.roots):
                continue
            if alias.asname:
                local = alias.asname
                module = alias.name
            else:
                local = alias.name.split(".")[0]
                module = local
            self.bindings[local] = module

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if not _is_dependency_module(module, self.roots):
            return
        for alias in node.names:
            if alias.name == "*":
                continue
            self._add(node, module, alias.name)
            local = alias.asname or alias.name
            self.bindings[local] = f"{module}.{alias.name}"

    def visit_Attribute(self, node: ast.Attribute) -> None:
        parts: list[str] = []
        current: ast.expr = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name) and current.id in self.bindings:
            base = self.bindings[current.id]
            if _is_dependency_module(base, self.roots):
                full = [*base.split("."), *reversed(parts)]
                module = ".".join(full[:-1])
                name = full[-1]
                self._add(node, module, name)
        self.generic_visit(node)

    def references(self) -> list[NameReference]:
        return sorted(
            self._found.values(),
            key=lambda ref: (ref.file, ref.line, ref.module, ref.name),
        )


def collect_dependency_names(
    root: str | Path,
    dependency: str,
) -> list[NameReference]:
    """Return every attribute or from-import name used on the dependency."""

    root_path = Path(root)
    roots = _dependency_roots(dependency)
    references: list[NameReference] = []
    for path in find_python_files(root_path):
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        relative = path.relative_to(root_path).as_posix()
        collector = _NameCollector(relative_path=relative, roots=roots)
        collector.visit(tree)
        references.extend(collector.references())
    return references


def _run_checks(
    target: Path,
    names_by_module: dict[str, list[str]],
    *,
    python: str,
    timeout: int,
) -> dict[tuple[str, str], bool]:
    if not names_by_module:
        return {}
    completed = subprocess.run(
        [python, "-c", _CHECK_SCRIPT, str(target)],
        input=json.dumps(names_by_module),
        cwd=str(target),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        tail = detail[-1] if detail else "unknown error"
        raise RuntimeError(f"runtime name check failed for {target}: {tail}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"runtime name check returned invalid output for {target}"
        ) from exc
    presence: dict[tuple[str, str], bool] = {}
    for module, values in payload.items():
        if not isinstance(values, dict) or "__error__" in values:
            for name in names_by_module.get(module, []):
                presence[(module, name)] = False
            continue
        for name, present in values.items():
            presence[(module, name)] = bool(present)
    return presence


def check_names_at_targets(
    old_target: str | Path,
    new_target: str | Path,
    references: Sequence[NameReference],
    *,
    python: str | None = None,
    timeout: int = 120,
) -> list[NameCheck]:
    """Check each unique name in both pip ``--target`` directories."""

    grouped: dict[tuple[str, str], list[NameReference]] = {}
    for reference in references:
        grouped.setdefault((reference.module, reference.name), []).append(reference)
    if not grouped:
        return []

    names_by_module: dict[str, list[str]] = {}
    for module, name in grouped:
        names_by_module.setdefault(module, []).append(name)
    for names in names_by_module.values():
        names.sort()

    executable = python or sys.executable
    old_presence = _run_checks(
        Path(old_target),
        names_by_module,
        python=executable,
        timeout=timeout,
    )
    new_presence = _run_checks(
        Path(new_target),
        names_by_module,
        python=executable,
        timeout=timeout,
    )
    checks: list[NameCheck] = []
    for module, name in sorted(grouped):
        checks.append(
            NameCheck(
                module=module,
                name=name,
                present_old=old_presence.get((module, name), False),
                present_new=new_presence.get((module, name), False),
                references=tuple(grouped[(module, name)]),
            )
        )
    return checks


def add_removed_names(
    diff: ApiDiff,
    checks: Sequence[NameCheck],
) -> ApiDiff:
    """Add runtime-detected removals to the diff without double counting."""

    existing = set(diff.affected_old_symbols())
    additions: list[ApiSymbol] = []
    for check in checks:
        if not check.removed or check.qualified_name in existing:
            continue
        existing.add(check.qualified_name)
        additions.append(
            ApiSymbol(
                module=check.module,
                name=check.name,
                kind="function",
                params=[],
            )
        )
    if not additions:
        return diff
    removed = sorted(
        [*diff.removed, *additions],
        key=lambda symbol: symbol.qualified_name,
    )
    return diff.model_copy(update={"removed": removed})
