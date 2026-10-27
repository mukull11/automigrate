from pathlib import Path

from fake_llm import FIXTURES, FakeLLM, LocalApiDetector, fixture_patch, fixture_plan

from automigrate.graph import run_migration
from automigrate.state import Status
from automigrate.tools.subprocess import SubprocessRunner
from automigrate.tools.test_runner import PytestRunner

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "rename_sample"


def test_example_and_fixture_exist_and_fixture_is_broken() -> None:
    assert (EXAMPLE / "repo").is_dir()
    assert (EXAMPLE / "docs" / "guide.md").is_file()
    assert (FIXTURES / "repo").is_dir()

    baseline = PytestRunner(SubprocessRunner(FIXTURES / "repo")).run(["tests"])
    assert baseline.failed + baseline.errors > 0


def test_message_fixture_migrates_with_fake_model(tmp_path) -> None:
    result = run_migration(
        FIXTURES / "repo",
        "lib",
        "1.0.0",
        "2.0.0",
        docs_dir=FIXTURES / "docs",
        outputs_dir=tmp_path / "outputs",
        db_path=tmp_path / "automigrate.db",
        llm_adapter=FakeLLM(plan_text=fixture_plan(), patch_text=fixture_patch()),
        api_detector=LocalApiDetector(
            FIXTURES / "fake_lib" / "old_version",
            FIXTURES / "fake_lib" / "new_version",
        ),
    )
    assert result.status.value == "SUCCESSFUL"
    assert result.state.api_diff is not None
    assert result.state.api_diff.renamed
    assert result.state.api_usages


def test_no_tests_reports_unverified(tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text(
        "from lib import old_api\n\nold_api()\n", encoding="utf-8"
    )
    (repo / "requirements.txt").write_text("lib==1.0.0\n", encoding="utf-8")
    plan = (
        '{"summary": "rename", "changes": [{"file": "src/app.py", '
        '"old_symbol": "old_api", "new_symbol": "new_api", '
        '"description": "rename", "confidence": 0.9, "cites": []}], '
        '"expected_effect": "no tests"}'
    )
    patch = (
        "FILE: src/app.py\n<<<<<<< SEARCH\nfrom lib import old_api\n=======\n"
        "from lib import new_api\n>>>>>>> REPLACE\n"
    )
    patch += (
        "FILE: src/app.py\n<<<<<<< SEARCH\nold_api()\n=======\nnew_api()\n"
        ">>>>>>> REPLACE\n"
    )
    result = run_migration(
        repo,
        "lib",
        "1.0.0",
        "2.0.0",
        outputs_dir=tmp_path / "outputs",
        db_path=tmp_path / "automigrate.db",
        llm_adapter=FakeLLM(plan_text=plan, patch_text=patch),
        api_detector=LocalApiDetector(
            FIXTURES / "fake_lib" / "old_version",
            FIXTURES / "fake_lib" / "new_version",
        ),
    )
    assert result.state.baseline.passed + result.state.baseline.failed == 0
    assert result.state.status == Status.UNVERIFIED
