from pathlib import Path

import pytest

from automigrate import db
from automigrate.rag.chunker import chunk_text, estimate_tokens, make_chunk_id
from automigrate.rag.index import index_documents
from automigrate.rag.loaders import load_document, read_document
from automigrate.rag.retriever import BM25Index, build_query, retrieve, tokenize


def test_tokenizer_keeps_identifiers_intact() -> None:
    assert tokenize("Use lib.core.helpers and old_api, mode now.") == [
        "use",
        "lib.core.helpers",
        "and",
        "old_api",
        "mode",
        "now",
    ]


def test_chunker_preserves_heading_path() -> None:
    text = (
        "# Migration Guide\n\n"
        "General notes about version 2.0.\n\n"
        "## Removed parameters\n\n"
        "The mode keyword argument was removed. Use options instead.\n"
    )
    chunks = chunk_text(text, "guide.md")
    assert len(chunks) == 2
    assert chunks[0].heading_path == "Migration Guide"
    assert chunks[1].heading_path == "Migration Guide > Removed parameters"
    assert "mode keyword" in chunks[1].text
    assert chunks[0].chunk_id == make_chunk_id("guide.md", chunks[0].text)


def test_chunker_overlap_and_token_estimate() -> None:
    first = " ".join(["alpha"] * 400)
    second = " ".join(["beta"] * 400)
    chunks = chunk_text(f"# Section\n\n{first}\n\n{second}\n", "big.md")
    assert len(chunks) == 2
    assert estimate_tokens(chunks[0].text) > 400
    tail = chunks[0].text.split()[-10:]
    assert " ".join(tail) in chunks[1].text


def test_empty_document_has_no_chunks() -> None:
    assert chunk_text("   \n\n", "empty.md") == []


def test_rst_and_html_loaders(tmp_path: Path) -> None:
    rst = tmp_path / "migration.rst"
    rst.write_text(
        "Migration Guide\n===============\n\n"
        "Renames\n-------\n\n"
        "old_api becomes new_api.\n",
        encoding="utf-8",
    )
    html = tmp_path / "migration.html"
    html.write_text(
        "<html><body><h1>Guide</h1><p>Call <code>new_api</code>.</p>"
        "<script>ignored()</script></body></html>",
        encoding="utf-8",
    )
    rst_text = read_document(rst)
    assert "# Migration Guide" in rst_text
    assert "## Renames" in rst_text
    html_text = read_document(html)
    assert "# Guide" in html_text
    assert "new_api" in html_text
    assert "ignored" not in html_text
    loaded = load_document(rst, tmp_path)
    assert loaded.source_doc == "migration.rst"


def test_exact_identifier_query_returns_correct_chunk(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "renames.md").write_text(
        "# Renames\n\nThe old_api function is replaced by new_api in version 2.0.\n",
        encoding="utf-8",
    )
    (docs / "params.md").write_text(
        "# Parameters\n\nThe mode keyword argument was removed in version 2.0.\n",
        encoding="utf-8",
    )
    conn = db.init_db(tmp_path / "index.db")
    index_documents(conn, docs, "lib", "2.0")

    with pytest.warns(RuntimeWarning):
        evidence = retrieve(conn, "old_api", dependency="lib")
    assert evidence
    assert evidence[0].source_doc == "renames.md"
    assert evidence[0].heading_path == "Renames"
    assert "old_api" in evidence[0].text
    conn.close()


def test_dotted_identifier_query(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "paths.md").write_text(
        "# Import paths\n\nMove imports from lib.utils to lib.core.helpers.\n",
        encoding="utf-8",
    )
    conn = db.init_db(tmp_path / "index.db")
    index_documents(conn, docs, "lib")
    evidence = retrieve(conn, "lib.core.helpers", dependency="lib", threshold=0.1)
    assert evidence
    assert evidence[0].source_doc == "paths.md"
    conn.close()


def test_index_skips_unchanged_and_rebuilds_changed(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    page = docs / "page.md"
    page.write_text("# Guide\n\nold_api is renamed.\n", encoding="utf-8")
    conn = db.init_db(tmp_path / "index.db")

    first = index_documents(conn, docs, "lib")
    assert first.files_indexed == 1
    assert first.chunks_written == 1

    second = index_documents(conn, docs, "lib")
    assert second.files_indexed == 0
    assert second.files_skipped == 1

    page.write_text("# Guide\n\nold_api is renamed to new_api.\n", encoding="utf-8")
    third = index_documents(conn, docs, "lib")
    assert third.files_indexed == 1
    assert third.chunks_written == 1
    conn.close()


def test_build_query_includes_symbols_and_traceback() -> None:
    query = build_query(
        dependency="lib",
        from_version="1.0",
        to_version="2.0",
        symbols=["old_api", "mode"],
        iteration=1,
        latest_traceback="traceback text",
    )
    assert "old_api" in query
    assert "mode" in query
    assert "traceback" not in query

    query = build_query(
        dependency="lib",
        from_version="1.0",
        to_version="2.0",
        symbols=["old_api"],
        iteration=2,
        latest_traceback="E   AttributeError: old_api",
    )
    assert "AttributeError" in query


def test_bm25_index_search_threshold() -> None:
    index = BM25Index(
        [
            {
                "id": "a",
                "source_doc": "a.md",
                "heading_path": "Renames",
                "text": "old_api is now new_api",
            }
        ]
    )
    assert index.search("old_api", threshold=0.0)
    assert index.search("unrelated", threshold=0.0) == []
