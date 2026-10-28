import json
from pathlib import Path
from types import SimpleNamespace

from automigrate.agent.triage import triage_candidates
from automigrate.auto import NO_CHANGES, SUCCESSFUL, run_auto_migration
from automigrate.ingest.versions import DependencyCandidate
from automigrate.llm.base import LLMAdapter
from automigrate.state import Status


class ScriptedLLM(LLMAdapter):
    name = "scripted"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def complete(self, prompt, *, system=None, stage=None, iteration=0):
        self.prompts.append(prompt)
        return self.responses.pop(0)


class EmptyTriageLLM(LLMAdapter):
    name = "empty-triage"

    def complete(self, prompt, *, system=None, stage=None, iteration=0):
        return json.dumps({"migrations": [], "summary": "none recommended"})


def _candidate(
    name: str,
    usages: int,
    *,
    status: str = "outdated",
) -> DependencyCandidate:
    return DependencyCandidate(
        name=name,
        current_version="1.0.0",
        latest_version="2.0.0",
        source_file="requirements.txt",
        usage_count=usages,
        status=status,
    )


def _response(
    migrations: list[dict] | None = None,
    skipped: list[dict] | None = None,
    summary: str = "test",
) -> str:
    return json.dumps(
        {
            "migrations": migrations or [],
            "skipped": skipped or [],
            "summary": summary,
        }
    )


def _migration(name: str):
    return SimpleNamespace(
        session_id=f"session-{name}",
        workspace_path=Path("outputs") / f"session-{name}" / "workspace",
        status=Status.SUCCESSFUL,
    )


def test_empty_recommendation_falls_back_to_all_outdated() -> None:
    candidates = [
        _candidate("unused", 0),
        _candidate("low", 1),
        _candidate("high", 5),
    ]

    result = triage_candidates(ScriptedLLM([_response()]), candidates)

    assert [item.dependency for item in result.migrations] == [
        "high",
        "low",
        "unused",
    ]
    assert (
        result.summary
        == "LLM recommended none; deterministic fallback selected 3"
    )


def test_empty_recommendation_respects_max_dependencies() -> None:
    candidates = [_candidate("high", 5), _candidate("low", 1)]

    result = triage_candidates(
        ScriptedLLM([_response()]),
        candidates,
        max_dependencies=1,
    )

    assert [item.dependency for item in result.migrations] == ["high"]
    assert (
        result.summary
        == "LLM recommended none; deterministic fallback selected 1"
    )


def test_skip_with_confirmed_usages_is_rejected_then_retried() -> None:
    candidates = [
        _candidate("numpy", 4),
        _candidate("requests", 1),
        _candidate("unused", 0),
    ]
    selected = {
        "dependency": "numpy",
        "from_version": "1.0.0",
        "to_version": "2.0.0",
        "reason": "priority",
    }
    invalid = _response(
        migrations=[selected],
        skipped=[{"dependency": "requests", "reason": "low usage"}],
    )
    valid = _response(
        migrations=[selected],
        skipped=[{"dependency": "unused", "reason": "not imported"}],
        summary="numpy first",
    )
    llm = ScriptedLLM([invalid, valid])

    result = triage_candidates(llm, candidates)

    assert [item.dependency for item in result.migrations] == ["numpy", "requests"]
    assert [(item.dependency, item.reason) for item in result.skipped] == [
        ("unused", "not imported")
    ]
    assert len(llm.prompts) == 2
    assert "rejected" in llm.prompts[1]


def test_omitted_zero_usage_candidate_gets_a_skip_reason() -> None:
    candidates = [_candidate("numpy", 4), _candidate("unused", 0)]
    selected = {
        "dependency": "numpy",
        "from_version": "1.0.0",
        "to_version": "2.0.0",
        "reason": "priority",
    }

    result = triage_candidates(
        ScriptedLLM([_response(migrations=[selected])]),
        candidates,
    )

    assert [item.dependency for item in result.skipped] == ["unused"]
    assert result.skipped[0].reason == "zero usages; not selected by triage"


def test_auto_empty_triage_falls_back_and_respects_cap(
    tmp_path, monkeypatch
) -> None:
    candidates = [_candidate("high", 5), _candidate("low", 1)]
    monkeypatch.setattr(
        "automigrate.auto.build_candidates",
        lambda root, fetcher=None: candidates,
    )
    calls: list[str] = []

    def fake_run_migration(repo, dependency, from_version, to_version, **kwargs):
        calls.append(dependency)
        return _migration(dependency)

    monkeypatch.setattr("automigrate.auto.run_migration", fake_run_migration)

    result = run_auto_migration(
        tmp_path,
        outputs_dir=tmp_path / "out",
        db_path=tmp_path / "db.sqlite",
        triage_llm=EmptyTriageLLM(),
        max_dependencies=1,
    )

    assert result.status == SUCCESSFUL
    assert calls == ["high"]
    assert result.triage is not None
    assert (
        result.triage.summary
        == "LLM recommended none; deterministic fallback selected 1"
    )


def test_auto_no_outdated_candidates_is_no_changes(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "automigrate.auto.build_candidates",
        lambda root, fetcher=None: [_candidate("current", 0, status="current")],
    )
    calls: list[str] = []

    def fake_run_migration(*args, **kwargs):
        calls.append("run")

    monkeypatch.setattr("automigrate.auto.run_migration", fake_run_migration)

    result = run_auto_migration(
        tmp_path,
        outputs_dir=tmp_path / "out",
        db_path=tmp_path / "db.sqlite",
        triage_llm=EmptyTriageLLM(),
    )

    assert result.status == NO_CHANGES
    assert result.triage is None
    assert calls == []
