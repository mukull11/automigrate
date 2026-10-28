from types import SimpleNamespace

from typer.testing import CliRunner

from automigrate.agent.triage import TriageDecision, TriageResult
from automigrate.auto import (
    FAILED,
    NO_CHANGES,
    PARTIAL,
    SUCCESSFUL,
    AutoMigrationResult,
    DependencyRun,
)
from automigrate.cli import app
from automigrate.ingest.versions import DependencyCandidate
from automigrate.state import Status, TestRunResult

runner = CliRunner()


def _fake_auto_result(tmp_path, status: str = NO_CHANGES):
    return AutoMigrationResult(
        repo_input=str(tmp_path),
        source_path=tmp_path,
        status=status,
        summary="fake summary",
        outputs_dir=tmp_path / "outputs",
    )


def _migrate_args(tmp_path, *extra):
    return ["migrate", str(tmp_path), *extra]


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
    for variable in (
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GROQ_API_KEY",
        "DASHSCOPE_API_KEY",
        "DEEPSEEK_API_KEY",
        "XAI_API_KEY",
        "MISTRAL_API_KEY",
        "OPENROUTER_API_KEY",
        "AUTOMIGRATE_CUSTOM_API_KEY",
    ):
        monkeypatch.delenv(variable, raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("lib==1.0.0\n", encoding="utf-8")
    result = runner.invoke(app, ["migrate", str(repo)])
    assert result.exit_code == 1
    assert "docs/real_llm.md" in result.output


def test_migrate_rejects_removed_flags(tmp_path) -> None:
    for flag in ("--dependency", "--current", "--target"):
        result = runner.invoke(app, ["migrate", str(tmp_path), flag, "x"])
        assert result.exit_code != 0


def test_migrate_rejects_removed_mock_option(tmp_path) -> None:
    result = runner.invoke(app, ["migrate", str(tmp_path), "--llm", "mock"])
    assert result.exit_code != 0


def test_migrate_proceeds_with_environment_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    captured: dict = {}

    def fake_run_auto_migration(*args, **kwargs):
        captured.update(kwargs)
        return _fake_auto_result(tmp_path)

    monkeypatch.setattr("automigrate.cli.run_auto_migration", fake_run_auto_migration)
    result = runner.invoke(app, _migrate_args(tmp_path))

    assert result.exit_code == 0
    assert captured["api_key"] == "environment-key"
    assert captured["max_dependencies"] == 0


def test_migrate_api_key_flag_overrides_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    captured: dict = {}

    def fake_run_auto_migration(*args, **kwargs):
        captured.update(kwargs)
        return _fake_auto_result(tmp_path)

    monkeypatch.setattr("automigrate.cli.run_auto_migration", fake_run_auto_migration)
    result = runner.invoke(app, _migrate_args(tmp_path, "--api-key", "flag-key"))

    assert result.exit_code == 0
    assert captured["api_key"] == "flag-key"


def test_migrate_forwards_allow_undocumented_flag(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    captured: dict = {}

    def fake_run_auto_migration(*args, **kwargs):
        captured.update(kwargs)
        return _fake_auto_result(tmp_path)

    monkeypatch.setattr("automigrate.cli.run_auto_migration", fake_run_auto_migration)
    result = runner.invoke(app, _migrate_args(tmp_path, "--allow-undocumented"))

    assert result.exit_code == 0
    assert captured["allow_undocumented"] is True


def test_migrate_reads_allow_undocumented_from_environment(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    monkeypatch.setenv("AUTOMIGRATE_ALLOW_UNDOCUMENTED", "true")
    captured: dict = {}

    def fake_run_auto_migration(*args, **kwargs):
        captured.update(kwargs)
        return _fake_auto_result(tmp_path)

    monkeypatch.setattr("automigrate.cli.run_auto_migration", fake_run_auto_migration)
    result = runner.invoke(app, _migrate_args(tmp_path))

    assert result.exit_code == 0
    assert captured["allow_undocumented"] is True


def test_migrate_forwards_max_dependencies(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    captured: dict = {}

    def fake_run_auto_migration(*args, **kwargs):
        captured.update(kwargs)
        return _fake_auto_result(tmp_path)

    monkeypatch.setattr("automigrate.cli.run_auto_migration", fake_run_auto_migration)
    result = runner.invoke(app, _migrate_args(tmp_path, "--max-dependencies", "4"))

    assert result.exit_code == 0
    assert captured["max_dependencies"] == 4


def test_migrate_no_changes_exits_zero(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")

    def fake_run_auto_migration(*args, **kwargs):
        return _fake_auto_result(tmp_path, NO_CHANGES)

    monkeypatch.setattr("automigrate.cli.run_auto_migration", fake_run_auto_migration)
    result = runner.invoke(app, _migrate_args(tmp_path))
    assert result.exit_code == 0
    assert "Overall status: NO_CHANGES" in result.stdout


def test_migrate_all_successful_exits_zero(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")

    def fake_run_auto_migration(*args, **kwargs):
        return _fake_auto_result(tmp_path, SUCCESSFUL)

    monkeypatch.setattr("automigrate.cli.run_auto_migration", fake_run_auto_migration)
    result = runner.invoke(app, _migrate_args(tmp_path))
    assert result.exit_code == 0


def test_migrate_failed_exits_two(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")

    def fake_run_auto_migration(*args, **kwargs):
        return _fake_auto_result(tmp_path, FAILED)

    monkeypatch.setattr("automigrate.cli.run_auto_migration", fake_run_auto_migration)
    result = runner.invoke(app, _migrate_args(tmp_path))
    assert result.exit_code == 2


def test_migrate_prints_candidates_and_per_dependency_results(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    candidate = DependencyCandidate(
        name="numpy",
        current_version="1.0.0",
        latest_version="2.0.0",
        source_file="requirements.txt",
        usage_count=3,
        status="outdated",
    )
    success = SimpleNamespace(
        session_id="session-numpy",
        workspace_path=tmp_path / "workspace",
        status=Status.SUCCESSFUL,
        state=SimpleNamespace(
            baseline=TestRunResult(passed=1),
            latest=TestRunResult(passed=2),
            flag_reason=None,
        ),
    )
    result = AutoMigrationResult(
        repo_input=str(tmp_path),
        source_path=tmp_path,
        candidates=[candidate],
        triage=TriageResult(
            migrations=[
                TriageDecision(
                    dependency="numpy",
                    from_version="1.0.0",
                    to_version="2.0.0",
                    reason="most used",
                )
            ],
            summary="migrate numpy",
        ),
        runs=[
            DependencyRun(
                dependency="numpy",
                from_version="1.0.0",
                to_version="2.0.0",
                result=success,
            ),
            DependencyRun(
                dependency="requests",
                from_version="1.0.0",
                to_version="2.0.0",
                error="boom",
            ),
        ],
        status=PARTIAL,
        summary="1/2 selected dependency migration(s) succeeded.",
        outputs_dir=tmp_path / "outputs",
    )

    monkeypatch.setattr(
        "automigrate.cli.run_auto_migration", lambda *a, **k: result
    )
    invoked = runner.invoke(app, _migrate_args(tmp_path))

    assert invoked.exit_code == 2
    assert "Detected dependencies:" in invoked.stdout
    assert "numpy: 1.0.0 -> 2.0.0" in invoked.stdout
    assert "selected numpy 1.0.0 -> 2.0.0" in invoked.stdout
    assert "numpy: SUCCESSFUL" in invoked.stdout
    assert "error: boom" in invoked.stdout
    assert "Overall status: PARTIAL" in invoked.stdout


def test_scan_without_dependency_lists_each_dependency(tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text(
        "from lib import old_api\n\nold_api()\n", encoding="utf-8"
    )
    (repo / "requirements.txt").write_text("lib==1.0\n", encoding="utf-8")
    result = runner.invoke(app, ["scan", str(repo)])
    assert result.exit_code == 0
    assert "Usages for lib:" in result.stdout


def test_migrate_unknown_provider_exits_one(tmp_path) -> None:
    result = runner.invoke(
        app, ["migrate", str(tmp_path), "--provider", "does-not-exist"]
    )
    assert result.exit_code == 1
    assert "Unknown provider" in result.output


def test_migrate_missing_key_for_explicit_provider_exits_one(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    result = runner.invoke(app, ["migrate", str(tmp_path), "--provider", "groq"])
    assert result.exit_code == 1
    assert "GROQ_API_KEY" in result.output


def test_migrate_startup_error_exits_one(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")

    def fake_run_auto_migration(*args, **kwargs):
        raise FileNotFoundError("missing repo")

    monkeypatch.setattr("automigrate.cli.run_auto_migration", fake_run_auto_migration)
    result = runner.invoke(app, _migrate_args(tmp_path))
    assert result.exit_code == 1
    assert "migration failed to start" in result.output


def test_migrate_forwards_resources_and_output_options(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    captured: dict = {}
    output = tmp_path / "custom-out"

    def fake_run_auto_migration(*args, **kwargs):
        captured.update(kwargs)
        return _fake_auto_result(tmp_path)

    monkeypatch.setattr("automigrate.cli.run_auto_migration", fake_run_auto_migration)
    result = runner.invoke(
        app,
        _migrate_args(
            tmp_path,
            "--max-iterations",
            "2",
            "--model",
            "custom-model",
            "--out",
            str(output),
        ),
    )

    assert result.exit_code == 0
    assert captured["max_iterations"] == 2
    assert captured["model"] == "custom-model"
    assert captured["outputs_dir"] == output


def test_migrate_generic_startup_error_exits_one(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")

    def fake_run_auto_migration(*args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr("automigrate.cli.run_auto_migration", fake_run_auto_migration)
    result = runner.invoke(app, _migrate_args(tmp_path))
    assert result.exit_code == 1
    assert "migration failed to start" in result.output


def test_migrate_prints_empty_triage_and_flag_reason(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    flagged = SimpleNamespace(
        session_id="session-one",
        workspace_path=tmp_path / "workspace",
        status=Status.REQUIRES_HUMAN_REVIEW,
        state=SimpleNamespace(
            baseline=TestRunResult(passed=1),
            latest=TestRunResult(passed=1),
            flag_reason="needs review",
        ),
    )
    result = AutoMigrationResult(
        repo_input=str(tmp_path),
        source_path=tmp_path,
        candidates=[],
        triage=TriageResult(migrations=[], summary="nothing selected"),
        runs=[
            DependencyRun(
                dependency="numpy",
                from_version="1.0.0",
                to_version="2.0.0",
                result=flagged,
            )
        ],
        status=FAILED,
        summary="nothing succeeded",
        outputs_dir=tmp_path / "outputs",
    )
    monkeypatch.setattr("automigrate.cli.run_auto_migration", lambda *a, **k: result)
    invoked = runner.invoke(app, _migrate_args(tmp_path))
    assert invoked.exit_code == 2
    assert "selected: none" in invoked.stdout
    assert "reason: needs review" in invoked.stdout


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
