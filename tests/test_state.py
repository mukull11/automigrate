import pytest
from pydantic import ValidationError

from automigrate.state import (
    AgentState,
    Evidence,
    Plan,
    PlannedChange,
    TestRunResult,
    Usage,
)


def test_usage_kind_is_constrained() -> None:
    usage = Usage(file="src/a.py", line=3, symbol="old_api", kind="call", snippet="old_api()")
    assert usage.line == 3
    with pytest.raises(ValidationError):
        Usage(file="src/a.py", line=1, symbol="x", kind="other", snippet="x")


def test_evidence_text_limit() -> None:
    with pytest.raises(ValidationError):
        Evidence(
            chunk_id="c1",
            source_doc="d.md",
            heading_path="h",
            score=1.0,
            text="x" * 801,
        )


def test_plan_citation_validation() -> None:
    plan = Plan(
        summary="rename",
        expected_effect="tests pass",
        changes=[
            PlannedChange(
                file="src/a.py",
                old_symbol="old_api",
                new_symbol="new_api",
                description="rename",
                confidence=0.9,
                cites=["c1"],
            )
        ],
    )
    assert plan.validate_citations({"c1", "c2"})
    assert not plan.validate_citations({"c2"})


def test_planned_change_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        PlannedChange(
            file="a.py",
            old_symbol="x",
            description="d",
            confidence=1.5,
        )


def test_agent_state_defaults() -> None:
    state = AgentState(
        session_id="s1",
        repo_path="repo",
        workspace_path="ws",
        dependency="lib",
        from_version="1.0",
        to_version="2.0",
    )
    assert state.iteration == 0
    assert state.max_iterations == 5
    assert state.baseline == TestRunResult()
    assert state.usages == []
