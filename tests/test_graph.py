import json
from pathlib import Path

from fake_llm import FakeLLM, StaticApiDetector, rename_detector

from automigrate import db
from automigrate.apidiff.models import ApiDiff
from automigrate.graph import run_migration
from automigrate.rag.chunker import chunk_text
from automigrate.tools.git import GitRepo, tree_checksum


def build_case(
    tmp_path: Path,
    *,
    app_source: str,
    patch_text: str,
    extra_tests: str = "",
) -> Path:
    case = tmp_path / "case"
    repo = case / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "app.py").write_text(app_source, encoding="utf-8")
    (repo / "tests" / "test_api.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "\n"
        "def test_source_uses_new_api():\n"
        "    source = (\n"
        "        Path(__file__).resolve().parents[1] / 'src' / 'app.py'\n"
        "    ).read_text(encoding='utf-8')\n"
        "    assert 'new_api()' in source\n"
        + extra_tests,
        encoding="utf-8",
    )
    (repo / "requirements.txt").write_text("lib==1.0\n", encoding="utf-8")

    docs = case / "docs"
    docs.mkdir()
    doc_text = "# Renames\n\nold_api is replaced by new_api.\n"
    (docs / "guide.md").write_text(doc_text, encoding="utf-8")
    chunk_id = chunk_text(doc_text, "guide.md")[0].chunk_id

    plan = {
        "summary": "rename old_api",
        "changes": [
            {
                "file": "src/app.py",
                "old_symbol": "old_api",
                "new_symbol": "new_api",
                "description": "rename old_api to new_api",
                "confidence": 0.9,
                "cites": [chunk_id],
            }
        ],
        "expected_effect": "tests pass",
    }
    (case / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (case / "patch.txt").write_text(patch_text, encoding="utf-8")
    return case


def run_case(
    case: Path,
    tmp_path: Path,
    *,
    max_iterations: int = 5,
    detector=None,
    diagnosis: str | None = None,
):
    return run_migration(
        case / "repo",
        "lib",
        "1.0",
        "2.0",
        docs_dir=case / "docs",
        max_iterations=max_iterations,
        outputs_dir=tmp_path / "outputs",
        db_path=tmp_path / "automigrate.db",
        llm_adapter=FakeLLM(
            plan_text=(case / "plan.json").read_text(encoding="utf-8"),
            patch_text=(case / "patch.txt").read_text(encoding="utf-8"),
            diagnosis_text=diagnosis,
        ),
        api_detector=detector or rename_detector(),
    )


APP_SOURCE = (
    "from lib import old_api\n"
    "\n"
    "\n"
    "def call_api():\n"
    "    return old_api()\n"
)
APP_PATCH = (
    "FILE: src/app.py\n"
    "<<<<<<< SEARCH\n"
    "from lib import old_api\n"
    "\n"
    "\n"
    "def call_api():\n"
    "    return old_api()\n"
    "=======\n"
    "from lib import new_api\n"
    "\n"
    "\n"
    "def call_api():\n"
    "    return new_api()\n"
    ">>>>>>> REPLACE\n"
)


def test_migrate_case_succeeds_and_commits_per_iteration(tmp_path) -> None:
    case = build_case(tmp_path, app_source=APP_SOURCE, patch_text=APP_PATCH)
    repo = case / "repo"
    before = tree_checksum(repo)
    result = run_case(case, tmp_path)

    assert result.status.value == "SUCCESSFUL"
    assert result.state.latest.failed == 0
    assert result.state.latest.errors == 0
    assert len(result.commits) == 1
    workspace = result.workspace_path
    assert "new_api()" in (workspace / "src" / "app.py").read_text(encoding="utf-8")
    assert len(GitRepo(workspace).log_oneline()) == 2
    assert tree_checksum(repo) == before

    conn = db.init_db(tmp_path / "automigrate.db")
    session = db.get_session(conn, result.session_id)
    assert session is not None
    assert session["status"] == "SUCCESSFUL"
    rows = conn.execute(
        "SELECT stage FROM agent_action WHERE session_id = ?",
        (result.session_id,),
    )
    stages = {row["stage"] for row in rows}
    assert {"INGEST", "DETECT", "RETRIEVE", "PLAN", "PATCH", "VALIDATE", "REPORT"} <= stages
    conn.close()


def test_rollback_when_pass_count_drops(tmp_path) -> None:
    broken_patch = (
        "FILE: src/app.py\n"
        "<<<<<<< SEARCH\n"
        "from lib import old_api\n"
        "\n"
        "\n"
        "def call_api():\n"
        "    return old_api()\n"
        "=======\n"
        "from lib import new_api\n"
        "\n"
        "\n"
        "def call_api(:\n"
        "    return new_api()\n"
        ">>>>>>> REPLACE\n"
    )
    case = build_case(tmp_path, app_source=APP_SOURCE, patch_text=broken_patch)
    (case / "repo" / "tests" / "test_api.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "\n"
        "def _source():\n"
        "    path = Path(__file__).resolve().parents[1] / 'src' / 'app.py'\n"
        "    return path.read_text(encoding='utf-8')\n"
        "\n"
        "\n"
        "def test_one():\n"
        "    assert 'old_api()' in _source()\n"
        "\n"
        "\n"
        "def test_two():\n"
        "    assert 'old_api()' in _source()\n",
        encoding="utf-8",
    )
    result = run_case(case, tmp_path)
    assert result.status.value == "REQUIRES_HUMAN_REVIEW"
    assert "dropped" in (result.state.flag_reason or "")
    workspace = result.workspace_path
    assert "old_api()" in (workspace / "src" / "app.py").read_text(encoding="utf-8")
    assert len(GitRepo(workspace).log_oneline()) == 1


def test_max_iterations_is_enforced(tmp_path) -> None:
    noop_patch = (
        "FILE: src/app.py\n"
        "<<<<<<< SEARCH\n"
        "from lib import old_api\n"
        "\n"
        "\n"
        "def call_api():\n"
        "    return old_api()\n"
        "=======\n"
        "from lib import old_api\n"
        "\n"
        "\n"
        "def call_api():\n"
        "    return old_api()\n"
        ">>>>>>> REPLACE\n"
    )
    case = build_case(tmp_path, app_source=APP_SOURCE, patch_text=noop_patch)
    result = run_case(case, tmp_path, max_iterations=2)
    assert result.status.value == "REQUIRES_HUMAN_REVIEW"
    assert "maximum iterations" in (result.state.flag_reason or "")
    assert len(result.commits) == 2


def test_flags_insufficient_evidence_without_patching(tmp_path) -> None:
    case = tmp_path / "case"
    repo = case / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    source = "from lib import old_api\n\n\ndef call_api():\n    return old_api()\n"
    (repo / "src" / "app.py").write_text(source, encoding="utf-8")
    (repo / "tests" / "test_app.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "\n"
        "def test_source():\n"
        "    path = Path(__file__).resolve().parents[1] / 'src' / 'app.py'\n"
        "    assert 'old_api()' in path.read_text(encoding='utf-8')\n",
        encoding="utf-8",
    )
    (repo / "requirements.txt").write_text("lib==1.0\n", encoding="utf-8")

    docs = case / "docs"
    docs.mkdir()
    doc_text = (
        "# Behavior change\n\n"
        "old_api behavior changed in version 2.0. "
        "No documented replacement exists.\n"
    )
    (docs / "guide.md").write_text(doc_text, encoding="utf-8")
    chunk_id = chunk_text(doc_text, "guide.md")[0].chunk_id
    plan = {
        "summary": "rename old_api to new_api",
        "changes": [
            {
                "file": "src/app.py",
                "old_symbol": "old_api",
                "new_symbol": "new_api",
                "description": "rename old_api to new_api",
                "confidence": 0.9,
                "cites": [chunk_id],
            }
        ],
        "expected_effect": "tests pass",
    }
    (case / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (case / "patch.txt").write_text(
        "FILE: src/app.py\n"
        "<<<<<<< SEARCH\n"
        "from lib import old_api\n"
        "=======\n"
        "from lib import new_api\n"
        ">>>>>>> REPLACE\n",
        encoding="utf-8",
    )

    before = tree_checksum(repo)
    result = run_migration(
        repo,
        "lib",
        "1.0",
        "2.0",
        docs_dir=docs,
        outputs_dir=tmp_path / "outputs",
        db_path=tmp_path / "automigrate.db",
        llm_adapter=FakeLLM(
            plan_text=(case / "plan.json").read_text(encoding="utf-8"),
            patch_text=(case / "patch.txt").read_text(encoding="utf-8"),
        ),
        api_detector=StaticApiDetector(ApiDiff()),
    )

    assert result.status.value == "REQUIRES_HUMAN_REVIEW"
    assert result.state.diagnosis.value == "INSUFFICIENT_EVIDENCE"
    assert result.commits == []
    workspace_source = (result.workspace_path / "src" / "app.py").read_text(
        encoding="utf-8"
    )
    assert workspace_source == source
    assert tree_checksum(repo) == before
