"""LLM decorator that records every model call in SQLite."""

from __future__ import annotations

import sqlite3
import time

from automigrate import db
from automigrate.llm.base import LLMAdapter


class RecordingLLM(LLMAdapter):
    def __init__(self, delegate: LLMAdapter, conn: sqlite3.Connection, session_id: str) -> None:
        self.delegate = delegate
        self.conn = conn
        self.session_id = session_id
        self.name = delegate.name

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        stage: str | None = None,
        iteration: int = 0,
    ) -> str:
        started = time.perf_counter()
        text = self.delegate.complete(
            prompt, system=system, stage=stage, iteration=iteration
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        db.add_agent_action(
            self.conn,
            self.session_id,
            iteration,
            stage or "LLM",
            "llm_call",
            prompt[:500],
            text[:500],
            latency_ms,
        )
        return text
