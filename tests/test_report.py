from pathlib import Path

from typer.testing import CliRunner

from automigrate.cli import app
from automigrate.graph import run_migration

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "benchmarks" / "cases" / "01_rename"


def test_report_command_rerenders_status_and_diff(tmp_path) -> None:
    result = run_migration(
        CASE / "repo",
        "lib",
        "1.x",
        "2.x",
        docs_dir=CASE / "docs",
        llm="mock",
        outputs_dir=tmp_path / "outputs",
        db_path=tmp_path / "automigrate.db",
    )
    assert result.status.value == "SUCCESSFUL"

    runner = CliRunner()
    invoked = runner.invoke(
        app,
        [
            "report",
            result.session_id,
            "--db",
            str(tmp_path / "automigrate.db"),
            "--out",
            str(tmp_path / "outputs"),
        ],
    )
    assert invoked.exit_code == 0, invoked.output
    assert "Status: SUCCESSFUL" in invoked.stdout
    assert "new_api" in invoked.stdout

    report_path = tmp_path / "outputs" / result.session_id / "report.md"
    assert report_path.is_file()
    text = report_path.read_text(encoding="utf-8")
    assert "Status: SUCCESSFUL" in text
    assert "```diff" in text
    assert "new_api" in text


def test_report_command_rejects_unknown_session(tmp_path) -> None:
    runner = CliRunner()
    invoked = runner.invoke(
        app,
        [
            "report",
            "missing-session",
            "--db",
            str(tmp_path / "automigrate.db"),
            "--out",
            str(tmp_path / "outputs"),
        ],
    )
    assert invoked.exit_code == 1
    assert "unknown session" in invoked.output
