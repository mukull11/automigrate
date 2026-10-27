from pathlib import Path

from fake_llm import FakeLLM, rename_detector

from automigrate.graph import run_migration
from automigrate.state import Status


def make_repo(tmp_path: Path, *, test_source: str | None) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text(
        "from lib import old_api\n\n\ndef call_api():\n    return old_api()\n",
        encoding="utf-8",
    )
    (repo / "requirements.txt").write_text("lib==1.0\n", encoding="utf-8")
    (repo / "tests").mkdir()
    if test_source is not None:
        (repo / "tests" / "test_app.py").write_text(
            test_source, encoding="utf-8"
        )
    return repo


def run_repo(tmp_path: Path, repo: Path):
    return run_migration(
        repo,
        "lib",
        "1.0",
        "2.0",
        outputs_dir=tmp_path / "outputs",
        db_path=tmp_path / "automigrate.db",
        llm_adapter=FakeLLM(),
        api_detector=rename_detector(),
    )


def test_import_error_in_tests_records_unverified(tmp_path) -> None:
    repo = make_repo(
        tmp_path,
        test_source="import module_that_does_not_exist\n",
    )
    result = run_repo(tmp_path, repo)

    assert result.status == Status.UNVERIFIED
    reason = result.state.flag_reason or ""
    assert "tests not collected" in reason
    assert "ModuleNotFoundError" in reason
    assert result.commits == []


def test_zero_collected_tests_records_unverified(tmp_path) -> None:
    repo = make_repo(tmp_path, test_source=None)
    (repo / "tests" / "README.md").write_text("no tests here\n", encoding="utf-8")
    result = run_repo(tmp_path, repo)

    assert result.status == Status.UNVERIFIED
    reason = result.state.flag_reason or ""
    assert "tests not collected" in reason
    assert result.commits == []
