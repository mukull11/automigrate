"""Typer command line interface for AutoMigrate."""

from __future__ import annotations

from pathlib import Path

import typer

from automigrate import __version__, db
from automigrate.config import Settings, get_settings
from automigrate.evaluation import run_evaluation
from automigrate.graph import run_migration
from automigrate.ingest.dependencies import normalize_name
from automigrate.ingest.scanner import ScanResult, scan_repository
from automigrate.rag.index import index_documents
from automigrate.report import ReportError, write_report
from automigrate.state import Status

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
    repo: Path = typer.Argument(..., exists=True, help="Repository to scan."),
    dependency: str | None = typer.Option(
        None, "--dependency", help="Only report usages for this dependency."
    ),
) -> None:
    """Scan a repository for dependency declarations and usages."""
    result = scan_repository(repo, dependency)
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
            _print_scan(scan_repository(repo, declaration.name))


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
    repo: Path = typer.Argument(..., exists=True, help="Repository to migrate."),
    dependency: str = typer.Option(..., "--dependency", help="Dependency name."),
    from_version: str = typer.Option(..., "--from", help="Current major version."),
    to_version: str = typer.Option(..., "--to", help="Target major version."),
    docs: Path | None = typer.Option(None, "--docs", help="Documentation directory."),
    max_iterations: int | None = typer.Option(
        None, "--max-iterations", min=1, help="Maximum repair iterations."
    ),
    llm: str | None = typer.Option(None, "--llm", help="anthropic|openai|mock"),
    out: Path | None = typer.Option(None, "--out", help="Output directory."),
) -> None:
    """Run a migration from the source version to the target version."""
    settings = settings_from_cli(max_iterations=max_iterations, out=out, llm=llm)
    try:
        result = run_migration(
            repo,
            dependency,
            from_version,
            to_version,
            docs_dir=docs or settings.docs_dir,
            llm=settings.llm_provider,
            max_iterations=settings.max_iterations,
            outputs_dir=settings.outputs_dir,
            db_path=settings.db_path,
            top_k=settings.top_k,
            score_threshold=settings.score_threshold,
            subprocess_timeout=settings.subprocess_timeout,
        )
    except Exception as exc:
        typer.echo(f"migration failed to start: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    state = result.state
    typer.echo(f"Session: {result.session_id}")
    typer.echo(f"Status: {state.status.value}")
    typer.echo(
        f"Tests: baseline {state.baseline.passed} passed / "
        f"{state.baseline.failed} failed -> latest {state.latest.passed} passed / "
        f"{state.latest.failed} failed"
    )
    typer.echo(f"Workspace: {result.workspace_path}")
    if state.flag_reason:
        typer.echo(f"Reason: {state.flag_reason}")
    raise typer.Exit(code=0 if state.status == Status.SUCCESSFUL else 2)


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


@app.command("eval")
def evaluate(
    llm: str = typer.Option("mock", "--llm", help="anthropic|openai|mock"),
    cases: Path = typer.Option(
        Path("benchmarks/cases"), "--cases", help="Benchmark cases directory."
    ),
) -> None:
    """Run the benchmark suite."""
    results, metrics = run_evaluation(cases, llm=llm)
    typer.echo(
        f"Cases passed: {metrics['cases_passed']}/{metrics['cases']} | "
        f"success rate {metrics['migration_success_rate']:.0%} | "
        f"avg iterations {metrics['average_iterations']:.2f} | "
        f"time {metrics['execution_time_s']:.2f}s"
    )
    for item in results:
        marker = "PASS" if item.passed else "FAIL"
        typer.echo(
            f"  [{marker}] {item.case.name}: {item.actual_status}"
            + (f" ({item.actual_diagnosis})" if item.actual_diagnosis else "")
            + (f" - {item.flag_reason}" if item.flag_reason else "")
        )
    if any(not item.passed for item in results):
        raise typer.Exit(code=1)


def settings_from_cli(
    *,
    max_iterations: int | None = None,
    out: Path | None = None,
    llm: str | None = None,
) -> Settings:
    overrides: dict[str, object] = {}
    if max_iterations is not None:
        overrides["max_iterations"] = max_iterations
    if out is not None:
        overrides["outputs_dir"] = out
    if llm is not None:
        overrides["llm_provider"] = llm
    return get_settings(**overrides)
