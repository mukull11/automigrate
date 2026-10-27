"""Find repository locations that use symbols affected by an API diff."""

from __future__ import annotations

import ast
from pathlib import Path

from automigrate.apidiff.models import ApiDiff
from automigrate.ingest.scanner import find_python_files
from automigrate.state import Usage


def _resolve_attribute(node: ast.Attribute, bindings: dict[str, str]) -> str | None:
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name) or current.id not in bindings:
        return None
    return ".".join([bindings[current.id], *reversed(parts)])


def _matches(candidate: str, targets: set[str]) -> bool:
    for target in targets:
        if candidate == target or candidate.startswith(f"{target}."):
            return True
    return False


class _ApiUsageCollector(ast.NodeVisitor):
    def __init__(
        self,
        *,
        relative_path: str,
        source_lines: list[str],
        targets: set[str],
        param_targets: dict[str, set[str]],
    ) -> None:
        self.relative_path = relative_path
        self.source_lines = source_lines
        self.targets = targets
        self.param_targets = param_targets
        self.bindings: dict[str, str] = {}
        self._found: dict[tuple[int, str, str], Usage] = {}

    def _snippet(self, line: int) -> str:
        if 1 <= line <= len(self.source_lines):
            return self.source_lines[line - 1].strip()
        return ""

    def _add(self, node: ast.AST, symbol: str, kind: str) -> None:
        line = getattr(node, "lineno", 1)
        key = (line, symbol, kind)
        if key in self._found:
            return
        self._found[key] = Usage(
            file=self.relative_path,
            line=line,
            symbol=symbol,
            kind=kind,  # type: ignore[arg-type]
            snippet=self._snippet(line),
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            self.bindings[local] = alias.name
            if _matches(alias.name, self.targets):
                self._add(node, alias.name, "import")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            full = f"{module}.{alias.name}" if alias.name != "*" else module
            local = alias.asname or alias.name
            self.bindings[local] = full
            if _matches(full, self.targets) or _matches(module, self.targets):
                self._add(node, full, "import")

    def visit_Call(self, node: ast.Call) -> None:
        resolved = None
        func = node.func
        if isinstance(func, ast.Name) and func.id in self.bindings:
            resolved = self.bindings[func.id]
        elif isinstance(func, ast.Attribute):
            resolved = _resolve_attribute(func, self.bindings)
        if resolved is not None and _matches(resolved, self.targets):
            self._add(node, resolved, "call")
            params = self.param_targets.get(resolved, set())
            for keyword in node.keywords:
                if keyword.arg is not None and keyword.arg in params:
                    self._add(keyword, keyword.arg, "keyword_arg")
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        resolved = _resolve_attribute(node, self.bindings)
        if resolved is not None and _matches(resolved, self.targets):
            self._add(node, resolved, "attribute")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id in self.bindings:
            resolved = self.bindings[node.id]
            if _matches(resolved, self.targets):
                self._add(node, resolved, "attribute")

    def usages(self) -> list[Usage]:
        return sorted(
            self._found.values(),
            key=lambda usage: (usage.file, usage.line, usage.symbol, usage.kind),
        )


def scan_api_usages(root: str | Path, diff: ApiDiff) -> list[Usage]:
    """Scan every Python file for imports, calls, and attributes of changed symbols."""

    root_path = Path(root)
    targets = set(diff.affected_old_symbols())
    if not targets:
        return []
    param_targets = {
        change.symbol.qualified_name: set(change.old_params)
        for change in diff.changed_parameters
    }
    usages: list[Usage] = []
    for path in find_python_files(root_path):
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        relative = path.relative_to(root_path).as_posix()
        collector = _ApiUsageCollector(
            relative_path=relative,
            source_lines=source.splitlines(),
            targets=targets,
            param_targets=param_targets,
        )
        collector.visit(tree)
        usages.extend(collector.usages())
    return usages


def collect_imported_modules(root: str | Path) -> set[str]:
    """Return every module name imported by the repository's Python files."""

    root_path = Path(root)
    modules: set[str] = set()
    for path in find_python_files(root_path):
        try:
            tree = ast.parse(
                path.read_text(encoding="utf-8", errors="replace")
            )
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    modules.add(node.module)
    return modules
