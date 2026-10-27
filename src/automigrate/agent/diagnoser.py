"""Failure diagnosis using strict JSON output."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, ValidationError

from automigrate.agent.parsing import LLMParseError, parse_json_object
from automigrate.llm.base import LLMAdapter
from automigrate.state import AgentState, Diagnosis

SYSTEM_PROMPT = (
    "You are a migration failure diagnostician. "
    "Decide whether test failures are caused by the dependency migration. "
    "Return only valid JSON."
)


class DiagnosisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diagnosis: Diagnosis
    reason: str = ""


class DiagnosisError(RuntimeError):
    pass


def build_diagnosis_prompt(state: AgentState) -> str:
    latest = state.latest
    baseline = state.baseline
    traceback = (latest.first_traceback or "")[:2000]
    evidence = "\n".join(
        f"[{item.chunk_id}] {item.heading_path}: {item.text[:300]}" for item in state.evidence
    )
    plan_summary = state.plan.summary if state.plan else "- no plan"
    return (
        f"Diagnose the failed validation for {state.dependency} "
        f"{state.from_version} -> {state.to_version}.\n\n"
        f"Baseline: passed={baseline.passed}, failed={baseline.failed}, "
        f"errors={baseline.errors}, ruff={baseline.ruff_issue_count}\n"
        f"Latest: passed={latest.passed}, failed={latest.failed}, "
        f"errors={latest.errors}, ruff={latest.ruff_issue_count}\n"
        f"Failed tests: {', '.join(latest.failed_test_names) or 'none'}\n"
        f"Ruff output: {chr(10).join(latest.ruff_messages) or 'none'}\n\n"
        f"Plan summary: {plan_summary}\n\n"
        f"Evidence:\n{evidence or '- none'}\n\n"
        f"Traceback:\n{traceback or '- none'}\n\n"
        "Choose exactly one diagnosis: MIGRATION_RELATED, POSSIBLY_RELATED, "
        "UNRELATED, or INSUFFICIENT_EVIDENCE. Return JSON like "
        '{"diagnosis": "MIGRATION_RELATED", "reason": "short explanation"}.'
    )


def parse_diagnosis(text: str) -> DiagnosisResult:
    data = parse_json_object(text)
    try:
        return DiagnosisResult.model_validate(data)
    except ValidationError as exc:
        raise DiagnosisError(f"diagnosis schema validation failed: {exc}") from exc


class Diagnoser:
    def __init__(self, llm: LLMAdapter, *, retries: int = 1) -> None:
        self.llm = llm
        self.retries = retries

    def diagnose(self, state: AgentState) -> DiagnosisResult:
        prompt = build_diagnosis_prompt(state)
        feedback = ""
        last_error: Exception | None = None
        for _ in range(self.retries + 1):
            raw = self.llm.complete(
                prompt + feedback,
                system=SYSTEM_PROMPT,
                stage="DIAGNOSE",
                iteration=state.iteration,
            )
            try:
                return parse_diagnosis(raw)
            except (DiagnosisError, LLMParseError) as exc:
                last_error = exc
                feedback = (
                    "\n\nYour previous response was rejected: "
                    f"{exc}\nReturn only a corrected JSON object."
                )
        raise DiagnosisError(f"diagnoser failed after retry: {last_error}")
