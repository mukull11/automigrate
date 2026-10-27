"""Deterministic data models for the public API diff."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SymbolKind = Literal["function", "class", "method"]


class ApiSymbol(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module: str
    name: str
    kind: SymbolKind
    params: list[str] = Field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        return f"{self.module}.{self.name}" if self.module else self.name

    @property
    def leaf(self) -> str:
        return self.name.rsplit(".", 1)[-1]

    @property
    def signature(self) -> str:
        if self.kind == "class":
            return self.name
        return f"{self.name}({', '.join(self.params)})"


class RenameChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old: ApiSymbol
    new: ApiSymbol
    reason: str


class MoveChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old: ApiSymbol
    new: ApiSymbol
    reason: str


class ParamChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: ApiSymbol
    old_params: list[str]
    new_params: list[str]
    reason: str


class ModuleMove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    old_module: str
    new_module: str
    reason: str


class ApiDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dependency: str = ""
    current_version: str = ""
    target_version: str = ""
    removed: list[ApiSymbol] = Field(default_factory=list)
    added: list[ApiSymbol] = Field(default_factory=list)
    renamed: list[RenameChange] = Field(default_factory=list)
    changed_parameters: list[ParamChange] = Field(default_factory=list)
    moved: list[MoveChange] = Field(default_factory=list)
    moved_modules: list[ModuleMove] = Field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(
            self.removed
            or self.renamed
            or self.changed_parameters
            or self.moved
            or self.moved_modules
        )

    def affected_old_symbols(self) -> list[str]:
        names: list[str] = []
        for symbol in self.removed:
            names.append(symbol.qualified_name)
        for change in self.renamed:
            names.append(change.old.qualified_name)
        for change in self.moved:
            names.append(change.old.qualified_name)
        for change in self.changed_parameters:
            names.append(change.symbol.qualified_name)
        for move in self.moved_modules:
            names.append(move.old_module)
        seen: set[str] = set()
        ordered: list[str] = []
        for name in names:
            if name not in seen:
                seen.add(name)
                ordered.append(name)
        return ordered

    def documented_replacements(self) -> set[str]:
        names: set[str] = set()
        for change in self.renamed:
            names.add(change.new.qualified_name)
            names.add(change.new.leaf)
        for change in self.moved:
            names.add(change.new.qualified_name)
            names.add(change.new.leaf)
        for change in self.changed_parameters:
            names.add(change.symbol.qualified_name)
            names.add(change.symbol.leaf)
        return names
