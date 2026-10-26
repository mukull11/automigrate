"""Typer command line interface for AutoMigrate."""

from __future__ import annotations

from pathlib import Path

import typer

from automigrate import __version__
from automigrate.config import Settings, get_settings

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


@app.command()
def scan(repo: Path = typer.Argument(..., exists=True, help="Repository to scan.")) -> None:
    """Scan a repository for dependency declarations and usages."""
    _pending("scan")


@app.command("index-docs")
def index_docs(
    docs: Path = typer.Option(..., "--docs", exists=True, help="Documentation directory."),
    dependency: str = typer.Option(..., "--dependency", help="Dependency name."),
) -> None:
    """Chunk and index migration documentation."""
    _pending("index-docs")


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
