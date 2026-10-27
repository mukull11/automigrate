import os
import subprocess
from pathlib import Path

import pytest

from automigrate.tools.filesystem import ReadOnlyPath, Sandbox, SandboxViolation
from automigrate.tools.git import (
    GitError,
    GitRepo,
    is_repository_url,
    prepare_workspace,
    tree_checksum,
)
from automigrate.tools.lint_runner import RuffRunner
from automigrate.tools.subprocess import DisallowedCommand, SubprocessRunner
from automigrate.tools.test_runner import PytestRunner, parse_pytest_report


def test_sandbox_allows_inside_paths(tmp_path) -> None:
    sandbox = Sandbox(tmp_path)
    sandbox.write_text("src/app.py", "print('ok')\n")
    assert sandbox.read_text("src/app.py") == "print('ok')\n"
    assert sandbox.exists("src/app.py")
    assert sandbox.relative("src/app.py") == Path("src/app.py")


@pytest.mark.parametrize("path", ["../outside.txt", "src/../../outside.txt", "../../etc/passwd"])
def test_sandbox_rejects_traversal(tmp_path, path) -> None:
    sandbox = Sandbox(tmp_path)
    with pytest.raises(SandboxViolation):
        sandbox.resolve(path)


def test_sandbox_rejects_absolute_paths(tmp_path) -> None:
    sandbox = Sandbox(tmp_path)
    target = tmp_path.parent / "outside.txt"
    with pytest.raises(SandboxViolation):
        sandbox.read_text(target)


def test_sandbox_rejects_symlink_escape(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = root / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("symlink creation is not permitted on this machine")
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            pytest.skip("directory link creation is not permitted on this machine")
    sandbox = Sandbox(root)
    with pytest.raises(SandboxViolation):
        sandbox.read_text("link/secret.txt")


def test_sandbox_protects_test_files(tmp_path) -> None:
    sandbox = Sandbox(tmp_path)
    (tmp_path / "tests").mkdir()
    test_file = tmp_path / "tests" / "test_app.py"
    test_file.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    assert sandbox.read_text("tests/test_app.py")
    with pytest.raises(ReadOnlyPath):
        sandbox.write_text("tests/test_app.py", "def test_ok():\n    pass\n")
    with pytest.raises(ReadOnlyPath):
        sandbox.write_text("test_root.py", "pass\n")
    sandbox.write_text("src/app.py", "pass\n")


def test_subprocess_allowlist_rejects_other_commands(tmp_path) -> None:
    runner = SubprocessRunner(tmp_path)
    for command in ("python", "powershell", "cmd", "rm", "bash"):
        with pytest.raises(DisallowedCommand):
            runner.run(command, ["--version"])


def test_subprocess_rejects_cwd_outside_workspace(tmp_path) -> None:
    runner = SubprocessRunner(tmp_path)
    with pytest.raises(SandboxViolation):
        runner.run("git", ["--version"], cwd=tmp_path.parent)


def test_pytest_and_ruff_runners(tmp_path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sample.py").write_text(
        "def test_ok():\n    assert True\n\n\ndef test_bad():\n    assert False\n",
        encoding="utf-8",
    )
    runner = SubprocessRunner(tmp_path)
    result = PytestRunner(runner).run(["tests"])
    assert result.passed == 1
    assert result.failed == 1
    assert any("test_bad" in name for name in result.failed_test_names)
    assert result.first_traceback

    (tmp_path / "bad.py").write_text("import os\n", encoding="utf-8")
    ruff = RuffRunner(runner).run(["bad.py"])
    assert ruff.issue_count >= 1
    assert any(issue["code"] == "F401" for issue in ruff.issues)


def test_parse_pytest_report() -> None:
    report = {
        "duration": 0.25,
        "summary": {"passed": 2, "failed": 1, "error": 1},
        "tests": [
            {"nodeid": "tests/test_a.py::test_ok", "outcome": "passed"},
            {"nodeid": "tests/test_a.py::test_bad", "outcome": "failed"},
            {"nodeid": "tests/test_a.py::test_error", "outcome": "error"},
        ],
    }
    result = parse_pytest_report(report)
    assert result.passed == 2
    assert result.failed == 1
    assert result.errors == 1
    assert result.failed_test_names == [
        "tests/test_a.py::test_bad",
        "tests/test_a.py::test_error",
    ]
    assert result.duration_s == 0.25


def test_workspace_git_flow_leaves_source_untouched(tmp_path) -> None:
    source = tmp_path / "repo"
    source.mkdir()
    (source / "app.py").write_text("old_api()\n", encoding="utf-8")
    (source / "requirements.txt").write_text("lib==1.0\n", encoding="utf-8")
    before = tree_checksum(source)

    workspace = tmp_path / "workspace"
    repo = prepare_workspace(source, workspace)
    assert repo.has_commits()
    baseline = repo.head_sha()
    assert len(repo.log_oneline()) == 1

    sandbox = Sandbox(workspace)
    sandbox.write_text("app.py", "new_api()\n")
    repo.add_all()
    changed = repo.commit("automigrate: iter 1 - rename old_api")
    assert changed != baseline
    assert len(repo.log_oneline()) == 2
    assert "new_api" in repo.diff_commit(changed)

    repo.reset_hard(baseline)
    assert sandbox.read_text("app.py") == "old_api()\n"
    assert tree_checksum(source) == before


def test_pytest_runner_counts_collection_errors(tmp_path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_broken.py").write_text(
        "import module_that_does_not_exist\n", encoding="utf-8"
    )
    result = PytestRunner(SubprocessRunner(tmp_path)).run(["tests"])
    assert result.errors == 1
    assert result.first_traceback


def test_git_repo_rejects_failed_command(tmp_path) -> None:
    repo = GitRepo(tmp_path)
    with pytest.raises(GitError):
        repo.head_sha()


def test_repository_url_detection() -> None:
    assert is_repository_url("https://github.com/org/repo")
    assert is_repository_url("git@github.com:org/repo.git")
    assert is_repository_url("org/repo.git")
    assert not is_repository_url(r"C:\work\repo")
    assert not is_repository_url(".")
