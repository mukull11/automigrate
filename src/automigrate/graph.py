"""Explicit migration state machine."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from automigrate import db
from automigrate.agent.diagnoser import Diagnoser, DiagnosisError
from automigrate.agent.patcher import Patcher, PatchError
from automigrate.agent.planner import (
    Planner,
    PlanValidationError,
    is_insufficient_evidence_plan,
)
from automigrate.apidiff.detector import ApiDetector, ApiDiffError, PipApiDiffDetector
from automigrate.apidiff.usages import scan_api_usages
from automigrate.ingest.dependencies import find_declaration
from automigrate.ingest.scanner import ScanResult, find_python_files, scan_repository
from automigrate.llm import RecordingLLM, create_adapter
from automigrate.llm.base import LLMAdapter, LLMError
from automigrate.rag.index import clear_dependency_chunks, index_documents, index_text_documents
from automigrate.rag.retriever import build_query, retrieve
from automigrate.state import (
    AgentState,
    Diagnosis,
    Plan,
    Stage,
    Status,
    TestRunResult,
)
from automigrate.tools.filesystem import Sandbox, SandboxViolation
from automigrate.tools.git import (
    GitError,
    GitRepo,
    clone_repository,
    is_repository_url,
    prepare_workspace,
)
from automigrate.tools.lint_runner import RuffRunner
from automigrate.tools.subprocess import SubprocessRunner
from automigrate.tools.test_runner import PytestRunner

ProgressCallback = Callable[[str, str], None]


@dataclass
class MigrationResult:
    state: AgentState
    session_id: str
    workspace_path: Path
    commits: list[str] = field(default_factory=list)

    @property
    def status(self) -> Status:
        return self.state.status


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MigrationGraph:
    def __init__(
        self,
        *,
        repo_path: str | Path,
        dependency: str,
        from_version: str,
        to_version: str,
        docs_dir: str | Path | None = None,
        model: str | None = None,
        api_key: str | None = None,
        max_iterations: int = 5,
        outputs_dir: str | Path = "outputs",
        db_path: str | Path = "automigrate.db",
        top_k: int = 6,
        score_threshold: float = 0.0,
        subprocess_timeout: int = 120,
        session_id: str | None = None,
        llm_adapter: LLMAdapter | None = None,
        api_detector: ApiDetector | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self.repo_input = str(repo_path)
        self.repo_is_url = is_repository_url(self.repo_input)
        self.repo_path = (
            Path(self.repo_input)
            if not self.repo_is_url
            else Path(self.repo_input)
        )
        self.dependency = dependency
        self.from_version = from_version
        self.to_version = to_version
        self.docs_dir = Path(docs_dir).resolve() if docs_dir else None
        self.model = model
        self.api_key = api_key
        self.max_iterations = max_iterations
        self.outputs_dir = Path(outputs_dir).resolve()
        self.db_path = Path(db_path)
        self.top_k = top_k
        self.score_threshold = score_threshold
        self.subprocess_timeout = subprocess_timeout
        self.session_id = session_id or uuid.uuid4().hex
        self.llm_adapter = llm_adapter
        self.api_detector = api_detector or PipApiDiffDetector(
            timeout=max(self.subprocess_timeout, 300)
        )
        self.progress_callback = progress_callback

        self.conn = db.init_db(self.db_path)
        self.workspace = self.outputs_dir / self.session_id / "workspace"
        self.repo: GitRepo | None = None
        self.sandbox: Sandbox | None = None
        self.llm: LLMAdapter | None = None
        self.state: AgentState | None = None
        self.commits: list[str] = []
        self.previous_passed = 0
        self._last_mark = time.perf_counter()
        self._done = False
        self._files: list[Path] = []
        self._scan: ScanResult | None = None
        self.baseline_sha: str | None = None
        self._clone_dir: Path | None = None

    def _record(self, stage: str, summary: str, action_type: str = "transition") -> None:
        assert self.state is not None
        now = time.perf_counter()
        latency_ms = int((now - self._last_mark) * 1000)
        self._last_mark = now
        db.add_agent_action(
            self.conn,
            self.session_id,
            self.state.iteration,
            stage,
            action_type,
            "",
            summary[:1000],
            latency_ms,
        )
        if self.progress_callback is not None:
            self.progress_callback(stage, summary)

    def _validator(self) -> tuple[PytestRunner, RuffRunner]:
        assert self.repo is not None
        runner = SubprocessRunner(self.repo.path, timeout=self.subprocess_timeout)
        return PytestRunner(runner), RuffRunner(runner)

    @staticmethod
    def _ruff_messages(ruff) -> list[str]:
        messages: list[str] = []
        for issue in ruff.issues[:20]:
            filename = issue.get("filename") or issue.get("file") or ""
            location = issue.get("location") or {}
            row = location.get("row", "")
            code = issue.get("code") or ""
            message = issue.get("message") or ""
            messages.append(f"{filename}:{row}: {code} {message}".strip())
        return messages

    def _validate(self) -> TestRunResult:
        assert self.state is not None
        pytest_runner, ruff_runner = self._validator()
        tests = pytest_runner.run()
        ruff = ruff_runner.run(["."])
        result = tests.model_copy(
            update={
                "ruff_issue_count": ruff.issue_count,
                "ruff_messages": self._ruff_messages(ruff),
            }
        )
        self.state.latest = result
        db.add_test_run(
            self.conn,
            self.session_id,
            self.state.iteration,
            passed=result.passed,
            failed=result.failed,
            errors=result.errors,
            ruff_issues=result.ruff_issue_count,
            duration_s=result.duration_s,
            failed_tests_json=json.dumps(result.failed_test_names),
            traceback=result.first_traceback,
        )
        return result

    def _import_check(self) -> list[str]:
        assert self.repo is not None
        failures: list[str] = []
        for path in find_python_files(self.repo.path):
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
                compile(source, str(path), "exec")
            except (OSError, SyntaxError) as exc:
                failures.append(f"{path.name}: {exc}")
        return failures

    def _finish(self, final_status: Status, reason: str | None = None) -> None:
        assert self.state is not None
        if (
            final_status in {Status.REQUIRES_HUMAN_REVIEW, Status.FAILED}
            and self.repo is not None
            and self.baseline_sha is not None
        ):
            self.repo.reset_hard(self.baseline_sha)
        self.state.status = final_status
        self.state.flag_reason = reason
        self._record(
            Stage.REPORT.value,
            f"final status {final_status.value}"
            + (f": {reason}" if reason else ""),
            "report",
        )
        db.update_session(
            self.conn,
            self.session_id,
            status=final_status.value,
            iterations=self.state.iteration,
            final_reason=reason or "",
            ended_at=_utc_now(),
        )
        self._done = True

    def _flag(self, reason: str, *, tool_error: bool = False) -> None:
        assert self.state is not None
        if self.state.latest.passed > self.state.baseline.passed:
            final = Status.PARTIAL
        elif tool_error:
            final = Status.FAILED
        else:
            final = Status.REQUIRES_HUMAN_REVIEW
        self._finish(final, reason)

    def _resolve_source(self) -> Path:
        if not self.repo_is_url:
            resolved = self.repo_path.resolve()
            if not resolved.is_dir():
                raise FileNotFoundError(f"repository does not exist: {resolved}")
            return resolved
        clone_root = Path(tempfile.mkdtemp(prefix="automigrate-clone-"))
        self._clone_dir = clone_root
        destination = clone_root / "repository"
        if self.progress_callback is not None:
            self.progress_callback(Stage.INGEST.value, "cloning repository")
        return clone_repository(
            self.repo_input, destination, timeout=max(self.subprocess_timeout, 300)
        )

    def _prepare(self) -> None:
        source_path = self._resolve_source()
        display_path = self.repo_input
        repo_id = db.create_repository(
            self.conn, Path(display_path).name or display_path, display_path, "python"
        )
        dependency_id = db.create_dependency(
            self.conn,
            repo_id,
            self.dependency,
            self.from_version,
            self.to_version,
        )
        db.create_session(
            self.conn, repo_id, dependency_id, "RUNNING", self.session_id
        )

        if self.workspace.exists() and any(self.workspace.iterdir()):
            raise FileExistsError(f"workspace is not empty: {self.workspace}")
        self.repo = prepare_workspace(source_path, self.workspace)
        if self._clone_dir is not None:
            shutil.rmtree(self._clone_dir, ignore_errors=True)
            self._clone_dir = None
        self.baseline_sha = self.repo.head_sha()
        self.sandbox = Sandbox(self.workspace)

        adapter = self.llm_adapter or create_adapter(
            self.model, api_key=self.api_key
        )
        self.llm = RecordingLLM(adapter, self.conn, self.session_id)

        self.state = AgentState(
            session_id=self.session_id,
            repo_path=display_path,
            workspace_path=str(self.workspace),
            dependency=self.dependency,
            from_version=self.from_version,
            to_version=self.to_version,
            max_iterations=self.max_iterations,
        )

        self._files = find_python_files(self.workspace)
        for path in self._files:
            relative = path.relative_to(self.workspace).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            modified = datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).isoformat()
            db.add_source_file(self.conn, self.session_id, relative, digest, modified)

        db.update_session(self.conn, self.session_id, iterations=0)

        clear_dependency_chunks(self.conn, self.dependency)
        if self.docs_dir is not None:
            index_documents(self.conn, self.docs_dir, self.dependency, self.to_version)

    def _baseline(self) -> None:
        assert self.state is not None
        result = self._validate()
        self.state.baseline = result
        self.state.has_tests = (result.passed + result.failed + result.errors) > 0
        self.previous_passed = result.passed
        self._record(
            Stage.VALIDATE.value,
            f"baseline: passed={result.passed}, failed={result.failed}, "
            f"errors={result.errors}, ruff={result.ruff_issue_count}",
            "baseline",
        )
        self.state.iteration = 1

    def _ingest(self) -> bool:
        assert self.state is not None
        scan = scan_repository(self.workspace, self.dependency)
        self._scan = scan
        if not self._files:
            self._record(Stage.INGEST.value, "no Python files found")
            self._flag("repository has no Python files")
            return False
        if not find_declaration(scan.declarations, self.dependency):
            self._record(Stage.INGEST.value, f"{self.dependency} is not declared")
            self._flag(f"dependency {self.dependency} is not declared in the repository")
            return False
        self._record(
            Stage.INGEST.value,
            f"{len(self._files)} Python files; {self.dependency} is declared",
        )
        return True

    def _detect(self) -> bool:
        assert self.state is not None and self._scan is not None
        self._record(Stage.DETECT.value, "detecting API changes between versions")
        bundle = self.api_detector.detect(
            self.dependency, self.from_version, self.to_version, self.workspace
        )
        self.state.api_diff = bundle.diff
        if bundle.documents:
            index_text_documents(
                self.conn,
                [(doc.name, doc.text) for doc in bundle.documents],
                self.dependency,
                self.to_version,
            )
        self._record(
            Stage.DETECT.value,
            "API diff: "
            f"{len(bundle.diff.removed)} removed, "
            f"{len(bundle.diff.renamed)} renamed, "
            f"{len(bundle.diff.moved)} moved, "
            f"{len(bundle.diff.changed_parameters)} changed parameters",
        )
        self.state.usages = list(self._scan.usages)
        self.state.api_usages = scan_api_usages(self.workspace, bundle.diff)
        self._record(
            Stage.DETECT.value,
            f"found {len(self.state.usages)} usages of {self.dependency} "
            f"and {len(self.state.api_usages)} uses of changed symbols",
        )
        if not self.state.usages and not self.state.api_usages:
            self._flag(f"no usages of {self.dependency} found")
            return False
        return True

    def _query(self) -> str:
        assert self.state is not None
        return build_query(
            dependency=self.state.dependency,
            from_version=self.state.from_version,
            to_version=self.state.to_version,
            symbols=[usage.symbol for usage in self.state.usages],
            iteration=self.state.iteration,
            latest_traceback=self.state.latest.first_traceback,
        )

    def _retrieve(self) -> bool:
        assert self.state is not None
        evidence = retrieve(
            self.conn,
            self._query(),
            dependency=self.dependency,
            top_k=self.top_k,
            threshold=self.score_threshold,
        )
        self.state.evidence = evidence
        self._record(Stage.RETRIEVE.value, f"retrieved {len(evidence)} evidence chunks")
        if not evidence and not (
            self.state.api_diff is not None and self.state.api_diff.has_changes
        ):
            self._flag(f"no evidence found for {self.dependency}")
            return False
        return True

    def _advance_model(self) -> bool:
        adapter = self.llm_adapter
        advance = getattr(adapter, "advance_model", None)
        if advance is None:
            return False
        advanced = bool(advance())
        if advanced:
            self._record(
                Stage.PLAN.value,
                f"trying next model: {getattr(adapter, 'current_model', 'unknown')}",
                "model_fallback",
            )
        return advanced

    def _plan(self) -> Plan | None:
        assert self.state is not None and self.llm is not None
        models = getattr(self.llm_adapter, "model_names", None) or [None]
        for _ in range(len(models)):
            self._record(Stage.PLAN.value, f"planning iteration {self.state.iteration}")
            try:
                plan = Planner(self.llm).propose(self.state)
            except PlanValidationError as exc:
                if self._advance_model():
                    continue
                self._flag(f"planner failed: {exc}")
                return None
            except LLMError as exc:
                self._flag(f"LLM error during planning: {exc}", tool_error=True)
                return None
            self.state.plan = plan
            if is_insufficient_evidence_plan(plan):
                self.state.diagnosis = Diagnosis.INSUFFICIENT_EVIDENCE
                self._flag(plan.summary)
                return None
            confident = [change for change in plan.changes if change.confidence >= 0.5]
            if not confident:
                self._flag("plan has no change with confidence >= 0.5")
                return None
            return plan
        self._flag("all models in the chain failed to produce a valid plan")
        return None

    def _patch(self, plan: Plan) -> bool:
        assert self.state is not None and self.sandbox is not None and self.repo is not None
        models = getattr(self.llm_adapter, "model_names", None) or [None]
        result = None
        for _ in range(len(models)):
            try:
                result = Patcher(self.llm).apply(self.state, plan, self.sandbox)
                break
            except (PatchError, SandboxViolation) as exc:
                if self._advance_model():
                    continue
                self._flag(f"patcher failed: {exc}")
                return False
            except LLMError as exc:
                self._flag(f"LLM error during patching: {exc}", tool_error=True)
                return False
        if result is None:
            self._flag("all models in the chain failed to produce a valid patch")
            return False

        message = f"automigrate: iter {self.state.iteration} - {plan.summary}"
        try:
            self.repo.add_all()
            commit_sha = self.repo.commit(message)
            diff = self.repo.diff_commit(commit_sha)
        except GitError as exc:
            self._flag(f"git commit failed: {exc}", tool_error=True)
            return False

        self.commits.append(commit_sha)
        for file_name in result.files:
            description = "; ".join(
                change.description
                for change in plan.changes
                if change.file == file_name
            )
            db.add_migration_change(
                self.conn,
                self.session_id,
                self.state.iteration,
                file_name,
                description or plan.summary,
                diff,
                commit_sha,
            )
        self._record(
            Stage.PATCH.value,
            f"applied {result.blocks_applied} blocks in "
            f"{len(result.files)} files; commit {commit_sha[:8]}",
        )
        return True

    def _diagnose(self) -> Diagnosis | None:
        assert self.state is not None and self.llm is not None
        self._record(Stage.DIAGNOSE.value, "diagnosing validation failure")
        try:
            result = Diagnoser(self.llm).diagnose(self.state)
        except DiagnosisError as exc:
            self._flag(f"diagnoser failed: {exc}")
            return None
        except LLMError as exc:
            self._flag(f"LLM error during diagnosis: {exc}", tool_error=True)
            return None
        self.state.diagnosis = result.diagnosis
        return result.diagnosis

    def _refresh_evidence(self) -> bool:
        assert self.state is not None
        evidence = retrieve(
            self.conn,
            self._query(),
            dependency=self.dependency,
            top_k=self.top_k,
            threshold=self.score_threshold,
        )
        self.state.evidence = evidence
        self._record(
            Stage.RETRIEVE.value,
            f"refreshed evidence with traceback: {len(evidence)} chunks",
            "retrieve_refresh",
        )
        if not evidence and not (
            self.state.api_diff is not None and self.state.api_diff.has_changes
        ):
            self._flag("no evidence found after traceback refresh")
            return False
        return True

    def _successful_or_unverified(self) -> None:
        assert self.state is not None
        if self.state.has_tests:
            self._finish(Status.SUCCESSFUL, None)
            return
        failures = self._import_check()
        if failures:
            self._finish(
                Status.UNVERIFIED,
                "no tests were found; import check failed: " + "; ".join(failures[:3]),
            )
            return
        self._finish(
            Status.UNVERIFIED,
            "no tests were found; validated with ruff and an import check only",
        )

    def _loop(self) -> None:
        assert self.state is not None and self.repo is not None
        while True:
            plan = self._plan()
            if plan is None:
                return
            base_sha = self.repo.head_sha()
            if not self._patch(plan):
                return

            latest = self._validate()
            self._record(
                Stage.VALIDATE.value,
                f"iteration {self.state.iteration}: passed={latest.passed}, "
                f"failed={latest.failed}, errors={latest.errors}, "
                f"ruff={latest.ruff_issue_count}",
            )
            if (
                latest.failed == 0
                and latest.errors == 0
                and latest.ruff_issue_count <= self.state.baseline.ruff_issue_count
            ):
                self._successful_or_unverified()
                return

            if latest.passed < self.previous_passed:
                self.repo.reset_hard(base_sha)
                self._flag(
                    f"pass count dropped from {self.previous_passed} "
                    f"to {latest.passed}; rolled back to {base_sha[:8]}"
                )
                return

            diagnosis = self._diagnose()
            if diagnosis is None:
                return
            if diagnosis in {Diagnosis.UNRELATED, Diagnosis.INSUFFICIENT_EVIDENCE}:
                self._flag(f"diagnosis is {diagnosis.value}")
                return
            if self.state.iteration >= self.state.max_iterations:
                self._flag(
                    f"maximum iterations reached ({self.state.max_iterations})"
                )
                return

            self.previous_passed = latest.passed
            self.state.iteration += 1
            if self.state.iteration >= 2 and not self._refresh_evidence():
                return

    def run(self) -> MigrationResult:
        try:
            self._prepare()
            self._baseline()
            if not self._ingest():
                return self._result()
            if not self._detect():
                return self._result()
            if not self._retrieve():
                return self._result()
            self._loop()
        except ApiDiffError:
            raise
        except Exception as exc:
            if not self._done and self.state is not None:
                self._flag(f"internal error: {exc}", tool_error=True)
            else:
                raise
        finally:
            self.conn.close()
        return self._result()

    def _result(self) -> MigrationResult:
        assert self.state is not None
        return MigrationResult(
            state=self.state,
            session_id=self.session_id,
            workspace_path=self.workspace,
            commits=self.commits,
        )


def run_migration(
    repo_path: str | Path,
    dependency: str,
    from_version: str,
    to_version: str,
    *,
    docs_dir: str | Path | None = None,
    model: str | None = None,
    api_key: str | None = None,
    max_iterations: int = 5,
    outputs_dir: str | Path = "outputs",
    db_path: str | Path = "automigrate.db",
    top_k: int = 6,
    score_threshold: float = 0.0,
    subprocess_timeout: int = 120,
    session_id: str | None = None,
    llm_adapter: LLMAdapter | None = None,
    api_detector: ApiDetector | None = None,
    progress_callback: ProgressCallback | None = None,
) -> MigrationResult:
    graph = MigrationGraph(
        repo_path=repo_path,
        dependency=dependency,
        from_version=from_version,
        to_version=to_version,
        docs_dir=docs_dir,
        model=model,
        api_key=api_key,
        max_iterations=max_iterations,
        outputs_dir=outputs_dir,
        db_path=db_path,
        top_k=top_k,
        score_threshold=score_threshold,
        subprocess_timeout=subprocess_timeout,
        session_id=session_id,
        llm_adapter=llm_adapter,
        api_detector=api_detector,
        progress_callback=progress_callback,
    )
    return graph.run()
