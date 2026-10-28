import os
import shutil
import uuid
from pathlib import Path

from automigrate import db
from automigrate.config import (
    PROJECT_ROOT,
    WINDOWS_OUTPUTS_DIR,
    get_settings,
)
from automigrate.report import write_report


def _expected_default_outputs() -> Path:
    if os.name == "nt":
        if WINDOWS_OUTPUTS_DIR.exists():
            return WINDOWS_OUTPUTS_DIR
        return Path(os.environ["LOCALAPPDATA"]) / "AutoMigrate" / "outputs"
    return Path.home() / ".automigrate" / "outputs"


def test_outputs_default_to_d_drive(monkeypatch) -> None:
    monkeypatch.delenv("AUTOMIGRATE_OUTPUTS_DIR", raising=False)
    monkeypatch.delenv("AUTOMIGRATE_DB", raising=False)
    settings = get_settings(_env_file=None)

    assert settings.outputs_dir == _expected_default_outputs()
    assert settings.db_path == settings.outputs_dir / "automigrate.db"
    if os.name == "nt" and WINDOWS_OUTPUTS_DIR.exists():
        assert settings.outputs_dir == Path("D:/AutoMigrateOutputs")


def test_outputs_env_override_keeps_reports_outside_repo(
    tmp_path, monkeypatch
) -> None:
    outputs = tmp_path / "custom-outputs"
    monkeypatch.setenv("AUTOMIGRATE_OUTPUTS_DIR", str(outputs))
    monkeypatch.delenv("AUTOMIGRATE_DB", raising=False)
    monkeypatch.chdir(tmp_path)

    settings = get_settings(_env_file=None)
    assert settings.outputs_dir == outputs
    assert settings.db_path == outputs / "automigrate.db"

    conn = db.init_db(tmp_path / "automigrate.db")
    session_id = f"cwd-check-{uuid.uuid4().hex}"
    repo_id = db.create_repository(conn, "repo", tmp_path / "repo")
    dependency_id = db.create_dependency(conn, repo_id, "lib", "1.0", "2.0")
    db.create_session(conn, repo_id, dependency_id, "SUCCESSFUL", session_id)
    try:
        result = write_report(conn, session_id)

        expected = outputs / session_id / "report.md"
        assert result.report_path == expected
        assert expected.is_file()
        assert not (tmp_path / "outputs").exists()
        assert not (PROJECT_ROOT / "outputs" / session_id).exists()
    finally:
        conn.close()
        shutil.rmtree(outputs / session_id, ignore_errors=True)


def test_db_env_override(tmp_path, monkeypatch) -> None:
    database = tmp_path / "older" / "automigrate.db"
    monkeypatch.setenv("AUTOMIGRATE_DB", str(database))
    settings = get_settings(_env_file=None)
    assert settings.db_path == database
