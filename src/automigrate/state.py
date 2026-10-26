"""Pydantic data contracts shared across AutoMigrate."""

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Stage(str, Enum):
    INGEST = "INGEST"
    DETECT = "DETECT"
    RETRIEVE = "RETRIEVE"
    PLAN = "PLAN"
    PATCH = "PATCH"
    VALIDATE = "VALIDATE"
    DIAGNOSE = "DIAGNOSE"
    REPORT = "REPORT"
    DONE = "DONE"
    FLAGGED = "FLAGGED"


class Status(str, Enum):
    RUNNING = "RUNNING"
    SUCCESSFUL = "SUCCESSFUL"
    PARTIAL = "PARTIAL"
    REQUIRES_HUMAN_REVIEW = "REQUIRES_HUMAN_REVIEW"
    FAILED = "FAILED"


class Diagnosis(str, Enum):
    MIGRATION_RELATED = "MIGRATION_RELATED"
    POSSIBLY_RELATED = "POSSIBLY_RELATED"
    UNRELATED = "UNRELATED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    line: int = Field(ge=1)
    symbol: str
    kind: Literal["import", "call", "attribute", "keyword_arg"]
    snippet: str


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    source_doc: str
    heading_path: str
    score: float
    text: str = Field(max_length=800)


class PlannedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file: str
    old_symbol: str
    new_symbol: str | None = None
    description: str
    confidence: float = Field(ge=0.0, le=1.0)
    cites: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    changes: list[PlannedChange] = Field(default_factory=list)
    expected_effect: str

    def citations(self) -> set[str]:
        return {chunk_id for change in self.changes for chunk_id in change.cites}

    def validate_citations(self, allowed_chunk_ids: set[str]) -> bool:
        return self.citations().issubset(allowed_chunk_ids)


class TestRunResult(BaseModel):
    __test__ = False

    model_config = ConfigDict(extra="forbid")

    passed: int = 0
    failed: int = 0
    errors: int = 0
    failed_test_names: list[str] = Field(default_factory=list)
    first_traceback: str | None = None
    ruff_issue_count: int = 0
    duration_s: float = 0.0


class AgentState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    repo_path: str
    workspace_path: str
    dependency: str
    from_version: str
    to_version: str
    usages: list[Usage] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    plan: Plan | None = None
    iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=5, ge=1)
    baseline: TestRunResult = Field(default_factory=TestRunResult)
    latest: TestRunResult = Field(default_factory=TestRunResult)
    diagnosis: Diagnosis | None = None
    status: Status = Status.RUNNING
    flag_reason: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)
