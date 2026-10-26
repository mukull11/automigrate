"""Heading-aware, overlapping chunking of migration documents."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

TOKENS_PER_WORD = 1.3
DEFAULT_MAX_TOKENS = 600
DEFAULT_OVERLAP_TOKENS = 80
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    source_doc: str
    heading_path: str
    text: str


def estimate_tokens(text: str) -> float:
    return len(text.split()) * TOKENS_PER_WORD


def make_chunk_id(source_doc: str, text: str) -> str:
    return hashlib.sha1((source_doc + text).encode("utf-8")).hexdigest()


def _split_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    stack: list[str] = []
    current_lines: list[str] = []

    def flush() -> None:
        body = "\n".join(current_lines).strip()
        if body:
            sections.append((" > ".join(stack), body))
        current_lines.clear()

    for line in text.splitlines():
        match = HEADING_RE.match(line.strip())
        if match:
            flush()
            level = len(match.group(1))
            title = match.group(2).strip()
            stack[:] = stack[: level - 1]
            stack.append(title)
            continue
        current_lines.append(line)
    flush()
    if not sections and text.strip():
        sections.append(("", text.strip()))
    return sections


def _paragraphs(body: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
    return parts or [body.strip()]


def _split_oversized(paragraph: str, max_words: int) -> list[str]:
    words = paragraph.split()
    if len(words) <= max_words:
        return [paragraph]
    return [" ".join(words[i : i + max_words]) for i in range(0, len(words), max_words)]


def chunk_text(
    text: str,
    source_doc: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[Chunk]:
    """Split a document into chunks of roughly ``max_tokens`` with overlap."""

    max_words = max(1, int(max_tokens / TOKENS_PER_WORD))
    overlap_words = max(0, int(overlap_tokens / TOKENS_PER_WORD))
    chunks: list[Chunk] = []

    for heading_path, body in _split_sections(text):
        current: list[str] = []
        current_words = 0
        for paragraph in _paragraphs(body):
            for piece in _split_oversized(paragraph, max_words):
                piece_words = len(piece.split())
                if current and (current_words + piece_words) > max_words:
                    joined = "\n\n".join(current).strip()
                    if joined:
                        chunks.append(
                            Chunk(
                                chunk_id=make_chunk_id(source_doc, joined),
                                source_doc=source_doc,
                                heading_path=heading_path,
                                text=joined,
                            )
                        )
                    tail = joined.split()[-overlap_words:] if overlap_words else []
                    current = [" ".join(tail)] if tail else []
                    current_words = len(tail)
                current.append(piece)
                current_words += piece_words
        joined = "\n\n".join(current).strip()
        if joined:
            chunks.append(
                Chunk(
                    chunk_id=make_chunk_id(source_doc, joined),
                    source_doc=source_doc,
                    heading_path=heading_path,
                    text=joined,
                )
            )

    unique: dict[str, Chunk] = {chunk.chunk_id: chunk for chunk in chunks}
    return list(unique.values())
