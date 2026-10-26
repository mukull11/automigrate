"""AST-based discovery of dependency usages in Python source files."""

from __future__ import annotations

import ast
from pathlib import Path

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


class _UsageCollector(ast.NodeVisitor):
    def __init__(
        self,
        *,
        relative_path: str,
        source_lines: list[str],
        modules: set[str],
    ) -> None:
        self.relative_path = relative_path
        self.source_lines = source_lines
        self.modules = modules
        self.bindings: dict[str, str] = {}
        self._parent: ast.AST | None = None
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
            kind=kind,
            snippet=self._snippet(line),
        )

    def _module_matches(self, module: str | None) -> bool:
        if not module:
            return False
        return module.split(".")[0].lower() in self.modules

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0].lower()
            if root in self.modules:
                local = alias.asname or alias.name.split(".")[0]
                self.bindings[local] = alias.name
                self._add(node, alias.name, "import")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if not self._module_matches(node.module):
            return
        for alias in node.names:
            full = f"{node.module}.{alias.name}" if alias.name != "*" else node.module
            local = alias.asname or alias.name
            self.bindings[local] = full
            self._add(node, full, "import")

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        resolved: str | None = None
        if isinstance(func, ast.Name) and func.id in self.bindings:
            resolved = self.bindings[func.id]
        elif isinstance(func, ast.Attribute):
            resolved = _resolve_attribute(func, self.bindings)
        if resolved is not None:
            self._add(node, resolved, "call")
            for keyword in node.keywords:
                if keyword.arg is not None:
                    self._add(keyword, keyword.arg, "keyword_arg")
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(self._parent, ast.Attribute):
            return
        if isinstance(self._parent, ast.Call) and self._parent.func is node:
            return
        resolved = _resolve_attribute(node, self.bindings)
        if resolved is not None:
            self._add(node, resolved, "attribute")

    def visit_Name(self, node: ast.Name) -> None:
        if not isinstance(node.ctx, ast.Load) or node.id not in self.bindings:
            return
        if isinstance(self._parent, ast.Attribute):
            return
        if isinstance(self._parent, ast.Call) and self._parent.func is node:
            return
        self._add(node, self.bindings[node.id], "attribute")

    def generic_visit(self, node: ast.AST) -> None:
        previous = self._parent
        self._parent = node
        try:
            super().generic_visit(node)
        finally:
            self._parent = previous

    def usages(self) -> list[Usage]:
        return sorted(
            self._found.values(),
            key=lambda usage: (usage.file, usage.line, usage.symbol, usage.kind),
        )


def analyze_source(
    source: str,
    relative_path: str,
    modules: set[str],
) -> list[Usage]:
    """Return dependency usages found in a single Python source string."""

    tree = ast.parse(source)
    collector = _UsageCollector(
        relative_path=relative_path,
        source_lines=source.splitlines(),
        modules={module.lower() for module in modules},
    )
    collector.visit(tree)
    return collector.usages()


def analyze_file(path: Path, root: Path, modules: set[str]) -> list[Usage]:
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        relative = path.as_posix()
    try:
        return analyze_source(source, relative, modules)
    except SyntaxError:
        return []


def find_usages(
    files: list[Path],
    dependency: str,
    root: str | Path,
    modules: set[str] | None = None,
) -> list[Usage]:
    """Find every usage of a dependency across the given Python files."""

    from automigrate.ingest.dependencies import import_module_roots

    module_roots = modules or import_module_roots(dependency)
    root_path = Path(root)
    usages: list[Usage] = []
    for path in files:
        usages.extend(analyze_file(path, root_path, module_roots))
    return usages
