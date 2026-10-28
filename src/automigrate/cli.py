"""Typer command line interface for AutoMigrate."""

from __future__ import annotations

from pathlib import Path

import typer

from automigrate import __version__, db
from automigrate.apidiff.detector import ApiDiffError
from automigrate.auto import AutoMigrationResult, run_auto_migration
from automigrate.config import Settings, get_settings
from automigrate.ingest.dependencies import normalize_name
from automigrate.ingest.scanner import ScanResult, scan_repository
from automigrate.llm import LLMConfigurationError
from automigrate.llm.keys import (
    MissingKeyError,
    resolve_api_key,
    select_default_provider,
)
from automigrate.llm.providers import UnknownProviderError, get_provider
from automigrate.rag.index import index_documents
from automigrate.report import ReportError, write_report
from automigrate.state import Status
from automigrate.tools.git import GitError

app = typer.Typer(
    name="automigrate",
    help="Test-guided dependency major-version migration for Python repositories.",
    no_args_is_help=True,
    add_completion=False,
    invoke_without_command=True,
)


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", help="Show the AutoMigrate version."),
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit(code=0)


def _print_scan(result: ScanResult) -> None:
    typer.echo(f"Repository: {result.repo_path}")
    typer.echo(f"Python files: {len(result.python_files)}")
    typer.echo(f"Declared dependencies: {len(result.declarations)}")
    for declaration in result.declarations:
        version = declaration.version or "unpinned"
        typer.echo(f"  {declaration.name} {version} ({declaration.source_file})")
    if not result.usages:
        typer.echo("Usages: none")
        return
    typer.echo(f"Usages: {len(result.usages)}")
    for usage in result.usages:
        typer.echo(
            f"  {usage.file}:{usage.line} {usage.kind} {usage.symbol} | {usage.snippet}"
        )


@app.command()
def scan(
    repository: Path = typer.Argument(..., exists=True, help="Repository to scan."),
    dependency: str | None = typer.Option(
        None, "--dependency", help="Only report usages for this dependency."
    ),
) -> None:
    """Scan a repository for dependency declarations and usages."""
    result = scan_repository(repository, dependency)
    _print_scan(result)
    if dependency is not None and not result.declared:
        raise typer.Exit(code=2)

    if dependency is None:
        seen: set[str] = set()
        for declaration in result.declarations:
            key = normalize_name(declaration.name)
            if key in seen:
                continue
            seen.add(key)
            typer.echo()
            typer.echo(f"Usages for {declaration.name}:")
            _print_scan(scan_repository(repository, declaration.name))


@app.command("index-docs")
def index_docs(
    docs: Path = typer.Option(..., "--docs", exists=True, help="Documentation directory."),
    dependency: str = typer.Option(..., "--dependency", help="Dependency name."),
    version_hint: str | None = typer.Option(
        None, "--version-hint", help="Migration version hint for indexed chunks."
    ),
    db_path: Path | None = typer.Option(None, "--db", help="SQLite database path."),
) -> None:
    """Chunk and index migration documentation."""
    settings = get_settings(**({"db_path": db_path} if db_path else {}))
    conn = db.init_db(settings.db_path)
    try:
        stats = index_documents(conn, docs, dependency, version_hint)
    finally:
        conn.close()
    typer.echo(
        f"Indexed {stats.files_indexed} file(s), skipped {stats.files_skipped} "
        f"unchanged file(s), wrote {stats.chunks_written} chunk(s) "
        f"for {dependency}."
    )


@app.command()
def migrate(
    repository: str = typer.Argument(
        ..., help="Repository folder path or GitHub URL."
    ),
    docs: Path | None = typer.Option(None, "--docs", help="Documentation directory."),
    max_iterations: int | None = typer.Option(
        None, "--max-iterations", min=1, help="Maximum repair iterations."
    ),
    max_dependencies: int | None = typer.Option(
        None,
        "--max-dependencies",
        min=0,
        help="Maximum outdated dependencies to attempt (0 = all).",
    ),
    allow_undocumented: bool | None = typer.Option(
        None,
        "--allow-undocumented/--no-allow-undocumented",
        help=(
            "Accept confident plans even when the docs do not name a replacement."
        ),
    ),
    provider: str | None = typer.Option(
        None,
        "--provider",
        help="LLM provider id (for example gemini, openai, anthropic, groq).",
    ),
    model: str | None = typer.Option(
        None, "--model", help="Primary model for the selected provider."
    ),
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        help="Override the provider base URL (required for the custom provider).",
    ),
    out: Path | None = typer.Option(None, "--out", help="Output directory."),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        help="Overrides the provider's environment key for this run only.",
    ),
) -> None:
    """Discover outdated dependencies, triage them, and migrate each one."""
    settings = settings_from_cli(
        max_iterations=max_iterations,
        max_dependencies=max_dependencies,
        allow_undocumented=allow_undocumented,
        out=out,
        model=model,
    )
    try:
        selected = get_provider(provider) if provider else select_default_provider()
    except UnknownProviderError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except MissingKeyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    try:
        resolved_key = resolve_api_key(selected, api_key)
    except MissingKeyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    selected_model = (settings.model or "").strip()
    if not selected_model and selected.default_models:
        selected_model = selected.default_models[0]
    typer.echo(f"Provider: {selected.id} ({selected.display_name})")
    typer.echo(f"Model: {selected_model or 'provider default'}")
    try:
        result = run_auto_migration(
            repository,
            docs_dir=docs or settings.docs_dir,
            model=selected_model or None,
            provider=selected.id,
            base_url=base_url,
            api_key=resolved_key,
            max_iterations=settings.max_iterations,
            max_dependencies=settings.max_dependencies,
            allow_undocumented=settings.allow_undocumented,
            outputs_dir=settings.outputs_dir,
            db_path=settings.db_path,
            top_k=settings.top_k,
            score_threshold=settings.score_threshold,
            subprocess_timeout=settings.subprocess_timeout,
        )
    except (LLMConfigurationError, ApiDiffError, GitError, FileNotFoundError) as exc:
        typer.echo(f"migration failed to start: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        typer.echo(f"migration failed to start: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    _print_auto_result(result)
    raise typer.Exit(code=0 if result.succeeded else 2)


def _print_auto_result(result: AutoMigrationResult) -> None:
    typer.echo("Detected dependencies:")
    if not result.candidates:
        typer.echo("  none")
    for candidate in result.candidates:
        current = candidate.current_version or "unknown"
        latest = candidate.latest_version or "unknown"
        typer.echo(
            f"  {candidate.name}: {current} -> {latest} "
            f"(usages={candidate.usage_count}, {candidate.status}, "
            f"{candidate.source_file})"
        )

    if result.triage is not None:
        typer.echo()
        typer.echo("Triage:")
        typer.echo(f"  {result.triage.summary or 'no summary'}")
        if not result.triage.migrations:
            typer.echo("  selected: none")
        for decision in result.triage.migrations:
            typer.echo(
                f"  selected {decision.dependency} "
                f"{decision.from_version} -> {decision.to_version}"
                + (f": {decision.reason}" if decision.reason else "")
            )
        for skipped in result.triage.skipped:
            typer.echo(f"  skipped {skipped.dependency}: {skipped.reason}")

    if result.runs:
        typer.echo()
        typer.echo("Per-dependency results:")
        for run in result.runs:
            typer.echo(f"  {run.dependency}: {run.status.value}")
            if run.error:
                typer.echo(f"    error: {run.error}")
            elif run.result is not None:
                state = run.result.state
                typer.echo(
                    f"    session {run.result.session_id}; tests "
                    f"{state.baseline.passed} passed -> "
                    f"{state.latest.passed} passed; "
                    f"workspace {run.result.workspace_path}"
                )
                if state.flag_reason:
                    typer.echo(f"    reason: {state.flag_reason}")

    typer.echo()
    typer.echo(f"Overall status: {result.status}")
    typer.echo(result.summary)
    typer.echo(f"Outputs: {result.outputs_dir}")


@app.command()
def report(
    session_id: str = typer.Argument(..., help="Migration session id."),
    db_path: Path | None = typer.Option(None, "--db", help="SQLite database path."),
    out: Path | None = typer.Option(None, "--out", help="Output directory."),
) -> None:
    """Render the report for a completed migration session."""
    settings = get_settings(
        **{
            **({"db_path": db_path} if db_path else {}),
            **({"outputs_dir": out} if out else {}),
        }
    )
    conn = db.init_db(settings.db_path)
    try:
        result = write_report(conn, session_id, settings.outputs_dir)
    except ReportError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    finally:
        conn.close()

    typer.echo(result.markdown)
    typer.echo(f"Report written to {result.report_path}")
    raise typer.Exit(code=0 if result.status == Status.SUCCESSFUL.value else 2)


def settings_from_cli(
    *,
    max_iterations: int | None = None,
    max_dependencies: int | None = None,
    allow_undocumented: bool | None = None,
    out: Path | None = None,
    model: str | None = None,
) -> Settings:
    overrides: dict[str, object] = {}
    if max_iterations is not None:
        overrides["max_iterations"] = max_iterations
    if max_dependencies is not None:
        overrides["max_dependencies"] = max_dependencies
    if allow_undocumented is not None:
        overrides["allow_undocumented"] = allow_undocumented
    if out is not None:
        overrides["outputs_dir"] = out
    if model is not None:
        overrides["model"] = model
    return get_settings(**overrides)
