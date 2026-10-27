"""Extract public API symbols from an installed package tree using ``ast``."""

from __future__ import annotations

import ast
from pathlib import Path

from automigrate.apidiff.models import ApiSymbol

SKIP_DIRS = {
    "__pycache__",
    "tests",
    "test",
    "testing",
    "docs",
    "doc",
    "examples",
    "example",
    "scripts",
    "vendor",
    "vendored",
    "_vendor",
}


def _module_name(root: Path, path: Path) -> str | None:
    relative = path.relative_to(root)
    parts = list(relative.parts)
    if any(part in SKIP_DIRS for part in parts[:-1]):
        return None
    if any(part.startswith(".") for part in parts[:-1]):
        return None
    parts[-1] = path.stem
    if parts[-1] == "__init__":
        parts = parts[:-1]
    public_parts = []
    for part in parts:
        if part.startswith("_") and part != "__init__":
            return None
        public_parts.append(part)
    return ".".join(public_parts)


def _param_names(args: ast.arguments, *, is_method: bool) -> list[str]:
    names: list[str] = []
    positional = [*args.posonlyargs, *args.args]
    if is_method and positional:
        positional = positional[1:]
    names.extend(arg.arg for arg in positional)
    if args.vararg is not None:
        names.append(f"*{args.vararg.arg}")
    names.extend(arg.arg for arg in args.kwonlyargs)
    if args.kwarg is not None:
        names.append(f"**{args.kwarg.arg}")
    return names


def _all_exports(tree: ast.Module) -> set[str] | None:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if "__all__" in targets and isinstance(node.value, (ast.List, ast.Tuple)):
                values = [
                    element.value
                    for element in node.value.elts
                    if isinstance(element, ast.Constant) and isinstance(element.value, str)
                ]
                return set(values)
    return None


def extract_module_symbols(source: str, module: str) -> list[ApiSymbol]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    exports = _all_exports(tree)
    symbols: list[ApiSymbol] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_") and not exports:
                continue
            if exports is not None and node.name not in exports:
                continue
            symbols.append(
                ApiSymbol(
                    module=module,
                    name=node.name,
                    kind="function",
                    params=_param_names(node.args, is_method=False),
                )
            )
        elif isinstance(node, ast.ClassDef):
            if (node.name.startswith("_") and not exports) or (
                exports is not None and node.name not in exports
            ):
                continue
            symbols.append(ApiSymbol(module=module, name=node.name, kind="class"))
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if item.name.startswith("_"):
                    continue
                symbols.append(
                    ApiSymbol(
                        module=module,
                        name=f"{node.name}.{item.name}",
                        kind="method",
                        params=_param_names(item.args, is_method=True),
                    )
                )
    return symbols


def extract_symbols(root: str | Path) -> list[ApiSymbol]:
    """Return every public symbol found under ``root`` in deterministic order."""

    root_path = Path(root)
    symbols: list[ApiSymbol] = []
    for path in sorted(root_path.rglob("*.py")):
        module = _module_name(root_path, path)
        if module is None:
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        symbols.extend(extract_module_symbols(source, module))
    return sorted(symbols, key=lambda symbol: (symbol.module, symbol.name, symbol.kind))
