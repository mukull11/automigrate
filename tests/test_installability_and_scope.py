import json

from automigrate.auto import UP_TO_DATE, run_auto_migration
from automigrate.ingest.versions import (
    DependencyCandidate,
    build_candidates,
    latest_installable_from_releases,
)
from automigrate.llm.base import LLMAdapter


def _repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text(
        "from lib import old_api\n\nold_api()\n", encoding="utf-8"
    )
    (repo / "requirements.txt").write_text("lib==1.0.0\n", encoding="utf-8")
    return repo


def _candidate(*, breaking_usages):
    return DependencyCandidate(
        name="lib",
        current_version="1.0.0",
        latest_version="2.0.0",
        source_file="requirements.txt",
        usage_count=2,
        status="outdated",
        breaking_usages=breaking_usages,
    )


class SelectingLLM(LLMAdapter):
    name = "selecting"

    def complete(self, prompt, *, system=None, stage=None, iteration=0):
        return json.dumps(
            {
                "migrations": [
                    {
                        "dependency": "lib",
                        "from_version": "1.0.0",
                        "to_version": "2.0.0",
                        "reason": "test",
                    }
                ],
                "summary": "select lib",
            }
        )


def test_latest_installable_skips_newer_python_incompatible_release() -> None:
    releases = {
        "2.4.6": [
            {
                "yanked": False,
                "packagetype": "bdist_wheel",
                "requires_python": ">=3.8",
            }
        ],
        "2.5.4": [
            {
                "yanked": False,
                "packagetype": "bdist_wheel",
                "requires_python": ">=3.12",
            }
        ],
    }

    release = latest_installable_from_releases(releases, python_version="3.11")

    assert release.version == "2.4.6"
    assert release.skipped == [
        "2.5.4 skipped: requires Python >=3.12, running 3.11"
    ]


def test_outdated_without_breaking_usage_is_not_migrated(
    tmp_path, monkeypatch
) -> None:
    candidate = _candidate(breaking_usages=[])
    monkeypatch.setattr(
        "automigrate.auto.build_candidates",
        lambda root, fetcher=None: [candidate],
    )
    calls = []

    def fake_run_migration(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr("automigrate.auto.run_migration", fake_run_migration)

    result = run_auto_migration(
        tmp_path,
        outputs_dir=tmp_path / "out",
        triage_llm=SelectingLLM(),
    )

    assert calls == []
    assert result.status == UP_TO_DATE
    assert "up to date for this code" in result.summary.lower()
    assert result.triage is not None
    assert result.triage.skipped[0].reason == "outdated, no breaking usage found"


def test_target_overrides_pypi_lookup(tmp_path) -> None:
    repo = _repo(tmp_path)

    candidates = build_candidates(
        repo,
        fetcher=lambda name: "2.5.4",
        target="lib==2.4.6",
    )

    assert candidates[0].latest_version == "2.4.6"
    assert candidates[0].status == "outdated"
