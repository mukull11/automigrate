from automigrate import db
from automigrate.report import build_report


def _session_with_runtime_check(tmp_path) -> tuple[object, str]:
    conn = db.init_db(tmp_path / "automigrate.db")
    repo_id = db.create_repository(conn, "numpy-migration-test", tmp_path / "repo")
    dependency_id = db.create_dependency(
        conn,
        repo_id,
        "numpy",
        "1.24.0",
        "2.5.4",
    )
    session_id = db.create_session(conn, repo_id, dependency_id)
    db.update_session(conn, session_id, status="SUCCESSFUL")
    db.add_agent_action(
        conn,
        session_id,
        0,
        "DETECT",
        "runtime_name_check",
        "",
        "runtime name check: checked 2 names [numpy.float_, numpy.int_]; "
        "removed 1 [numpy.float_]",
    )
    return conn, session_id


def test_report_includes_runtime_name_check(tmp_path) -> None:
    conn, session_id = _session_with_runtime_check(tmp_path)
    try:
        result = build_report(conn, session_id, tmp_path / "outputs")
    finally:
        conn.close()

    assert "## Runtime name check" in result.markdown
    assert "numpy 1.24.0 -> 2.5.4" in result.markdown
    assert "checked 2 names [numpy.float_, numpy.int_]" in result.markdown
    assert "removed 1 [numpy.float_]" in result.markdown
    assert len(result.data["runtime_name_checks"]) == 1
    assert "numpy.float_" in result.data["runtime_name_checks"][0]["output_summary"]


def test_report_states_when_runtime_check_is_absent(tmp_path) -> None:
    conn = db.init_db(tmp_path / "automigrate.db")
    repo_id = db.create_repository(conn, "numpy-migration-test", tmp_path / "repo")
    dependency_id = db.create_dependency(
        conn,
        repo_id,
        "numpy",
        "1.24.0",
        "2.5.4",
    )
    session_id = db.create_session(conn, repo_id, dependency_id)
    db.update_session(conn, session_id, status="SUCCESSFUL")
    try:
        result = build_report(conn, session_id, tmp_path / "outputs")
    finally:
        conn.close()

    assert "No runtime name check recorded." in result.markdown
    assert result.data["runtime_name_checks"] == []
