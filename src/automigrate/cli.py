"""Typer command line interface for AutoMigrate."""

from __future__ import annotations

from pathlib import Path

import typer

from automigrate import __version__, db
from automigrate.config import Settings, get_settings
from automigrate.ingest.dependencies import normalize_name
from automigrate.ingest.scanner import ScanResult, scan_repository
from automigrate.rag.index import index_documents

app = typer.Typer(
    name="automigrate",
    help="Test-guided dependency major-version migration for Python repositories.",
    no_args_is_help=True,
    add_completion=False,
    invoke_without_command=True,
)


def _pending(command: str) -> None:
    typer.echo(f"{command} is not implemented yet.", err=True)
    raise typer.Exit(code=1)


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
    _pending("migrate")


@app.command()
def report(session_id: str = typer.Argument(..., help="Migration session id.")) -> None:
    """Render the report for a completed migration session."""
    _pending("report")


@app.command("eval")
def evaluate(
    llm: str = typer.Option("mock", "--llm", help="anthropic|openai|mock"),
    cases: Path = typer.Option(
        Path("benchmarks/cases"), "--cases", help="Benchmark cases directory."
    ),
) -> None:
    """Run the benchmark suite."""
    _pending("eval")


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
