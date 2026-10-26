"""SEARCH/REPLACE patch generation, parsing, and atomic application."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from automigrate.agent.parsing import strip_code_fences
from automigrate.llm.base import LLMAdapter
from automigrate.state import AgentState, Plan
from automigrate.tools.filesystem import Sandbox, is_test_path

SYSTEM_PROMPT = (
    "You are a precise Python migration patcher. "
    "You output only FILE/SEARCH/REPLACE blocks and never edit tests."
)

PATCH_FORMAT = (
    "FILE: relative/path/to/file.py\n"
    "<<<<<<< SEARCH\n"
    "exact existing lines, copied verbatim\n"
    "=======\n"
    "replacement lines\n"
    ">>>>>>> REPLACE"
)


class PatchError(RuntimeError):
    pass


class PatchFormatError(PatchError):
    pass


class PatchApplyError(PatchError):
    pass


@dataclass(frozen=True)
class PatchBlock:
    file: str
    search: str
    replace: str


@dataclass
class PatchResult:
    files: list[str] = field(default_factory=list)
    blocks_applied: int = 0
    retry_used: bool = False


def parse_patch(text: str) -> list[PatchBlock]:
    stripped = strip_code_fences(text)
    lines = stripped.splitlines()
    blocks: list[PatchBlock] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line or line.startswith("```"):
            index += 1
            continue
        if not line.startswith("FILE:"):
            raise PatchFormatError(f"expected FILE: line, found: {line!r}")
        file_name = line[len("FILE:") :].strip()
        if not file_name:
            raise PatchFormatError("FILE: line has no path")
        index += 1
        if index >= len(lines) or lines[index].strip() != "<<<<<<< SEARCH":
            raise PatchFormatError("missing <<<<<<< SEARCH marker")
        index += 1
        search_lines: list[str] = []
        while index < len(lines) and lines[index].strip() != "=======":
            search_lines.append(lines[index])
            index += 1
        if index >= len(lines):
            raise PatchFormatError("missing ======= separator")
        index += 1
        replace_lines: list[str] = []
        while index < len(lines) and lines[index].strip() != ">>>>>>> REPLACE":
            replace_lines.append(lines[index])
            index += 1
        if index >= len(lines):
            raise PatchFormatError("missing >>>>>>> REPLACE marker")
        index += 1
        search = "\n".join(search_lines)
        replace = "\n".join(replace_lines)
        if not search:
            raise PatchFormatError("SEARCH block is empty")
        blocks.append(PatchBlock(file=file_name, search=search, replace=replace))
    if not blocks:
        raise PatchFormatError("no patch blocks found")
    return blocks


def build_patch_prompt(state: AgentState, plan: Plan, sandbox: Sandbox) -> str:
    file_contents: list[str] = []
    for file_name in sorted({change.file for change in plan.changes}):
        try:
            content = sandbox.read_text(file_name)
        except FileNotFoundError:
            content = ""
        file_contents.append(f"--- {file_name} ---\n{content}")
    changes = "\n".join(
        f"- {change.file}: {change.description} "
        f"({change.old_symbol} -> {change.new_symbol})"
        for change in plan.changes
    )
    evidence = "\n\n".join(
        f"[{item.chunk_id}] {item.heading_path}\n{item.text}" for item in state.evidence
    )
    return (
        f"Apply this migration plan for {state.dependency} "
        f"{state.from_version} -> {state.to_version}.\n\n"
        f"Plan summary: {plan.summary}\n{changes}\n\n"
        f"Evidence:\n{evidence or '- none'}\n\n"
        "Current files:\n" + "\n\n".join(file_contents) + "\n\n"
        "Return only blocks in exactly this format:\n"
        f"{PATCH_FORMAT}\n\n"
        "Each SEARCH must occur exactly once in its file and must match verbatim. "
        "Do not edit tests."
    )


class Patcher:
    def __init__(self, llm: LLMAdapter, *, retries: int = 1) -> None:
        self.llm = llm
        self.retries = retries

    def apply(self, state: AgentState, plan: Plan, sandbox: Sandbox) -> PatchResult:
        prompt = build_patch_prompt(state, plan, sandbox)
        feedback = ""
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            raw = self.llm.complete(
                prompt + feedback,
                system=SYSTEM_PROMPT,
                stage="PATCH",
                iteration=state.iteration,
            )
            try:
                result = self._apply_text(raw, sandbox)
                result.retry_used = attempt > 0
                return result
            except PatchError as exc:
                last_error = exc
                feedback = (
                    "\n\nYour previous patch was rejected: "
                    f"{exc}\nRegenerate the entire patch with exact SEARCH text."
                )
        raise PatchError(f"patcher failed after retry: {last_error}")

    def _apply_text(self, text: str, sandbox: Sandbox) -> PatchResult:
        blocks = parse_patch(text)
        contents: dict[str, str] = {}
        originals: dict[str, str | None] = {}
        touched: list[str] = []
        for block in blocks:
            target = sandbox.resolve(block.file)
            relative = target.relative_to(sandbox.root).as_posix()
            if is_test_path(Path(relative)):
                raise PatchApplyError(f"refusing to edit test file: {relative}")
            if relative not in originals:
                if not target.is_file():
                    raise PatchApplyError(f"target file does not exist: {relative}")
                original = sandbox.read_text(relative)
                originals[relative] = original
                contents[relative] = original
            current = contents[relative]
            matches = current.count(block.search)
            if matches != 1:
                raise PatchApplyError(
                    f"SEARCH text matched {matches} times in {relative}; expected exactly 1"
                )
            contents[relative] = current.replace(block.search, block.replace, 1)
            if relative not in touched:
                touched.append(relative)

        changed = [name for name in touched if contents[name] != originals[name]]
        written: list[str] = []
        try:
            for name in changed:
                sandbox.write_text(name, contents[name])
                written.append(name)
        except Exception:
            for name in written:
                if originals[name] is None:
                    sandbox.delete(name)
                else:
                    sandbox.write_text(name, str(originals[name]))
            raise
        return PatchResult(files=sorted(changed), blocks_applied=len(blocks))
