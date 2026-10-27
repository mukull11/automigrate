from pathlib import Path

from fake_llm import FakeLLM, StaticApiDetector, rename_detector

from automigrate import db
from automigrate.apidiff.models import ApiDiff
from automigrate.graph import run_migration
from automigrate.state import Status


def _repo(tmp_path: Path, source: str, *, with_tests: bool = False) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text(source, encoding="utf-8")
    (repo / "requirements.txt").write_text("lib==1.0\n", encoding="utf-8")
    if with_tests:
        (repo / "tests").mkdir()
        (repo / "tests" / "test_api.py").write_text(
            "from pathlib import Path\n"
            "\n"
            "\n"
            "def test_source():\n"
            "    source = (\n"
            "        Path(__file__).resolve().parents[1] / 'src' / 'app.py'\n"
            "    ).read_text(encoding='utf-8')\n"
            "    assert 'old_api()' in source\n",
            encoding="utf-8",
        )
    return repo


def _fake() -> FakeLLM:
    return FakeLLM(
        plan_text=(
            '{"summary": "rename", "changes": [{"file": "src/app.py", '
            '"old_symbol": "old_api", "new_symbol": "new_api", '
            '"description": "rename", "confidence": 0.9, "cites": []}], '
            '"expected_effect": "tests pass"}'
        ),
        patch_text=(
            "FILE: src/app.py\n<<<<<<< SEARCH\nfrom lib import old_api\n"
            "=======\nfrom lib import new_api\n>>>>>>> REPLACE\n"
            "FILE: src/app.py\n<<<<<<< SEARCH\nold_api()\n"
            "=======\nnew_api()\n>>>>>>> REPLACE\n"
        ),
    )


def _run(tmp_path: Path, repo: Path, llm: FakeLLM, detector):
    return run_migration(
        repo,
        "lib",
        "1.0",
        "2.0",
        outputs_dir=tmp_path / "outputs",
        db_path=tmp_path / "automigrate.db",
        llm_adapter=llm,
        api_detector=detector,
    )


def test_zero_changed_symbol_usages_skips_llm(tmp_path) -> None:
    repo = _repo(
        tmp_path,
        "from lib import stable_api\n\n\ndef call_api():\n    return stable_api()\n",
    )
    llm = _fake()
    result = _run(tmp_path, repo, llm, rename_detector())

    assert llm.calls == []
    assert result.status == Status.REQUIRES_HUMAN_REVIEW
    assert result.state.flag_reason == "no usages of changed API found in repository"
    assert result.commits == []

    conn = db.init_db(tmp_path / "automigrate.db")
    rows = conn.execute(
        "SELECT stage, output_summary FROM agent_action WHERE session_id = ?",
        (result.session_id,),
    ).fetchall()
    conn.close()
    stages = {row["stage"] for row in rows}
    assert "DETECT" in stages
    assert "REPORT" in stages
    assert "RETRIEVE" not in stages
    assert "PLAN" not in stages
    assert "LLM" not in stages
    assert any(
        "no usages of changed API found in repository" in row["output_summary"]
        for row in rows
    )


def test_empty_diff_with_dependency_usages_skips_llm(tmp_path) -> None:
    repo = _repo(
        tmp_path,
        "from lib import stable_api\n\n\ndef call_api():\n    return stable_api()\n",
    )
    llm = _fake()
    result = _run(tmp_path, repo, llm, StaticApiDetector(ApiDiff()))

    assert llm.calls == []
    assert result.status == Status.REQUIRES_HUMAN_REVIEW
    assert result.state.flag_reason == "no usages of changed API found in repository"


def test_repository_without_tests_is_never_successful(tmp_path) -> None:
    repo = _repo(
        tmp_path,
        "from lib import old_api\n\n\ndef call_api():\n    return old_api()\n",
    )
    llm = _fake()
    result = _run(tmp_path, repo, llm, rename_detector())

    assert result.state.has_tests is False
    assert result.status != Status.SUCCESSFUL
    assert result.status == Status.UNVERIFIED
    assert "no tests found" in (result.state.flag_reason or "")
