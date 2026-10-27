from typer.testing import CliRunner

from automigrate.cli import app

runner = CliRunner()


def test_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("scan", "index-docs", "migrate", "report"):
        assert command in result.stdout


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_scan_requires_existing_repo(tmp_path) -> None:
    result = runner.invoke(app, ["scan", str(tmp_path / "missing")])
    assert result.exit_code != 0


def test_scan_lists_usages(tmp_path) -> None:
    repo = tmp_path / "repo"
    source = repo / "src" / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text("from lib import old_api\n\nold_api()\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("lib==1.0\n", encoding="utf-8")

    result = runner.invoke(app, ["scan", str(repo), "--dependency", "lib"])
    assert result.exit_code == 0
    assert "Usages: 2" in result.stdout
    assert "lib.old_api" in result.stdout


def test_scan_unknown_dependency_exits_two(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("lib==1.0\n", encoding="utf-8")
    result = runner.invoke(app, ["scan", str(repo), "--dependency", "other"])
    assert result.exit_code == 2


def test_migrate_without_api_key_exits_with_clear_message(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("lib==1.0.0\n", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "migrate",
            str(repo),
            "--dependency",
            "lib",
            "--current",
            "1.0.0",
            "--target",
            "2.0.0",
        ],
    )
    assert result.exit_code == 1
    assert "OPENAI_API_KEY" in result.output
    assert "docs/real_llm.md" in result.output


def test_migrate_rejects_removed_mock_option(tmp_path) -> None:
    result = runner.invoke(app, ["migrate", str(tmp_path), "--llm", "mock"])
    assert result.exit_code != 0


def test_index_docs_command(tmp_path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Guide\n\nold_api is new_api.\n", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "index-docs",
            "--docs",
            str(docs),
            "--dependency",
            "lib",
            "--db",
            str(tmp_path / "index.db"),
        ],
    )
    assert result.exit_code == 0
    assert "wrote 1 chunk" in result.stdout
