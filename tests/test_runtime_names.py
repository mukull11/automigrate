import shutil
from pathlib import Path

from fake_llm import FakeLLM

from automigrate import db
from automigrate.apidiff.detector import PipApiDiffDetector
from automigrate.apidiff.runtime import (
    check_names_at_targets,
    collect_dependency_names,
)
from automigrate.graph import run_migration


def write_package(root: Path, source: str) -> None:
    package = root / "numpy"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(source, encoding="utf-8")


def test_only_runtime_removed_attribute_is_reported(tmp_path) -> None:
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    write_package(old_root, "kept = 1\nremoved = 2\n")
    write_package(new_root, "kept = 1\n")

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text(
        "import numpy as np\n"
        "from numpy import kept\n"
        "\n"
        "value = np.removed\n"
        "other = kept\n",
        encoding="utf-8",
    )

    references = collect_dependency_names(repo, "numpy")
    checks = check_names_at_targets(old_root, new_root, references)

    removed = [check for check in checks if check.removed]
    assert [check.qualified_name for check in removed] == ["numpy.removed"]
    kept = next(check for check in checks if check.name == "kept")
    assert kept.present_old is True
    assert kept.present_new is True


class LocalPipDetector(PipApiDiffDetector):
    def __init__(self, old_root: Path, new_root: Path) -> None:
        super().__init__(timeout=60)
        self.old_root = old_root
        self.new_root = new_root

    def _install(self, dependency, version, target, workspace) -> None:
        source = self.old_root if version == "1.0" else self.new_root
        shutil.copytree(source, target, dirs_exist_ok=True)


class CapturingLLM(FakeLLM):
    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[str] = []

    def complete(self, prompt, *, system=None, stage=None, iteration=0):
        self.prompts.append(prompt)
        return super().complete(
            prompt,
            system=system,
            stage=stage,
            iteration=iteration,
        )


def test_numpy_float_removal_is_detected_and_reaches_the_planner(tmp_path) -> None:
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    write_package(
        old_root,
        "float_ = float\nint_ = int\n\ndef kept_api():\n    return 1\n",
    )
    write_package(
        new_root,
        "int_ = int\n\ndef kept_api():\n    return 1\n",
    )

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text(
        "import numpy as np\n"
        "\n"
        "value = np.float_\n",
        encoding="utf-8",
    )
    (repo / "requirements.txt").write_text("numpy==1.0\n", encoding="utf-8")

    llm = CapturingLLM()
    result = run_migration(
        repo,
        "numpy",
        "1.0",
        "2.0",
        outputs_dir=tmp_path / "outputs",
        db_path=tmp_path / "automigrate.db",
        llm_adapter=llm,
        api_detector=LocalPipDetector(old_root, new_root),
    )

    assert len(result.state.api_usages) == 1
    assert result.state.api_usages[0].symbol == "numpy.float_"
    assert llm.prompts
    assert any("removed: numpy.float_" in prompt for prompt in llm.prompts)

    conn = db.init_db(tmp_path / "automigrate.db")
    rows = conn.execute(
        "SELECT output_summary FROM agent_action WHERE session_id = ?",
        (result.session_id,),
    ).fetchall()
    conn.close()
    runtime_rows = [
        row["output_summary"]
        for row in rows
        if "runtime name check" in row["output_summary"]
    ]
    assert runtime_rows
    assert "numpy.float_" in runtime_rows[0]
