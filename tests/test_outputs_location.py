import shutil
import uuid

from automigrate import db
from automigrate.config import PROJECT_ROOT, get_settings
from automigrate.report import write_report


def test_outputs_stay_in_project_folder_from_another_cwd(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    assert get_settings().outputs_dir == PROJECT_ROOT / "outputs"

    conn = db.init_db(tmp_path / "automigrate.db")
    session_id = f"cwd-check-{uuid.uuid4().hex}"
    repo_id = db.create_repository(conn, "repo", tmp_path / "repo")
    dependency_id = db.create_dependency(conn, repo_id, "lib", "1.0", "2.0")
    db.create_session(conn, repo_id, dependency_id, "SUCCESSFUL", session_id)
    try:
        result = write_report(conn, session_id)

        expected = PROJECT_ROOT / "outputs" / session_id / "report.md"
        assert result.report_path == expected
        assert expected.is_file()
        assert not (tmp_path / "outputs").exists()
    finally:
        conn.close()
        shutil.rmtree(PROJECT_ROOT / "outputs" / session_id, ignore_errors=True)
