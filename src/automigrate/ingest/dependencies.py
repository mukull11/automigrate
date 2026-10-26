"""Dependency declaration parsing for common Python project files."""

from __future__ import annotations

import configparser
import re
from pathlib import Path

from pydantic import BaseModel

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

REQUIREMENT_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(?P<spec>.*)$"
)
VERSION_RE = re.compile(r"\d+(?:\.\d+)*(?:[A-Za-z0-9.*+-]+)?")
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "build",
    "dist",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".tox",
    "htmlcov",
    "outputs",
    "workspace",
}


class DependencyDeclaration(BaseModel):
    name: str
    version: str | None = None
    source_file: str
    raw: str


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def import_module_roots(dependency: str) -> set[str]:
    base = dependency.strip().lower()
    roots = {base, base.replace("-", "_"), base.replace("_", "-")}
    return {root for root in roots if root}


def _version_from_spec(spec: str) -> str | None:
    match = VERSION_RE.search(spec)
    return match.group(0) if match else None


def parse_requirement(text: str, source_file: str) -> DependencyDeclaration | None:
    line = text.strip()
    if not line or line.startswith("#"):
        return None
    line = re.sub(r"\s+#.*$", "", line).strip()
    if not line or line.startswith(("-", "http:", "https:", "git+", "file:")):
        return None
    match = REQUIREMENT_RE.match(line)
    if not match:
        return None
    name = match.group("name")
    version = _version_from_spec(match.group("spec"))
    return DependencyDeclaration(
        name=name,
        version=version,
        source_file=source_file,
        raw=line,
    )


def parse_requirements_file(
    path: Path,
    root: Path,
    seen: set[Path] | None = None,
) -> list[DependencyDeclaration]:
    seen = seen or set()
    resolved = path.resolve()
    if resolved in seen or not path.is_file():
        return []
    seen.add(resolved)
    declarations: list[DependencyDeclaration] = []
    source = _relative_source(path, root)
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith(("-r ", "--requirement ")):
            nested = (path.parent / stripped.split(maxsplit=1)[1]).resolve()
            declarations.extend(parse_requirements_file(nested, root, seen))
            continue
        declaration = parse_requirement(line, source)
        if declaration is not None:
            declarations.append(declaration)
    return declarations


def _relative_source(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _parse_pyproject(path: Path, root: Path) -> list[DependencyDeclaration]:
    source = _relative_source(path, root)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    raw_specs: list[str] = []
    project = data.get("project", {})
    raw_specs.extend(project.get("dependencies", []))
    for specs in project.get("optional-dependencies", {}).values():
        raw_specs.extend(specs)

    declarations = [
        declaration
        for spec in raw_specs
        if (declaration := parse_requirement(spec, source)) is not None
    ]

    poetry = data.get("tool", {}).get("poetry", {})
    groups = [poetry.get("dependencies", {}), poetry.get("dev-dependencies", {})]
    for group in poetry.get("group", {}).values():
        groups.append(group.get("dependencies", {}))
    for group in groups:
        for name, value in group.items():
            if name.lower() == "python":
                continue
            version = None
            if isinstance(value, str):
                version = _version_from_spec(value)
            elif isinstance(value, dict):
                version = _version_from_spec(str(value.get("version", "")))
            declarations.append(
                DependencyDeclaration(
                    name=name,
                    version=version,
                    source_file=source,
                    raw=f"{name} {value}",
                )
            )
    return declarations


def _parse_setup_cfg(path: Path, root: Path) -> list[DependencyDeclaration]:
    source = _relative_source(path, root)
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    specs: list[str] = []
    if parser.has_option("options", "install_requires"):
        specs.extend(parser.get("options", "install_requires").splitlines())
    if parser.has_section("options.extras_require"):
        for value in parser["options.extras_require"].values():
            specs.extend(value.splitlines())
    return [
        declaration
        for spec in specs
        if (declaration := parse_requirement(spec, source)) is not None
    ]


def _parse_pipfile(path: Path, root: Path) -> list[DependencyDeclaration]:
    source = _relative_source(path, root)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    declarations: list[DependencyDeclaration] = []
    for section in ("packages", "dev-packages"):
        for name, value in data.get(section, {}).items():
            version = _version_from_spec(str(value)) if value != "*" else None
            declarations.append(
                DependencyDeclaration(
                    name=name,
                    version=version,
                    source_file=source,
                    raw=f"{name} {value}",
                )
            )
    return declarations


def detect_declarations(root: str | Path) -> list[DependencyDeclaration]:
    """Return every dependency declaration found in a repository root."""

    root_path = Path(root)
    declarations: list[DependencyDeclaration] = []

    requirement_files = sorted(root_path.glob("requirements*.txt"))
    requirements_dir = root_path / "requirements"
    if requirements_dir.is_dir():
        requirement_files.extend(sorted(requirements_dir.glob("*.txt")))
    for path in requirement_files:
        declarations.extend(parse_requirements_file(path, root_path))

    pyproject = root_path / "pyproject.toml"
    if pyproject.is_file():
        declarations.extend(_parse_pyproject(pyproject, root_path))

    setup_cfg = root_path / "setup.cfg"
    if setup_cfg.is_file():
        declarations.extend(_parse_setup_cfg(setup_cfg, root_path))

    pipfile = root_path / "Pipfile"
    if pipfile.is_file():
        declarations.extend(_parse_pipfile(pipfile, root_path))

    unique: dict[tuple[str, str, str | None], DependencyDeclaration] = {}
    for declaration in declarations:
        key = (
            normalize_name(declaration.name),
            declaration.source_file,
            declaration.version,
        )
        unique[key] = declaration
    return list(unique.values())


def find_declaration(
    declarations: list[DependencyDeclaration], dependency: str
) -> DependencyDeclaration | None:
    target = normalize_name(dependency)
    for declaration in declarations:
        if normalize_name(declaration.name) == target:
            return declaration
    return None
