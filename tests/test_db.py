from automigrate import db


def test_init_db_creates_schema(tmp_path) -> None:
    conn = db.init_db(tmp_path / "test.db")
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    expected = {
        "repository",
        "dependency",
        "migration_session",
        "source_file",
        "agent_action",
        "test_run",
        "migration_change",
        "doc_chunk",
        "migration_report",
        "schema_version",
    }
    assert expected <= tables
    assert db.get_schema_version(conn) == db.SCHEMA_VERSION
    conn.close()


def test_repository_session_roundtrip(tmp_path) -> None:
    conn = db.init_db(tmp_path / "test.db")
    repo_id = db.create_repository(conn, "demo", tmp_path / "repo")
    dep_id = db.create_dependency(conn, repo_id, "lib", "1.0", "2.0")
    session_id = db.create_session(conn, repo_id, dep_id)
    db.update_session(conn, session_id, status="SUCCESSFUL", iterations=2)

    session = db.get_session(conn, session_id)
    assert session is not None
    assert session["status"] == "SUCCESSFUL"
    assert session["iterations"] == 2
    assert db.get_dependency(conn, dep_id)["name"] == "lib"
    assert db.get_repository(conn, repo_id)["name"] == "demo"
    conn.close()


def test_activity_rows(tmp_path) -> None:
    conn = db.init_db(tmp_path / "test.db")
    repo_id = db.create_repository(conn, "demo", "repo")
    dep_id = db.create_dependency(conn, repo_id, "lib", "1.0", "2.0")
    session_id = db.create_session(conn, repo_id, dep_id)

    db.add_agent_action(conn, session_id, 0, "DETECT", "usage_scan", "repo", "1 usage", 5)
    db.add_test_run(
        conn,
        session_id,
        0,
        passed=3,
        failed=1,
        errors=0,
        ruff_issues=2,
        duration_s=1.5,
        failed_tests_json='["tests/test_a.py::test_x"]',
    )
    db.add_migration_change(conn, session_id, 1, "src/a.py", "rename", "diff", "abc123")
    db.add_source_file(conn, session_id, "src/a.py", "deadbeef", "2026-01-01")
    db.save_report(conn, session_id, "SUCCESSFUL", "{}", "outputs/x/report.md")

    assert conn.execute("SELECT COUNT(*) AS n FROM agent_action").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM test_run").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM migration_change").fetchone()["n"] == 1
    assert db.get_report(conn, session_id)["status"] == "SUCCESSFUL"
    assert len(db.list_sessions(conn)) == 1
    conn.close()


def test_doc_chunks(tmp_path) -> None:
    conn = db.init_db(tmp_path / "test.db")
    db.upsert_doc_chunk(
        conn,
        chunk_id="abc",
        source_doc="migration.md",
        heading_path="Guide > Renames",
        text="old_api is now new_api",
        dependency="lib",
        version_hint="2.0",
        sha256="sha",
    )
    db.upsert_doc_chunk(
        conn,
        chunk_id="abc",
        source_doc="migration.md",
        heading_path="Guide > Renames",
        text="updated text",
        dependency="lib",
        version_hint="2.0",
        sha256="sha2",
    )
    chunks = db.list_doc_chunks(conn, "lib")
    assert len(chunks) == 1
    assert chunks[0]["text"] == "updated text"
    assert db.get_doc_chunk(conn, "abc")["source_doc"] == "migration.md"
    assert db.list_doc_chunks(conn, "other") == []
    conn.close()
