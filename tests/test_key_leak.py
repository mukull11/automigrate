import json
import logging
import shutil
from pathlib import Path

from fake_llm import FakeLLM, rename_detector

from automigrate import db
from automigrate.graph import run_migration
from automigrate.report import write_report

ROOT = Path(__file__).resolve().parent
CASE = ROOT / "fixtures" / "rename_sample"
SECRET = "sk-unique-key-that-must-never-leak-7f31c"


def test_explicit_key_does_not_leak_to_reports_logs_or_database(
    tmp_path, caplog
) -> None:
    caplog.set_level(logging.DEBUG)
    repo = tmp_path / "repo"
    shutil.copytree(CASE / "repo", repo)

    result = run_migration(
        repo,
        "lib",
        "1.x",
        "2.x",
        docs_dir=CASE / "docs",
        outputs_dir=tmp_path / "outputs",
        db_path=tmp_path / "automigrate.db",
        api_key=SECRET,
        llm_adapter=FakeLLM(
            plan_text=(CASE / "plan.json").read_text(encoding="utf-8"),
            patch_text=(CASE / "patch.txt").read_text(encoding="utf-8"),
        ),
        api_detector=rename_detector(),
    )

    conn = db.init_db(tmp_path / "automigrate.db")
    try:
        actions = [dict(row) for row in conn.execute("SELECT * FROM agent_action")]
        report = write_report(conn, result.session_id, tmp_path / "outputs")
    finally:
        conn.close()

    directory = tmp_path / "outputs" / result.session_id
    markdown = (directory / "report.md").read_text(encoding="utf-8")
    raw_json = (directory / "report.json").read_text(encoding="utf-8")

    assert SECRET not in report.markdown
    assert SECRET not in markdown
    assert SECRET not in raw_json
    assert SECRET not in json.dumps(actions)
    assert SECRET not in caplog.text
