"""Autonomous migration orchestrator: discover, triage, then migrate."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from automigrate.agent.triage import TriageDecision, TriageResult, triage_candidates
from automigrate.config import default_outputs_dir, ensure_outputs_dir
from automigrate.graph import MigrationResult, ProgressCallback, run_migration
from automigrate.ingest.versions import (
    DependencyCandidate,
    Fetcher,
    build_candidates,
    outdated_candidates,
)
from automigrate.llm.base import LLMAdapter
from automigrate.llm.factory import build_llm_adapter
from automigrate.state import Status
from automigrate.tools.git import (
    clone_repository,
    is_repository_url,
)

NO_CHANGES = "NO_CHANGES"
SUCCESSFUL = "SUCCESSFUL"
PARTIAL = "PARTIAL"
FAILED = "FAILED"

SUCCESS_STATUSES = {Status.SUCCESSFUL, Status.UNVERIFIED}


@dataclass
class DependencyRun:
    """One dependency migration attempt within an auto run."""

    dependency: str
    from_version: str
    to_version: str
    result: MigrationResult | None = None
    error: str | None = None

    @property
    def status(self) -> Status:
        if self.result is None:
            return Status.FAILED
        return self.result.status

    @property
    def succeeded(self) -> bool:
        return self.result is not None and self.status in SUCCESS_STATUSES


@dataclass
class AutoMigrationResult:
    """Aggregate result for an autonomous multi-dependency run."""

    repo_input: str
    source_path: Path
    candidates: list[DependencyCandidate] = field(default_factory=list)
    triage: TriageResult | None = None
    runs: list[DependencyRun] = field(default_factory=list)
    status: str = NO_CHANGES
    summary: str = ""
    outputs_dir: Path = field(default_factory=default_outputs_dir)

    @property
    def selected(self) -> list[TriageDecision]:
        return list(self.triage.migrations) if self.triage is not None else []

    @property
    def succeeded(self) -> bool:
        return self.status in {NO_CHANGES, SUCCESSFUL}


def _aggregate(runs: list[DependencyRun]) -> str:
    if not runs:
        return NO_CHANGES
    succeeded = [run for run in runs if run.succeeded]
    if len(succeeded) == len(runs):
        return SUCCESSFUL
    if succeeded:
        return PARTIAL
    return FAILED


def _summary(runs: list[DependencyRun]) -> str:
    succeeded = sum(1 for run in runs if run.succeeded)
    failed = [run.dependency for run in runs if not run.succeeded]
    text = f"{succeeded}/{len(runs)} selected dependency migration(s) succeeded."
    if failed:
        text += " Not successful: " + ", ".join(failed) + "."
    return text


def _prefixed_progress(
    callback: ProgressCallback | None,
    dependency: str,
) -> ProgressCallback | None:
    if callback is None:
        return None

    def report(stage: str, message: str) -> None:
        callback(stage, f"[{dependency}] {message}")

    return report


def _resolve_source(
    repository: str,
    progress: ProgressCallback | None,
) -> tuple[Path, Path | None]:
    """Return the scan source and an optional temporary clone root to remove."""

    if not is_repository_url(repository):
        resolved = Path(repository).resolve()
        if not resolved.is_dir():
            raise FileNotFoundError(f"repository does not exist: {resolved}")
        return resolved, None

    if progress is not None:
        progress("INGEST", "cloning repository once for discovery and migration")
    clone_root = Path(tempfile.mkdtemp(prefix="automigrate-auto-clone-"))
    destination = clone_root / "repository"
    source = clone_repository(repository, destination)
    return source, clone_root


def run_auto_migration(
    repo_path: str | Path,
    *,
    docs_dir: str | Path | None = None,
    model: str | None = None,
    provider: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    max_iterations: int = 5,
    max_dependencies: int = 0,
    allow_undocumented: bool | None = None,
    outputs_dir: str | Path | None = None,
    db_path: str | Path | None = None,
    top_k: int = 6,
    score_threshold: float = 0.0,
    subprocess_timeout: int = 120,
    llm_adapter: LLMAdapter | None = None,
    api_detector=None,
    triage_llm: LLMAdapter | None = None,
    pypi_fetcher: Fetcher | None = None,
    progress_callback: ProgressCallback | None = None,
) -> AutoMigrationResult:
    """Discover outdated dependencies, triage them, and migrate each one.

    ``run_migration`` keeps its existing contract: this orchestrator only
    resolves the source once, resolves versions, and calls it once per selected
    dependency with a fresh session/workspace.
    """

    repository = str(repo_path)
    target_dir = (
        Path(outputs_dir).resolve() if outputs_dir is not None else default_outputs_dir()
    )
    ensure_outputs_dir(target_dir)
    source, clone_root = _resolve_source(repository, progress_callback)
    try:
        if progress_callback is not None:
            progress_callback("INGEST", "resolving dependency versions from PyPI")
        candidates = build_candidates(source, fetcher=pypi_fetcher)
        outdated = outdated_candidates(candidates)
        result = AutoMigrationResult(
            repo_input=repository,
            source_path=source,
            candidates=candidates,
            outputs_dir=target_dir,
        )
        if not outdated:
            result.status = NO_CHANGES
            result.summary = "No outdated dependencies were detected."
            return result

        if progress_callback is not None:
            progress_callback(
                "PLAN",
                f"triaging {len(outdated)} outdated dependency(ies) with the LLM",
            )
        triage_model = triage_llm
        if triage_model is None:
            triage_model = build_llm_adapter(
                model=model,
                provider=provider,
                base_url=base_url,
                api_key=api_key,
            )
        triage = triage_candidates(
            triage_model,
            outdated,
            max_dependencies=max_dependencies,
        )
        result.triage = triage

        for decision in triage.migrations:
            run_progress = _prefixed_progress(progress_callback, decision.dependency)
            try:
                migration = run_migration(
                    source,
                    decision.dependency,
                    decision.from_version,
                    decision.to_version,
                    docs_dir=docs_dir,
                    model=model,
                    provider=provider,
                    base_url=base_url,
                    api_key=api_key,
                    max_iterations=max_iterations,
                    allow_undocumented=allow_undocumented,
                    outputs_dir=target_dir,
                    db_path=db_path,
                    top_k=top_k,
                    score_threshold=score_threshold,
                    subprocess_timeout=subprocess_timeout,
                    llm_adapter=llm_adapter,
                    api_detector=api_detector,
                    progress_callback=run_progress,
                )
            except Exception as exc:  # noqa: BLE001 - continue with next dependency
                result.runs.append(
                    DependencyRun(
                        dependency=decision.dependency,
                        from_version=decision.from_version,
                        to_version=decision.to_version,
                        error=str(exc),
                    )
                )
                continue
            result.runs.append(
                DependencyRun(
                    dependency=decision.dependency,
                    from_version=decision.from_version,
                    to_version=decision.to_version,
                    result=migration,
                )
            )

        result.status = _aggregate(result.runs)
        result.summary = _summary(result.runs)
        return result
    finally:
        if clone_root is not None:
            shutil.rmtree(clone_root, ignore_errors=True)
