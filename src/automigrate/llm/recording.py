"""LLM decorator that records every model call in SQLite."""

from __future__ import annotations

import sqlite3
import time

from automigrate import db
from automigrate.llm.base import LLMAdapter, LLMError


class RecordingLLM(LLMAdapter):
    def __init__(
        self,
        delegate: LLMAdapter,
        conn: sqlite3.Connection,
        session_id: str,
        *,
        record_attempts: bool = True,
    ) -> None:
        self.delegate = delegate
        self.conn = conn
        self.session_id = session_id
        self.name = delegate.name
        self.record_attempts = record_attempts

    @property
    def provider(self) -> str | None:
        return getattr(self.delegate, "provider", None)

    @property
    def current_model(self) -> str | None:
        return getattr(
            self.delegate,
            "current_model",
            getattr(self.delegate, "model", None),
        )

    def _record_attempt(
        self,
        *,
        iteration: int,
        stage: str | None,
        status_code: int | None,
        error: str | None,
        success: bool,
        latency_ms: int,
    ) -> None:
        provider = self.provider or self.name
        model = self.current_model or "default"
        summary = (
            f"provider={provider} model={model} "
            f"status={status_code if status_code is not None else 'ok'} "
            f"result={'success' if success else 'error'}"
        )
        if error:
            summary = f"{summary} error={error}"
        db.add_agent_action(
            self.conn,
            self.session_id,
            iteration,
            stage or "LLM",
            "model_attempt",
            "",
            summary[:1000],
            latency_ms,
        )

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        stage: str | None = None,
        iteration: int = 0,
    ) -> str:
        started = time.perf_counter()
        try:
            text = self.delegate.complete(
                prompt, system=system, stage=stage, iteration=iteration
            )
        except LLMError as exc:
            if self.record_attempts:
                self._record_attempt(
                    iteration=iteration,
                    stage=stage,
                    status_code=exc.status_code,
                    error=str(exc),
                    success=False,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            raise
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
        if self.record_attempts:
            self._record_attempt(
                iteration=iteration,
                stage=stage,
                status_code=None,
                error=None,
                success=True,
                latency_ms=latency_ms,
            )
        return text
