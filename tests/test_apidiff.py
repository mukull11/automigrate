from pathlib import Path

from automigrate.apidiff.diff import diff_apis
from automigrate.apidiff.models import ApiDiff, ApiSymbol, ModuleMove, RenameChange
from automigrate.apidiff.symbols import extract_symbols
from automigrate.apidiff.usages import scan_api_usages
from automigrate.ingest.analyzer import analyze_source


def write_package(root: Path, files: dict[str, str]) -> None:
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def test_extract_symbols_keeps_public_api_only(tmp_path) -> None:
    pkg = tmp_path / "old"
    write_package(
        pkg,
        {
            "lib/__init__.py": (
                "def old_api(mode='fast'):\n"
                "    return mode\n"
                "\n"
                "def _private():\n"
                "    return 1\n"
                "\n"
                "class Client:\n"
                "    def get(self, name, timeout=1):\n"
                "        return name\n"
                "\n"
                "    def _hidden(self):\n"
                "        return 2\n"
            ),
            "lib/utils.py": "def helper(value):\n    return value\n",
        },
    )
    symbols = extract_symbols(pkg)
    names = {symbol.qualified_name for symbol in symbols}
    assert "lib.old_api" in names
    assert "lib.Client" in names
    assert "lib.Client.get" in names
    assert "lib.utils.helper" in names
    assert all("_private" not in name and "_hidden" not in name for name in names)


def test_diff_detects_removed_added_renamed_moved_and_changed_params() -> None:
    old_symbols = [
        ApiSymbol(module="lib", name="old_api", kind="function", params=["mode"]),
        ApiSymbol(module="lib", name="gone", kind="function", params=[]),
        ApiSymbol(module="lib.utils", name="helper", kind="function", params=["value"]),
        ApiSymbol(module="lib", name="configure", kind="function", params=["mode"]),
    ]
    new_symbols = [
        ApiSymbol(module="lib", name="new_api", kind="function", params=["mode"]),
        ApiSymbol(module="lib", name="brand_new", kind="function", params=[]),
        ApiSymbol(module="lib.core", name="helper", kind="function", params=["value"]),
        ApiSymbol(module="lib", name="configure", kind="function", params=["options"]),
    ]
    diff = diff_apis(old_symbols, new_symbols, dependency="lib")
    assert [item.name for item in diff.removed] == ["gone"]
    assert [item.name for item in diff.added] == ["brand_new"]
    assert [(c.old.name, c.new.name) for c in diff.renamed] == [
        ("old_api", "new_api")
    ]
    assert [(c.old.qualified_name, c.new.qualified_name) for c in diff.moved] == [
        ("lib.utils.helper", "lib.core.helper")
    ]
    assert diff.moved_modules[0].old_module == "lib.utils"
    assert diff.moved_modules[0].new_module == "lib.core"
    assert diff.changed_parameters[0].symbol.name == "configure"
    assert diff.changed_parameters[0].old_params == ["mode"]
    assert diff.changed_parameters[0].new_params == ["options"]


def test_diff_is_deterministic() -> None:
    old = [ApiSymbol(module="lib", name="a", kind="function", params=[])]
    new = [ApiSymbol(module="lib", name="b", kind="function", params=[])]
    assert diff_apis(old, new).model_dump() == diff_apis(old, new).model_dump()


def test_scan_api_usages_finds_imports_calls_and_attributes(tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text(
        "from lib import old_api\n"
        "import lib.utils as utils\n"
        "\n"
        "old_api(mode='fast')\n"
        "utils.helper(1)\n",
        encoding="utf-8",
    )
    diff = ApiDiff(
        dependency="lib",
        renamed=[
            RenameChange(
                old=ApiSymbol(
                    module="lib", name="old_api", kind="function", params=["mode"]
                ),
                new=ApiSymbol(
                    module="lib", name="new_api", kind="function", params=["mode"]
                ),
                reason="rename",
            )
        ],
    )
    usages = scan_api_usages(repo, diff)
    assert any(usage.kind == "import" and usage.symbol == "lib.old_api" for usage in usages)
    assert any(usage.kind == "call" and usage.line == 4 for usage in usages)

    moved = ApiDiff(
        dependency="lib",
        moved_modules=[
            ModuleMove(
                old_module="lib.utils",
                new_module="lib.core.helpers",
                reason="move",
            )
        ],
    )
    moved_usages = scan_api_usages(repo, moved)
    assert any("utils" in usage.symbol for usage in moved_usages)
    assert analyze_source("from lib import old_api\n", "a.py", {"lib"})
