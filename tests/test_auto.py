import json
from pathlib import Path
from types import SimpleNamespace

from automigrate.auto import (
    FAILED,
    NO_CHANGES,
    PARTIAL,
    SUCCESSFUL,
    run_auto_migration,
)
from automigrate.ingest.versions import DependencyCandidate
from automigrate.llm.base import LLMAdapter
from automigrate.state import Status


class TriageLLM(LLMAdapter):
    name = "triage"

    def __init__(self, dependencies: list[str]) -> None:
        self.dependencies = dependencies
        self.calls = 0

    def complete(self, prompt, *, system=None, stage=None, iteration=0):
        self.calls += 1
        migrations = [
            {
                "dependency": name,
                "from_version": "1.0.0",
                "to_version": "2.0.0",
                "reason": "test",
            }
            for name in self.dependencies
        ]
        return json.dumps({"migrations": migrations, "summary": "test summary"})


def _candidate(name: str, *, status: str = "outdated") -> DependencyCandidate:
    return DependencyCandidate(
        name=name,
        current_version="1.0.0",
        latest_version="2.0.0",
        source_file="requirements.txt",
        usage_count=3,
        status=status,
    )


def _migration(name: str, status: Status):
    return SimpleNamespace(
        session_id=f"session-{name}",
        workspace_path=Path("outputs") / f"session-{name}" / "workspace",
        status=status,
    )


def test_calls_run_migration_once_per_selected_dependency(tmp_path, monkeypatch) -> None:
    candidates = [_candidate("numpy"), _candidate("requests"), _candidate("ok", status="current")]
    monkeypatch.setattr(
        "automigrate.auto.build_candidates", lambda root, fetcher=None: candidates
    )
    calls = []

    def fake_run_migration(repo, dependency, from_version, to_version, **kwargs):
        calls.append((repo, dependency, from_version, to_version, kwargs))
        return _migration(dependency, Status.SUCCESSFUL)

    monkeypatch.setattr("automigrate.auto.run_migration", fake_run_migration)
    result = run_auto_migration(
        tmp_path,
        outputs_dir=tmp_path / "out",
        db_path=tmp_path / "db.sqlite",
        triage_llm=TriageLLM(["numpy", "requests"]),
    )

    assert result.status == SUCCESSFUL
    assert [call[1] for call in calls] == ["numpy", "requests"]
    assert calls[0][2:] == ("1.0.0", "2.0.0", calls[0][4])
    assert calls[0][4]["db_path"] == tmp_path / "db.sqlite"
    assert calls[0][4]["outputs_dir"] == (tmp_path / "out").resolve()


def test_failed_dependency_does_not_stop_later_ones(tmp_path, monkeypatch) -> None:
    candidates = [_candidate("a"), _candidate("b")]
    monkeypatch.setattr(
        "automigrate.auto.build_candidates", lambda root, fetcher=None: candidates
    )
    statuses = {"a": Status.FAILED, "b": Status.SUCCESSFUL}
    calls = []

    def fake_run_migration(repo, dependency, from_version, to_version, **kwargs):
        calls.append(dependency)
        return _migration(dependency, statuses[dependency])

    monkeypatch.setattr("automigrate.auto.run_migration", fake_run_migration)
    result = run_auto_migration(
        tmp_path,
        outputs_dir=tmp_path / "out",
        triage_llm=TriageLLM(["a", "b"]),
    )

    assert calls == ["a", "b"]
    assert result.status == PARTIAL
    assert result.runs[0].succeeded is False
    assert result.runs[1].succeeded is True


def test_all_failed_aggregates_to_failed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "automigrate.auto.build_candidates",
        lambda root, fetcher=None: [_candidate("a")],
    )

    def fake_run_migration(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("automigrate.auto.run_migration", fake_run_migration)
    result = run_auto_migration(
        tmp_path,
        outputs_dir=tmp_path / "out",
        triage_llm=TriageLLM(["a"]),
    )
    assert result.status == FAILED
    assert "boom" in (result.runs[0].error or "")


def test_no_outdated_returns_no_changes_without_llm(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "automigrate.auto.build_candidates",
        lambda root, fetcher=None: [_candidate("a", status="current")],
    )
    called = {"run": False}

    def fake_run_migration(*args, **kwargs):
        called["run"] = True

    monkeypatch.setattr("automigrate.auto.run_migration", fake_run_migration)
    result = run_auto_migration(
        tmp_path,
        outputs_dir=tmp_path / "out",
        triage_llm=TriageLLM(["a"]),
    )
    assert result.status == NO_CHANGES
    assert called["run"] is False


def test_triage_selecting_none_returns_no_changes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "automigrate.auto.build_candidates",
        lambda root, fetcher=None: [_candidate("a")],
    )
    called = {"run": False}

    def fake_run_migration(*args, **kwargs):
        called["run"] = True

    monkeypatch.setattr("automigrate.auto.run_migration", fake_run_migration)
    result = run_auto_migration(
        tmp_path,
        outputs_dir=tmp_path / "out",
        triage_llm=TriageLLM([]),
    )
    assert result.status == NO_CHANGES
    assert result.triage is not None
    assert result.triage.migrations == []
    assert called["run"] is False


def test_url_is_cloned_once(tmp_path, monkeypatch) -> None:
    clones = []

    def fake_clone(url, destination, timeout=300):
        clones.append((url, destination))
        destination = Path(destination)
        destination.mkdir(parents=True)
        return destination

    monkeypatch.setattr("automigrate.auto.clone_repository", fake_clone)
    monkeypatch.setattr(
        "automigrate.auto.build_candidates",
        lambda root, fetcher=None: [_candidate("a")],
    )

    def fake_run_migration(repo, dependency, from_version, to_version, **kwargs):
        return _migration(dependency, Status.SUCCESSFUL)

    monkeypatch.setattr("automigrate.auto.run_migration", fake_run_migration)
    result = run_auto_migration(
        "https://github.com/org/repo.git",
        outputs_dir=tmp_path / "out",
        triage_llm=TriageLLM(["a"]),
    )

    assert result.status == SUCCESSFUL
    assert len(clones) == 1
    assert clones[0][0] == "https://github.com/org/repo.git"
