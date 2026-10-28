import io
import json
import urllib.error

from automigrate.ingest.versions import (
    build_candidates,
    compare_status,
    fetch_latest_stable,
    installed_version,
    latest_stable_from_releases,
)


def _repo(
    tmp_path, requirement: str, source: str = "from lib import old_api\n\nold_api()\n"
) -> object:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text(source, encoding="utf-8")
    (repo / "requirements.txt").write_text(requirement, encoding="utf-8")
    return repo


def _payload(releases: dict) -> io.BytesIO:
    return io.BytesIO(json.dumps({"releases": releases}).encode("utf-8"))


def test_latest_stable_skips_prereleases_and_yanked() -> None:
    releases = {
        "1.0.0": [{"yanked": False}],
        "1.2.0": [{"yanked": False}],
        "1.3.0rc1": [{"yanked": False}],
        "1.4.0": [{"yanked": True}],
        "2.0.0.dev1": [{"yanked": False}],
    }
    assert latest_stable_from_releases(releases) == "1.2.0"


def test_fetch_latest_skips_404() -> None:
    def opener(url, timeout=10):
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)

    assert fetch_latest_stable("private-pkg", opener=opener) is None


def test_fetch_latest_uses_mocked_pypi_json() -> None:
    def opener(url, timeout=10):
        return _payload({"1.0.0": [{"yanked": False}], "1.1.0": [{"yanked": False}]})

    assert fetch_latest_stable("lib", opener=opener) == "1.1.0"


def test_build_candidates_pinned_outdated(tmp_path) -> None:
    repo = _repo(tmp_path, "lib==1.0.0\n")

    def fetcher(name):
        assert name == "lib"
        return "2.0.0"

    candidates = build_candidates(repo, fetcher=fetcher)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.current_version == "1.0.0"
    assert candidate.latest_version == "2.0.0"
    assert candidate.status == "outdated"
    assert candidate.usage_count == 2


def test_build_candidates_unpinned_uses_installed_version(tmp_path) -> None:
    repo = _repo(tmp_path, "lib\n")

    candidates = build_candidates(
        repo,
        fetcher=lambda name: "2.0.0",
        installed=lambda name: "1.0.0",
    )
    assert candidates[0].current_version == "1.0.0"
    assert candidates[0].status == "outdated"


def test_build_candidates_unresolved_is_not_outdated(tmp_path) -> None:
    repo = _repo(tmp_path, "lib\n")

    candidates = build_candidates(
        repo,
        fetcher=lambda name: None,
        installed=lambda name: None,
    )
    assert candidates[0].status == "unresolved"
    assert candidates[0].latest_version is None


def test_build_candidates_current_when_equal(tmp_path) -> None:
    repo = _repo(tmp_path, "lib==2.0.0\n")
    candidates = build_candidates(repo, fetcher=lambda name: "2.0.0")
    assert candidates[0].status == "current"


def test_compare_status_handles_invalid_and_ahead_versions() -> None:
    assert compare_status("1.x", "2.0.0") == "unresolved"
    assert compare_status("3.0.0", "2.0.0") == "current"


def test_installed_version_for_unknown_distribution() -> None:
    assert installed_version("definitely-not-installed-pkg-xyz") is None
