from pathlib import Path

from automigrate.ingest.analyzer import analyze_source
from automigrate.ingest.dependencies import detect_declarations, find_declaration
from automigrate.ingest.scanner import find_python_files, scan_repository
from automigrate.state import Usage


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    source = repo / "src" / "app.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from fake_lib import old_api\n"
        "import fake_lib as fl\n"
        "\n"
        'old_api(mode="fast")\n'
        "fl.old_api()\n"
        "fl.helper.attribute\n"
        "value = old_api\n",
        encoding="utf-8",
    )
    (repo / "requirements.txt").write_text("Fake_Lib==1.4.2\nrequests>=2\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        "[project]\n"
        'name = "demo"\n'
        'dependencies = ["pandas==1.5.3"]\n'
        "\n"
        "[tool.poetry.dependencies]\n"
        'python = "^3.10"\n'
        'numpy = "^1.24"\n',
        encoding="utf-8",
    )
    (repo / "setup.cfg").write_text(
        "[options]\ninstall_requires =\n    attrs>=22.1\n", encoding="utf-8"
    )
    (repo / ".venv" / "lib").mkdir(parents=True)
    (repo / ".venv" / "lib" / "ignored.py").write_text("import fake_lib\n", encoding="utf-8")
    return repo


def test_detect_declarations_from_supported_files(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    declarations = detect_declarations(repo)
    names = {declaration.name.lower() for declaration in declarations}
    assert {"fake_lib", "requests", "pandas", "numpy", "attrs"} <= names
    fake = find_declaration(declarations, "fake-lib")
    assert fake is not None
    assert fake.version == "1.4.2"


def test_find_python_files_skips_generated_dirs(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    files = find_python_files(repo)
    assert [path.name for path in files] == ["app.py"]


def test_analyze_source_kinds() -> None:
    source = (
        "from fake_lib import old_api\n"
        "import fake_lib as fl\n"
        'old_api(mode="fast")\n'
        "fl.old_api()\n"
        "fl.helper.attribute\n"
        "value = old_api\n"
    )
    usages = analyze_source(source, "src/app.py", {"fake_lib"})
    keys = {(usage.line, usage.symbol, usage.kind) for usage in usages}
    assert (1, "fake_lib.old_api", "import") in keys
    assert (2, "fake_lib", "import") in keys
    assert (3, "fake_lib.old_api", "call") in keys
    assert (3, "mode", "keyword_arg") in keys
    assert (4, "fake_lib.old_api", "call") in keys
    assert (5, "fake_lib.helper.attribute", "attribute") in keys
    assert (6, "fake_lib.old_api", "attribute") in keys


def test_scan_repository_collects_usages(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    result = scan_repository(repo, "Fake_Lib")
    assert result.declared is True
    assert result.dependency == "Fake_Lib"
    assert result.usages
    assert all(isinstance(usage, Usage) for usage in result.usages)
    assert all(usage.file == "src/app.py" for usage in result.usages)


def test_scan_without_dependency_reports_declarations(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    result = scan_repository(repo)
    assert result.declared is False
    assert result.usages == []
    assert result.declarations


def test_scan_undeclared_dependency(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    result = scan_repository(repo, "missing_package")
    assert result.declared is False
    assert result.usages == []
