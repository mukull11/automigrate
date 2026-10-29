"""Resolve declared dependencies to their latest stable PyPI releases."""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, Field

from automigrate.ingest.dependencies import (
    DependencyDeclaration,
    detect_declarations,
    normalize_name,
)
from automigrate.ingest.scanner import scan_repository
from automigrate.state import Usage

PYPI_JSON_URL = "https://pypi.org/pypi/{name}/json"
DEFAULT_TIMEOUT = 10
SKIPPED_NAMES = {"python"}
INSTALLABLE_FILE_TYPES = {"bdist_wheel", "sdist"}
PYTHON_VERSION: str | None = None
_TARGET_PATTERN = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9_.-]+)\s*(?:==|=|:|@)\s*(?P<version>[^=\s]+)\s*$"
)

Fetcher = Callable[[str], str | None]
InstalledVersionResolver = Callable[[str], str | None]


class NoInstallableReleaseError(RuntimeError):
    """Raised when stable releases exist but none can run on this interpreter."""


@dataclass
class LatestRelease:
    """The newest installable release and the newer releases we skipped."""

    version: str | None = None
    skipped: list[str] = field(default_factory=list)
    reason: str | None = None


class DependencyCandidate(BaseModel):
    """One declared dependency with its resolved latest installable release."""

    name: str
    current_version: str | None = None
    latest_version: str | None = None
    source_file: str = ""
    usage_count: int = 0
    sample_symbols: list[str] = Field(default_factory=list)
    status: Literal["outdated", "current", "unresolved"] = "unresolved"
    skipped_releases: list[str] = Field(default_factory=list)
    breaking_usage_count: int | None = None
    breaking_symbols: list[str] = Field(default_factory=list)
    breaking_usages: list[Usage] | int | None = None
    breaking_usage: bool | None = None
    has_breaking_usage: bool | None = None
    has_breaking_usages: bool | None = None
    breaking_usage_found: bool | None = None
    confirmed_breaking_usage: bool | None = None

    def breaking_count(self) -> int | None:
        """Return the number of confirmed breaking usages, or ``None`` if unknown."""

        if self.breaking_usage_count is not None:
            return self.breaking_usage_count
        if isinstance(self.breaking_usages, int):
            return self.breaking_usages
        if self.breaking_usages is not None:
            return len(self.breaking_usages)
        for value in (
            self.breaking_usage,
            self.has_breaking_usage,
            self.has_breaking_usages,
            self.breaking_usage_found,
            self.confirmed_breaking_usage,
        ):
            if value is not None:
                return 1 if value else 0
        return None

    def breaking_known(self) -> bool:
        return self.breaking_count() is not None

    def has_confirmed_breaking_usage(self) -> bool:
        count = self.breaking_count()
        return count is not None and count > 0

    def is_migratable(self) -> bool:
        """Legacy candidates without a breaking-usage check keep old behaviour."""

        count = self.breaking_count()
        if count is None:
            return True
        return count > 0


def running_python_version() -> str:
    """Return the interpreter version used for Requires-Python checks."""

    info = sys.version_info
    major = getattr(info, "major", info[0])
    minor = getattr(info, "minor", info[1])
    micro = getattr(info, "micro", info[2] if len(info) > 2 else 0)
    return f"{major}.{minor}.{micro}"


def _resolved_python_version(python_version: str | Any | None) -> str:
    value = python_version or PYTHON_VERSION or running_python_version()
    if isinstance(value, (tuple, list)):
        return ".".join(str(part) for part in value)
    return str(value)


def _display_python_version(python_version: str) -> str:
    parts = python_version.split(".")
    return ".".join(parts[:2]) if len(parts) > 1 else python_version


def _stable_releases(releases: dict[str, Any]) -> list[tuple[Version, str, list[Any]]]:
    stable: list[tuple[Version, str, list[Any]]] = []
    for raw_version, files in releases.items():
        if not isinstance(files, list) or not files:
            continue
        try:
            parsed = Version(raw_version)
        except InvalidVersion:
            continue
        if parsed.is_prerelease or parsed.is_devrelease:
            continue
        stable.append((parsed, raw_version, files))
    return sorted(stable, key=lambda item: item[0], reverse=True)


def _installable_files(files: list[Any]) -> tuple[list[dict[str, Any]], str | None]:
    present = [file for file in files if isinstance(file, dict)]
    non_yanked = [file for file in present if not file.get("yanked")]
    if not non_yanked:
        return [], "all files are yanked"
    typed = any("packagetype" in file for file in non_yanked)
    if not typed:
        return non_yanked, None
    installable = [
        file
        for file in non_yanked
        if file.get("packagetype") in INSTALLABLE_FILE_TYPES
    ]
    if not installable:
        return [], "no wheel or sdist available"
    return installable, None


def _requires_python_reason(
    files: list[dict[str, Any]],
    python_version: str,
) -> str | None:
    interpreter = Version(python_version)
    first_requirement: str | None = None
    for file in files:
        requirement = file.get("requires_python")
        if not requirement:
            return None
        if not isinstance(requirement, str):
            continue
        if first_requirement is None:
            first_requirement = requirement
        try:
            specifier = SpecifierSet(requirement)
        except InvalidSpecifier:
            continue
        if interpreter in specifier:
            return None
    requirement = first_requirement or "an unknown version"
    return (
        f"requires Python {requirement}, running "
        f"{_display_python_version(python_version)}"
    )


def latest_installable_from_releases(
    releases: dict[str, Any],
    *,
    python_version: str | None = None,
) -> LatestRelease:
    """Pick the newest stable release this interpreter can install.

    A release is installable when it has at least one non-yanked wheel or sdist
    whose ``requires_python`` accepts the running interpreter. Newer stable
    releases that fail that check are returned in ``skipped`` with a reason.
    """

    interpreter = _resolved_python_version(python_version)
    stable = _stable_releases(releases)
    if not stable:
        return LatestRelease(reason="no stable releases published")

    skipped: list[str] = []
    for _parsed, raw_version, files in stable:
        installable, reason = _installable_files(files)
        if reason is not None:
            skipped.append(f"{raw_version} skipped: {reason}")
            continue
        reason = _requires_python_reason(installable, interpreter)
        if reason is not None:
            skipped.append(f"{raw_version} skipped: {reason}")
            continue
        return LatestRelease(version=raw_version, skipped=skipped)

    return LatestRelease(
        skipped=skipped,
        reason=(
            "no stable release is installable for Python "
            f"{_display_python_version(interpreter)}"
        ),
    )


def latest_stable_from_releases(
    releases: dict[str, Any],
    *,
    python_version: str | None = None,
) -> str | None:
    """Pick the newest stable release that the running interpreter can install."""

    return latest_installable_from_releases(
        releases, python_version=python_version
    ).version


def fetch_pypi_releases(
    name: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any] | None:
    """Return the PyPI ``releases`` mapping, or ``None`` when unavailable."""

    url = PYPI_JSON_URL.format(name=name)
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(url, timeout=timeout) as response:
            payload = json.load(response)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    releases = payload.get("releases") if isinstance(payload, dict) else None
    return releases if isinstance(releases, dict) else None


def fetch_latest_installable(
    name: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    opener: Callable[..., Any] | None = None,
    python_version: str | None = None,
) -> LatestRelease | None:
    """Resolve the latest installable release for ``name`` from PyPI.

    Missing packages (``404``), private packages, network failures, and invalid
    metadata all return ``None`` so callers can skip them gracefully.
    """

    releases = fetch_pypi_releases(name, timeout=timeout, opener=opener)
    if releases is None:
        return None
    return latest_installable_from_releases(
        releases, python_version=python_version
    )


def fetch_latest_stable(
    name: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    opener: Callable[..., Any] | None = None,
    python_version: str | None = None,
) -> str | None:
    """Resolve the latest installable stable release for ``name`` from PyPI."""

    release = fetch_latest_installable(
        name,
        timeout=timeout,
        opener=opener,
        python_version=python_version,
    )
    return release.version if release is not None else None


def installed_version(name: str) -> str | None:
    """Return the installed distribution version, or ``None`` if unavailable."""

    try:
        return metadata.version(name)
    except (metadata.PackageNotFoundError, ValueError):
        return None


def compare_status(
    current_version: str | None, latest_version: str | None
) -> Literal["outdated", "current", "unresolved"]:
    """Compare two versions with ``packaging.version.Version``."""

    if not current_version or not latest_version:
        return "unresolved"
    try:
        current = Version(current_version)
        latest = Version(latest_version)
    except InvalidVersion:
        return "unresolved"
    return "outdated" if current < latest else "current"


def _group_declarations(
    declarations: list[DependencyDeclaration],
) -> dict[str, list[DependencyDeclaration]]:
    grouped: dict[str, list[DependencyDeclaration]] = {}
    for declaration in declarations:
        key = normalize_name(declaration.name)
        if not key or key in SKIPPED_NAMES:
            continue
        grouped.setdefault(key, []).append(declaration)
    return grouped


def _chosen_declaration(
    declarations: list[DependencyDeclaration],
) -> DependencyDeclaration:
    for declaration in declarations:
        if declaration.version:
            return declaration
    return declarations[0]


def parse_target_override(target: str) -> tuple[str, str]:
    """Parse ``NAME==VERSION`` (also ``NAME@VERSION``) into its two parts.

    A bare version is accepted only when a single dependency is being
    considered. The empty name signals that case.
    """

    text = target.strip()
    match = _TARGET_PATTERN.match(text)
    if match:
        return match.group("name"), match.group("version")
    try:
        Version(text)
    except InvalidVersion as exc:
        raise ValueError(
            "--target must look like NAME==VERSION, for example numpy==2.4.6"
        ) from exc
    return "", text


def apply_target_override(
    candidates: list[DependencyCandidate],
    target: str | None,
) -> list[DependencyCandidate]:
    """Apply an explicit per-run target release, overriding the PyPI lookup."""

    if target is None or not target.strip():
        return candidates
    name, version = parse_target_override(target)
    normalized = normalize_name(name) if name else ""
    if not normalized:
        if len(candidates) != 1:
            raise ValueError(
                "--target must name the dependency when more than one is declared"
            )
        normalized = normalize_name(candidates[0].name)

    updated: list[DependencyCandidate] = []
    matched = False
    for candidate in candidates:
        if normalize_name(candidate.name) != normalized:
            updated.append(candidate)
            continue
        matched = True
        updated.append(
            candidate.model_copy(
                update={
                    "latest_version": version,
                    "status": compare_status(candidate.current_version, version),
                    "skipped_releases": [],
                }
            )
        )
    if not matched:
        raise ValueError(f"--target names an undeclared dependency: {name or version}")
    updated.sort(key=lambda item: normalize_name(item.name))
    return updated


def build_candidates(
    root: str | Path,
    *,
    fetcher: Fetcher | None = None,
    installed: InstalledVersionResolver | None = None,
    target: str | None = None,
    python_version: str | None = None,
) -> list[DependencyCandidate]:
    """Build one candidate per declared dependency, resolving latest versions.

    Declarations come from the existing ``detect_declarations`` parser and
    usage counts from the existing ``scan_repository``.  The fetcher and
    installed-version resolver are injectable so tests stay offline.
    """

    root_path = Path(root)
    resolve_installed = installed or installed_version
    grouped = _group_declarations(detect_declarations(root_path))

    candidates: list[DependencyCandidate] = []
    for _key, declarations in grouped.items():
        declaration = _chosen_declaration(declarations)
        current = declaration.version
        skipped_releases: list[str] = []
        if fetcher is not None:
            latest = fetcher(declaration.name)
        else:
            release = fetch_latest_installable(
                declaration.name, python_version=python_version
            )
            latest = release.version if release is not None else None
            if release is not None:
                skipped_releases = list(release.skipped)
                if release.version is None and release.reason and release.skipped:
                    raise NoInstallableReleaseError(
                        f"{declaration.name}: {release.reason}"
                    )
        if not current:
            current = resolve_installed(declaration.name)
        scan = scan_repository(root_path, declaration.name)
        symbols = sorted({usage.symbol for usage in scan.usages})
        candidates.append(
            DependencyCandidate(
                name=declaration.name,
                current_version=current,
                latest_version=latest,
                source_file=declaration.source_file,
                usage_count=len(scan.usages),
                sample_symbols=symbols[:8],
                status=compare_status(current, latest),
                skipped_releases=skipped_releases,
            )
        )
    candidates = apply_target_override(candidates, target)
    candidates.sort(key=lambda item: normalize_name(item.name))
    return candidates


def outdated_candidates(
    candidates: list[DependencyCandidate],
) -> list[DependencyCandidate]:
    """Return outdated candidates ordered by usage count, then name."""

    selected = [item for item in candidates if item.status == "outdated"]
    return sorted(
        selected,
        key=lambda item: (-item.usage_count, normalize_name(item.name)),
    )
