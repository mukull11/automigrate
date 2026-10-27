"""Deterministic public API diff between two package versions."""

from __future__ import annotations

from difflib import SequenceMatcher

from automigrate.apidiff.models import (
    ApiDiff,
    ApiSymbol,
    ModuleMove,
    MoveChange,
    ParamChange,
    RenameChange,
)

RENAME_SIMILARITY = 0.5


def _is_public_symbol(symbol: ApiSymbol) -> bool:
    if symbol.name.startswith("_"):
        return False
    module = symbol.module or ""
    return all(
        not part.startswith("_") for part in module.split(".") if part
    )


def _module_is_used(module: str, imported_modules: set[str]) -> bool:
    if not module or "." not in module:
        return True
    for imported in imported_modules:
        if imported == module or imported.startswith(f"{module}."):
            return True
    return False


def filter_public_diff(
    diff: ApiDiff, imported_modules: set[str] | None = None
) -> ApiDiff:
    """Drop private names and submodules the repository never imports.

    The old side of a rename/move/parameter change decides whether the change
    is relevant; this keeps the replacement symbol available to the planner.
    """

    def keeps(symbol: ApiSymbol) -> bool:
        if not _is_public_symbol(symbol):
            return False
        if imported_modules is None:
            return True
        return _module_is_used(symbol.module, imported_modules)

    updates = {
        "removed": [item for item in diff.removed if keeps(item)],
        "added": [item for item in diff.added if keeps(item)],
        "renamed": [
            change for change in diff.renamed if keeps(change.old)
        ],
        "moved": [change for change in diff.moved if keeps(change.old)],
        "changed_parameters": [
            change
            for change in diff.changed_parameters
            if keeps(change.symbol)
        ],
    }
    kept_moves = updates["moved"]
    grouped: dict[tuple[str, str], list[str]] = {}
    for change in kept_moves:
        grouped.setdefault(
            (change.old.module, change.new.module), []
        ).append(change.old.name)
    updates["moved_modules"] = [
        ModuleMove(
            old_module=old_module,
            new_module=new_module,
            reason=(
                f"module {old_module} moved to {new_module} "
                f"({', '.join(sorted(names))})"
            ),
        )
        for (old_module, new_module), names in grouped.items()
    ]
    return diff.model_copy(update=updates)


def _ratio(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio()


def _index(symbols: list[ApiSymbol]) -> dict[str, ApiSymbol]:
    return {symbol.qualified_name: symbol for symbol in symbols}


def diff_apis(
    old_symbols: list[ApiSymbol],
    new_symbols: list[ApiSymbol],
    *,
    dependency: str = "",
    current_version: str = "",
    target_version: str = "",
    imported_modules: set[str] | None = None,
) -> ApiDiff:
    """Compare two symbol sets and classify every difference deterministically."""

    old_index = _index(old_symbols)
    new_index = _index(new_symbols)
    removed = [old_index[name] for name in sorted(old_index.keys() - new_index.keys())]
    added = [new_index[name] for name in sorted(new_index.keys() - old_index.keys())]

    moved: list[MoveChange] = []
    renamed: list[RenameChange] = []
    _pair_moved(removed, added, moved)
    _pair_renamed(removed, added, renamed)

    changed: list[ParamChange] = []
    for name in sorted(old_index.keys() & new_index.keys()):
        old = old_index[name]
        new = new_index[name]
        if old.kind != new.kind:
            continue
        if old.params != new.params:
            changed.append(
                ParamChange(
                    symbol=old,
                    old_params=list(old.params),
                    new_params=list(new.params),
                    reason="parameter list changed",
                )
            )

    moved_modules = _module_moves(moved)

    result = ApiDiff(
        dependency=dependency,
        current_version=current_version,
        target_version=target_version,
        removed=sorted(removed, key=lambda symbol: symbol.qualified_name),
        added=sorted(added, key=lambda symbol: symbol.qualified_name),
        renamed=sorted(renamed, key=lambda change: change.old.qualified_name),
        changed_parameters=sorted(
            changed, key=lambda change: change.symbol.qualified_name
        ),
        moved=sorted(moved, key=lambda change: change.old.qualified_name),
        moved_modules=moved_modules,
    )
    return filter_public_diff(result, imported_modules)


def _pair_moved(
    removed: list[ApiSymbol],
    added: list[ApiSymbol],
    moved: list[MoveChange],
) -> None:
    for old in list(removed):
        for new in list(added):
            if new not in added:
                continue
            if new.kind != old.kind:
                continue
            if new.name != old.name or old.params != new.params:
                continue
            if new.module == old.module:
                continue
            moved.append(
                MoveChange(
                    old=old,
                    new=new,
                    reason=f"{old.qualified_name} moved to {new.qualified_name}",
                )
            )
            removed.remove(old)
            added.remove(new)
            break


def _pair_renamed(
    removed: list[ApiSymbol],
    added: list[ApiSymbol],
    renamed: list[RenameChange],
) -> None:
    while True:
        best: tuple[float, ApiSymbol, ApiSymbol] | None = None
        for old in removed:
            for new in added:
                if new.kind != old.kind or new.module != old.module:
                    continue
                if old.params != new.params:
                    continue
                ratio = _ratio(old.leaf, new.leaf)
                if ratio < RENAME_SIMILARITY:
                    continue
                if best is None or ratio > best[0]:
                    best = (ratio, old, new)
        if best is None:
            return
        _, old, new = best
        renamed.append(
            RenameChange(
                old=old,
                new=new,
                reason=f"{old.leaf} renamed to {new.leaf}",
            )
        )
        removed.remove(old)
        added.remove(new)


def _module_moves(moved: list[MoveChange]) -> list[ModuleMove]:
    grouped: dict[tuple[str, str], list[str]] = {}
    for change in moved:
        grouped.setdefault((change.old.module, change.new.module), []).append(
            change.old.name
        )
    result = [
        ModuleMove(
            old_module=old_module,
            new_module=new_module,
            reason=(
                f"module {old_module} moved to {new_module} "
                f"({', '.join(sorted(names))})"
            ),
        )
        for (old_module, new_module), names in grouped.items()
    ]
    return sorted(result, key=lambda move: (move.old_module, move.new_module))
