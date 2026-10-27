"""SQLite-backed document index with change detection."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from automigrate import db
from automigrate.rag.chunker import chunk_text
from automigrate.rag.loaders import iter_document_paths, load_document


@dataclass
class IndexStats:
    files_indexed: int = 0
    files_skipped: int = 0
    chunks_written: int = 0

    @property
    def files_seen(self) -> int:
        return self.files_indexed + self.files_skipped


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def index_documents(
    conn: sqlite3.Connection,
    docs_dir: str | Path,
    dependency: str,
    version_hint: str | None = None,
) -> IndexStats:
    """Index every supported document, skipping files whose sha256 is unchanged."""

    stats = IndexStats()
    for path in iter_document_paths(docs_dir):
        document = load_document(path, docs_dir)
        file_sha = _sha256_file(path)
        existing = db.list_doc_chunks_by_source(conn, document.source_doc)
        if existing and all(chunk["sha256"] == file_sha for chunk in existing):
            stats.files_skipped += 1
            continue

        chunks = chunk_text(document.text, document.source_doc)
        db.delete_doc_chunks_by_source(conn, document.source_doc)
        for chunk in chunks:
            db.upsert_doc_chunk(
                conn,
                chunk_id=chunk.chunk_id,
                source_doc=chunk.source_doc,
                heading_path=chunk.heading_path,
                text=chunk.text,
                dependency=dependency,
                version_hint=version_hint,
                sha256=file_sha,
            )
        stats.files_indexed += 1
        stats.chunks_written += len(chunks)
    return stats


def index_text_documents(
    conn: sqlite3.Connection,
    documents: list[tuple[str, str]],
    dependency: str,
    version_hint: str | None = None,
) -> IndexStats:
    """Index in-memory documents such as files shipped inside a package."""

    stats = IndexStats()
    for source_doc, text in documents:
        chunks = chunk_text(text, source_doc)
        db.delete_doc_chunks_by_source(conn, source_doc)
        for chunk in chunks:
            db.upsert_doc_chunk(
                conn,
                chunk_id=chunk.chunk_id,
                source_doc=chunk.source_doc,
                heading_path=chunk.heading_path,
                text=chunk.text,
                dependency=dependency,
                version_hint=version_hint,
                sha256=chunk.chunk_id,
            )
        stats.files_indexed += 1
        stats.chunks_written += len(chunks)
    return stats


def clear_dependency_chunks(conn: sqlite3.Connection, dependency: str) -> None:
    """Remove indexed chunks for one dependency before a fresh run."""

    conn.execute("DELETE FROM doc_chunk WHERE dependency = ?", (dependency,))
    conn.commit()
