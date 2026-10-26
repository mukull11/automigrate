"""SQLite persistence for AutoMigrate.

All SQL statements live in this module. Callers use the functions below.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_DB_PATH = Path("automigrate.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS repository (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'python',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS dependency (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL REFERENCES repository(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    source_version TEXT NOT NULL,
    target_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS migration_session (
    id TEXT PRIMARY KEY,
    repo_id INTEGER NOT NULL REFERENCES repository(id) ON DELETE CASCADE,
    dependency_id INTEGER NOT NULL REFERENCES dependency(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at TEXT,
    status TEXT NOT NULL,
    iterations INTEGER NOT NULL DEFAULT 0,
    final_reason TEXT
);

CREATE TABLE IF NOT EXISTS source_file (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES migration_session(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    modified TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_action (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES migration_session(id) ON DELETE CASCADE,
    iteration INTEGER NOT NULL,
    stage TEXT NOT NULL,
    action_type TEXT NOT NULL,
    input_summary TEXT,
    output_summary TEXT,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS test_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES migration_session(id) ON DELETE CASCADE,
    iteration INTEGER NOT NULL,
    passed INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    ruff_issues INTEGER NOT NULL DEFAULT 0,
    duration_s REAL NOT NULL DEFAULT 0,
    failed_tests_json TEXT,
    traceback TEXT
);

CREATE TABLE IF NOT EXISTS migration_change (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES migration_session(id) ON DELETE CASCADE,
    iteration INTEGER NOT NULL,
    file TEXT NOT NULL,
    description TEXT,
    diff TEXT,
    commit_sha TEXT
);

CREATE TABLE IF NOT EXISTS doc_chunk (
    id TEXT PRIMARY KEY,
    source_doc TEXT NOT NULL,
    heading_path TEXT NOT NULL,
    text TEXT NOT NULL,
    dependency TEXT NOT NULL,
    version_hint TEXT,
    sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS migration_report (
    session_id TEXT PRIMARY KEY REFERENCES migration_session(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    report_path TEXT
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a SQLite connection with foreign keys enabled."""

    target = str(db_path)
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Create the schema if needed and return an open connection."""

    conn = connect(db_path)
    conn.executescript(SCHEMA)
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    conn.commit()
    return conn


def create_repository(
    conn: sqlite3.Connection,
    name: str,
    path: str | Path,
    language: str = "python",
) -> int:
    cursor = conn.execute(
        "INSERT INTO repository (name, path, language) VALUES (?, ?, ?)",
        (name, str(path), language),
    )
    conn.commit()
    return int(cursor.lastrowid)


def get_repository(conn: sqlite3.Connection, repo_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM repository WHERE id = ?", (repo_id,)).fetchone()
    return dict(row) if row else None


def create_dependency(
    conn: sqlite3.Connection,
    repo_id: int,
    name: str,
    source_version: str,
    target_version: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO dependency (repo_id, name, source_version, target_version)
        VALUES (?, ?, ?, ?)
        """,
        (repo_id, name, source_version, target_version),
    )
    conn.commit()
    return int(cursor.lastrowid)


def get_dependency(conn: sqlite3.Connection, dependency_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM dependency WHERE id = ?", (dependency_id,)).fetchone()
    return dict(row) if row else None


def create_session(
    conn: sqlite3.Connection,
    repo_id: int,
    dependency_id: int,
    status: str = "RUNNING",
    session_id: str | None = None,
) -> str:
    sid = session_id or uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO migration_session (id, repo_id, dependency_id, status)
        VALUES (?, ?, ?, ?)
        """,
        (sid, repo_id, dependency_id, status),
    )
    conn.commit()
    return sid


def update_session(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    status: str | None = None,
    iterations: int | None = None,
    final_reason: str | None = None,
    ended_at: str | None = None,
) -> None:
    fields: list[str] = []
    values: list[Any] = []
    for column, value in (
        ("status", status),
        ("iterations", iterations),
        ("final_reason", final_reason),
        ("ended_at", ended_at),
    ):
        if value is not None:
            fields.append(f"{column} = ?")
            values.append(value)
    if not fields:
        return
    values.append(session_id)
    conn.execute(f"UPDATE migration_session SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()


def get_session(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM migration_session WHERE id = ?", (session_id,)
    ).fetchone()
    return dict(row) if row else None


def list_sessions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM migration_session ORDER BY started_at DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def add_agent_action(
    conn: sqlite3.Connection,
    session_id: str,
    iteration: int,
    stage: str,
    action_type: str,
    input_summary: str = "",
    output_summary: str = "",
    latency_ms: int = 0,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO agent_action
            (session_id, iteration, stage, action_type, input_summary, output_summary, latency_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (session_id, iteration, stage, action_type, input_summary, output_summary, latency_ms),
    )
    conn.commit()
    return int(cursor.lastrowid)


def add_test_run(
    conn: sqlite3.Connection,
    session_id: str,
    iteration: int,
    *,
    passed: int,
    failed: int,
    errors: int,
    ruff_issues: int,
    duration_s: float,
    failed_tests_json: str = "[]",
    traceback: str | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO test_run
            (session_id, iteration, passed, failed, errors, ruff_issues, duration_s,
             failed_tests_json, traceback)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            iteration,
            passed,
            failed,
            errors,
            ruff_issues,
            duration_s,
            failed_tests_json,
            traceback,
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def add_migration_change(
    conn: sqlite3.Connection,
    session_id: str,
    iteration: int,
    file: str,
    description: str,
    diff: str,
    commit_sha: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO migration_change
            (session_id, iteration, file, description, diff, commit_sha)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (session_id, iteration, file, description, diff, commit_sha),
    )
    conn.commit()
    return int(cursor.lastrowid)


def add_source_file(
    conn: sqlite3.Connection,
    session_id: str,
    path: str,
    sha256: str,
    modified: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO source_file (session_id, path, sha256, modified)
        VALUES (?, ?, ?, ?)
        """,
        (session_id, path, sha256, modified),
    )
    conn.commit()
    return int(cursor.lastrowid)


def upsert_doc_chunk(
    conn: sqlite3.Connection,
    *,
    chunk_id: str,
    source_doc: str,
    heading_path: str,
    text: str,
    dependency: str,
    version_hint: str | None,
    sha256: str,
) -> None:
    conn.execute(
        """
        INSERT INTO doc_chunk
            (id, source_doc, heading_path, text, dependency, version_hint, sha256)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            source_doc = excluded.source_doc,
            heading_path = excluded.heading_path,
            text = excluded.text,
            dependency = excluded.dependency,
            version_hint = excluded.version_hint,
            sha256 = excluded.sha256
        """,
        (chunk_id, source_doc, heading_path, text, dependency, version_hint, sha256),
    )
    conn.commit()


def list_doc_chunks(
    conn: sqlite3.Connection, dependency: str | None = None
) -> list[dict[str, Any]]:
    if dependency is None:
        rows = conn.execute("SELECT * FROM doc_chunk").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM doc_chunk WHERE dependency = ?", (dependency,)
        ).fetchall()
    return [dict(row) for row in rows]


def get_doc_chunk(conn: sqlite3.Connection, chunk_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM doc_chunk WHERE id = ?", (chunk_id,)).fetchone()
    return dict(row) if row else None


def list_doc_chunks_by_source(
    conn: sqlite3.Connection, source_doc: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM doc_chunk WHERE source_doc = ?", (source_doc,)
    ).fetchall()
    return [dict(row) for row in rows]


def delete_doc_chunks_by_source(conn: sqlite3.Connection, source_doc: str) -> None:
    conn.execute("DELETE FROM doc_chunk WHERE source_doc = ?", (source_doc,))
    conn.commit()


def list_indexed_sources(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT source_doc FROM doc_chunk").fetchall()
    return sorted(row["source_doc"] for row in rows)


def save_report(
    conn: sqlite3.Connection,
    session_id: str,
    status: str,
    metrics_json: str,
    report_path: str,
) -> None:
    conn.execute(
        """
        INSERT INTO migration_report (session_id, status, metrics_json, report_path)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            status = excluded.status,
            metrics_json = excluded.metrics_json,
            report_path = excluded.report_path
        """,
        (session_id, status, metrics_json, report_path),
    )
    conn.commit()


def get_report(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM migration_report WHERE session_id = ?", (session_id,)
    ).fetchone()
    return dict(row) if row else None


def get_session_summary(
    conn: sqlite3.Connection, session_id: str
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT s.id, s.status, s.iterations, s.final_reason, s.started_at, s.ended_at,
               s.repo_id, s.dependency_id,
               r.name AS repo_name, r.path AS repo_path,
               d.name AS dependency, d.source_version, d.target_version
        FROM migration_session AS s
        JOIN repository AS r ON r.id = s.repo_id
        JOIN dependency AS d ON d.id = s.dependency_id
        WHERE s.id = ?
        """,
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


def list_test_runs(
    conn: sqlite3.Connection, session_id: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM test_run
        WHERE session_id = ?
        ORDER BY iteration ASC, id ASC
        """,
        (session_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def list_migration_changes(
    conn: sqlite3.Connection, session_id: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM migration_change
        WHERE session_id = ?
        ORDER BY iteration ASC, id ASC
        """,
        (session_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_schema_version(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row["version"]) if row else None
