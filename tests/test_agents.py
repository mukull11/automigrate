import json
from pathlib import Path

import pytest

from automigrate.agent.diagnoser import Diagnoser, DiagnosisError, parse_diagnosis
from automigrate.agent.patcher import Patcher, PatchError, parse_patch
from automigrate.agent.planner import (
    Planner,
    PlanValidationError,
    is_insufficient_evidence_plan,
    parse_plan,
)
from automigrate.llm.base import LLMAdapter
from automigrate.llm.mock import MockLLM
from automigrate.state import (
    AgentState,
    Evidence,
    Plan,
    PlannedChange,
    TestRunResult,
    Usage,
)
from automigrate.tools.filesystem import Sandbox


def make_state(tmp_path) -> AgentState:
    base = Path(tmp_path) if tmp_path is not None else Path(".")
    return AgentState(
        session_id="s1",
        repo_path=str(base / "repo"),
        workspace_path=str(base / "ws"),
        dependency="lib",
        from_version="1.0",
        to_version="2.0",
        usages=[
            Usage(
                file="src/app.py",
                line=1,
                symbol="old_api",
                kind="call",
                snippet="old_api()",
            )
        ],
        baseline=TestRunResult(passed=1, failed=1),
        latest=TestRunResult(
            passed=1, failed=1, failed_test_names=["tests/test_app.py::test_it"]
        ),
    )


def make_plan(*, cites: list[str] | None = None, file: str = "src/app.py") -> Plan:
    return Plan(
        summary="rename old_api",
        expected_effect="tests pass",
        changes=[
            PlannedChange(
                file=file,
                old_symbol="old_api",
                new_symbol="new_api",
                description="rename",
                confidence=0.9,
                cites=cites or ["c1"],
            )
        ],
    )


class QueuedLLM(LLMAdapter):
    name = "queued"

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls = 0

    def complete(self, prompt, *, system=None, stage=None, iteration=0) -> str:
        self.calls += 1
        return self.responses.pop(0)


def test_parse_patch_multiple_blocks() -> None:
    text = (
        "FILE: a.py\n"
        "<<<<<<< SEARCH\n"
        "old\n"
        "=======\n"
        "new\n"
        ">>>>>>> REPLACE\n"
        "FILE: b.py\n"
        "<<<<<<< SEARCH\n"
        "x\n"
        "=======\n"
        "y\n"
        ">>>>>>> REPLACE\n"
    )
    blocks = parse_patch(text)
    assert len(blocks) == 2
    assert blocks[0].file == "a.py"
    assert blocks[1].search == "x"


def test_patcher_applies_multiple_blocks(tmp_path) -> None:
    sandbox = Sandbox(tmp_path)
    sandbox.write_text("a.py", "old()\n")
    sandbox.write_text("b.py", "call old_api()\n")
    patch = (
        "FILE: a.py\n<<<<<<< SEARCH\nold()\n=======\nnew()\n>>>>>>> REPLACE\n"
        "FILE: b.py\n<<<<<<< SEARCH\ncall old_api()\n=======\ncall new_api()\n"
        ">>>>>>> REPLACE\n"
    )
    result = Patcher(MockLLM(patch_text=patch)).apply(
        make_state(tmp_path), make_plan(), sandbox
    )
    assert result.blocks_applied == 2
    assert result.files == ["a.py", "b.py"]
    assert sandbox.read_text("a.py") == "new()\n"
    assert sandbox.read_text("b.py") == "call new_api()\n"


def test_patcher_rejects_no_match_and_leaves_file_unchanged(tmp_path) -> None:
    sandbox = Sandbox(tmp_path)
    sandbox.write_text("src/app.py", "old_api()\n")
    patch = (
        "FILE: src/app.py\n<<<<<<< SEARCH\nmissing()\n=======\nnew()\n>>>>>>> REPLACE\n"
    )
    llm = MockLLM(patch_text=patch)
    with pytest.raises(PatchError):
        Patcher(llm).apply(make_state(tmp_path), make_plan(), sandbox)
    assert sandbox.read_text("src/app.py") == "old_api()\n"
    assert len(llm.calls) == 2


def test_patcher_rejects_multi_match(tmp_path) -> None:
    sandbox = Sandbox(tmp_path)
    sandbox.write_text("src/app.py", "old_api()\nold_api()\n")
    patch = (
        "FILE: src/app.py\n<<<<<<< SEARCH\nold_api()\n=======\nnew_api()\n"
        ">>>>>>> REPLACE\n"
    )
    with pytest.raises(PatchError, match="matched 2 times"):
        Patcher(MockLLM(patch_text=patch)).apply(
            make_state(tmp_path), make_plan(), sandbox
        )
    assert sandbox.read_text("src/app.py") == "old_api()\nold_api()\n"


def test_patcher_is_atomic_across_blocks(tmp_path) -> None:
    sandbox = Sandbox(tmp_path)
    sandbox.write_text("a.py", "old()\n")
    sandbox.write_text("b.py", "old_api()\n")
    patch = (
        "FILE: a.py\n<<<<<<< SEARCH\nold()\n=======\nnew()\n>>>>>>> REPLACE\n"
        "FILE: b.py\n<<<<<<< SEARCH\nmissing\n=======\nnew\n>>>>>>> REPLACE\n"
    )
    with pytest.raises(PatchError):
        Patcher(MockLLM(patch_text=patch)).apply(
            make_state(tmp_path), make_plan(), sandbox
        )
    assert sandbox.read_text("a.py") == "old()\n"
    assert sandbox.read_text("b.py") == "old_api()\n"


def test_patcher_rejects_test_file_edits(tmp_path) -> None:
    sandbox = Sandbox(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("assert False\n", encoding="utf-8")
    patch = (
        "FILE: tests/test_app.py\n<<<<<<< SEARCH\nassert False\n=======\nassert True\n"
        ">>>>>>> REPLACE\n"
    )
    with pytest.raises(PatchError, match="test file"):
        Patcher(MockLLM(patch_text=patch)).apply(
            make_state(tmp_path), make_plan(file="tests/test_app.py"), sandbox
        )
    assert (tmp_path / "tests" / "test_app.py").read_text(encoding="utf-8") == "assert False\n"


def test_parse_patch_rejects_bad_format() -> None:
    with pytest.raises(PatchError):
        parse_patch("FILE: a.py\nold\n")
    with pytest.raises(PatchError):
        parse_patch("")


def test_planner_parses_plan_and_citations() -> None:
    state = make_state(tmp_path=None)
    state.evidence = [
        Evidence(
            chunk_id="c1",
            source_doc="guide.md",
            heading_path="Renames",
            score=1.0,
            text="old_api is new_api",
        )
    ]
    plan_text = json.dumps(make_plan().model_dump())
    plan = Planner(MockLLM(plan_text=plan_text)).propose(state)
    assert plan.changes[0].new_symbol == "new_api"
    assert parse_plan("```json\n" + plan_text + "\n```", {"c1"}).summary


def test_planner_rejects_unknown_citations_after_retry() -> None:
    state = make_state(tmp_path=None)
    state.evidence = [
        Evidence(
            chunk_id="c1",
            source_doc="guide.md",
            heading_path="Renames",
            score=1.0,
            text="old_api is new_api",
        )
    ]
    bad = json.dumps(make_plan(cites=["unknown"]).model_dump())
    llm = MockLLM(plan_text=bad)
    with pytest.raises(PlanValidationError):
        Planner(llm).propose(state)
    assert len(llm.calls) == 2


def test_planner_retries_then_succeeds(monkeypatch) -> None:
    state = make_state(tmp_path=None)
    state.evidence = [
        Evidence(
            chunk_id="c1",
            source_doc="guide.md",
            heading_path="Renames",
            score=1.0,
            text="old_api is new_api",
        )
    ]
    good = json.dumps(make_plan().model_dump())
    llm = QueuedLLM(["not json", good])
    assert Planner(llm).propose(state).summary == "rename old_api"
    assert llm.calls == 2


def test_planner_returns_insufficient_evidence_for_undocumented_replacement() -> None:
    state = make_state(tmp_path=None)
    state.evidence = [
        Evidence(
            chunk_id="c1",
            source_doc="guide.md",
            heading_path="Behavior changes",
            score=1.0,
            text="old_api behavior changed in 2.0; no documented replacement exists.",
        )
    ]
    plan_text = json.dumps(make_plan(cites=["c1"]).model_dump())
    llm = MockLLM(plan_text=plan_text)

    result = Planner(llm).propose(state)

    assert is_insufficient_evidence_plan(result)
    assert result.changes == []
    assert "new_api" in result.summary
    assert len(llm.calls) == 2


def test_diagnoser_parses_result() -> None:
    state = make_state(tmp_path=None)
    llm = MockLLM(
        diagnosis_text=json.dumps(
            {"diagnosis": "MIGRATION_RELATED", "reason": "signature changed"}
        )
    )
    result = Diagnoser(llm).diagnose(state)
    assert result.diagnosis.value == "MIGRATION_RELATED"


def test_parse_diagnosis_rejects_unknown_value() -> None:
    with pytest.raises(DiagnosisError):
        parse_diagnosis('{"diagnosis": "NOT_A_DIAGNOSIS"}')
