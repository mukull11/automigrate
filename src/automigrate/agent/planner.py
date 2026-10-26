"""Plan generation and validation."""

from __future__ import annotations

from pydantic import ValidationError

from automigrate.agent.parsing import LLMParseError, parse_json_object
from automigrate.llm.base import LLMAdapter
from automigrate.state import AgentState, Plan

SYSTEM_PROMPT = (
    "You are a careful Python dependency-migration planner. "
    "You propose the smallest safe set of source changes and cite retrieved evidence. "
    "You never propose editing tests. Return only valid JSON."
)


class PlanValidationError(RuntimeError):
    pass


def build_plan_prompt(state: AgentState) -> str:
    usage_lines = [
        f"- {usage.file}:{usage.line} {usage.kind} {usage.symbol} | {usage.snippet}"
        for usage in state.usages
    ]
    evidence_lines = [
        f"- [{item.chunk_id}] ({item.source_doc} > {item.heading_path})\n{item.text}"
        for item in state.evidence
    ]
    return (
        f"Plan the migration of dependency {state.dependency} "
        f"from {state.from_version} to {state.to_version}.\n\n"
        "Usages found in the repository:\n"
        + ("\n".join(usage_lines) or "- none")
        + "\n\nRetrieved evidence:\n"
        + ("\n\n".join(evidence_lines) or "- none")
        + "\n\nReturn JSON with exactly these fields:\n"
        '{"summary": string, "changes": [{"file": string, "old_symbol": string, '
        '"new_symbol": string or null, "description": string, "confidence": number '
        'between 0 and 1, "cites": [evidence chunk ids]}], "expected_effect": string}\n'
        "Every cites value must be an evidence chunk id shown above. "
        "Do not edit files under tests/. Return only the JSON object."
    )


def parse_plan(text: str, allowed_chunk_ids: set[str]) -> Plan:
    data = parse_json_object(text)
    try:
        plan = Plan.model_validate(data)
    except ValidationError as exc:
        raise PlanValidationError(f"plan schema validation failed: {exc}") from exc
    unknown = plan.citations() - allowed_chunk_ids
    if unknown:
        raise PlanValidationError(f"plan cites unknown evidence ids: {sorted(unknown)}")
    return plan


class Planner:
    def __init__(self, llm: LLMAdapter, *, retries: int = 1) -> None:
        self.llm = llm
        self.retries = retries

    def propose(self, state: AgentState) -> Plan:
        allowed = {item.chunk_id for item in state.evidence}
        prompt = build_plan_prompt(state)
        feedback = ""
        last_error: Exception | None = None
        for _ in range(self.retries + 1):
            raw = self.llm.complete(
                prompt + feedback,
                system=SYSTEM_PROMPT,
                stage="PLAN",
                iteration=state.iteration,
            )
            try:
                return parse_plan(raw, allowed)
            except (PlanValidationError, LLMParseError) as exc:
                last_error = exc
                feedback = (
                    "\n\nYour previous response was rejected: "
                    f"{exc}\nReturn only a corrected JSON object using the allowed chunk ids."
                )
        raise PlanValidationError(f"planner failed after retry: {last_error}")
