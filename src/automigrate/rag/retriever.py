"""BM25 retrieval over indexed documentation chunks."""

from __future__ import annotations

import math
import re
import sqlite3
import warnings
from collections import Counter
from collections.abc import Iterable
from typing import Any

from automigrate import db
from automigrate.state import Evidence

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*")
BM25_K1 = 1.5
BM25_B = 0.75


def tokenize(text: str) -> list[str]:
    """Tokenize while keeping dotted identifiers such as ``lib.core.helpers`` intact."""

    return [match.group(0).lower() for match in TOKEN_RE.finditer(text)]


class BM25Index:
    def __init__(self, chunks: Iterable[dict[str, Any]]) -> None:
        self.chunks = list(chunks)
        self.documents = [
            tokenize(f"{chunk.get('heading_path', '')} {chunk.get('text', '')}")
            for chunk in self.chunks
        ]
        self.term_frequencies = [Counter(tokens) for tokens in self.documents]
        self.lengths = [len(tokens) for tokens in self.documents]
        self.avg_length = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0
        self.document_frequency: Counter[str] = Counter()
        for tokens in self.documents:
            self.document_frequency.update(set(tokens))

    def _idf(self, term: str) -> float:
        total = len(self.documents)
        frequency = self.document_frequency.get(term, 0)
        return math.log(1 + (total - frequency + 0.5) / (frequency + 0.5))

    def score(self, index: int, terms: set[str]) -> float:
        tokens = self.lengths[index]
        if not tokens:
            return 0.0
        frequencies = self.term_frequencies[index]
        total = 0.0
        for term in terms:
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            denominator = frequency + BM25_K1 * (
                1 - BM25_B + BM25_B * tokens / (self.avg_length or 1.0)
            )
            total += self._idf(term) * (frequency * (BM25_K1 + 1)) / denominator
        return total

    def search(
        self,
        query: str,
        *,
        top_k: int = 6,
        threshold: float = 0.0,
    ) -> list[tuple[dict[str, Any], float]]:
        terms = set(tokenize(query))
        if not terms or not self.chunks:
            return []
        scored = [(chunk, self.score(i, terms)) for i, chunk in enumerate(self.chunks)]
        scored = [
            (chunk, score) for chunk, score in scored if score > threshold
        ]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]


def build_query(
    *,
    dependency: str,
    from_version: str,
    to_version: str,
    symbols: Iterable[str],
    iteration: int = 0,
    latest_traceback: str | None = None,
) -> str:
    terms: list[str] = [dependency, from_version, to_version, *symbols]
    if iteration >= 2 and latest_traceback:
        terms.append(latest_traceback[:300])
    expanded: list[str] = []
    for term in terms:
        expanded.append(term)
        if "." in term:
            expanded.append(term.rsplit(".", 1)[-1])
    seen: set[str] = set()
    ordered: list[str] = []
    for term in expanded:
        cleaned = term.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            ordered.append(cleaned)
    return " ".join(ordered)


def retrieve(
    conn: sqlite3.Connection,
    query: str,
    *,
    dependency: str | None = None,
    top_k: int = 6,
    threshold: float = 0.0,
) -> list[Evidence]:
    """Return the highest scoring evidence chunks for a query."""

    if threshold <= 0.0:
        warnings.warn(
            "score_threshold is 0.0; retrieval is not filtering low-relevance chunks",
            RuntimeWarning,
            stacklevel=2,
        )
    chunks = db.list_doc_chunks(conn, dependency)
    index = BM25Index(chunks)
    evidence: list[Evidence] = []
    for chunk, score in index.search(query, top_k=top_k, threshold=threshold):
        evidence.append(
            Evidence(
                chunk_id=chunk["id"],
                source_doc=chunk["source_doc"],
                heading_path=chunk["heading_path"],
                score=score,
                text=str(chunk["text"])[:800],
            )
        )
    return evidence
