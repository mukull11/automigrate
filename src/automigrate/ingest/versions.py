"""Resolve declared dependencies to their latest stable PyPI releases."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, Field

from automigrate.ingest.dependencies import (
    DependencyDeclaration,
    detect_declarations,
    normalize_name,
)
from automigrate.ingest.scanner import scan_repository

PYPI_JSON_URL = "https://pypi.org/pypi/{name}/json"
DEFAULT_TIMEOUT = 10
SKIPPED_NAMES = {"python"}

Fetcher = Callable[[str], str | None]
InstalledVersionResolver = Callable[[str], str | None]


class DependencyCandidate(BaseModel):
    """One declared dependency with its resolved latest stable release."""

    name: str
    current_version: str | None = None
    latest_version: str | None = None
    source_file: str = ""
    usage_count: int = 0
    sample_symbols: list[str] = Field(default_factory=list)
    status: Literal["outdated", "current", "unresolved"] = "unresolved"


def latest_stable_from_releases(releases: dict[str, Any]) -> str | None:
    """Pick the newest non-prerelease, non-yanked release from PyPI JSON."""

    stable: list[tuple[Version, str]] = []
    for raw_version, files in releases.items():
        if not isinstance(files, list) or not files:
            continue
        if all(
            isinstance(file, dict) and file.get("yanked")
            for file in files
        ):
            continue
        try:
            parsed = Version(raw_version)
        except InvalidVersion:
            continue
        if parsed.is_prerelease or parsed.is_devrelease:
            continue
        stable.append((parsed, raw_version))
    if not stable:
        return None
    return max(stable, key=lambda item: item[0])[1]


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


def fetch_latest_stable(
    name: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    opener: Callable[..., Any] | None = None,
) -> str | None:
    """Resolve the latest stable release for ``name`` from PyPI.

    Missing packages (``404``), private packages, network failures, and invalid
    metadata all return ``None`` so callers can skip them gracefully.
    """

    releases = fetch_pypi_releases(name, timeout=timeout, opener=opener)
    if releases is None:
        return None
    return latest_stable_from_releases(releases)


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


def build_candidates(
    root: str | Path,
    *,
    fetcher: Fetcher | None = None,
    installed: InstalledVersionResolver | None = None,
) -> list[DependencyCandidate]:
    """Build one candidate per declared dependency, resolving latest versions.

    Declarations come from the existing ``detect_declarations`` parser and
    usage counts from the existing ``scan_repository``.  The fetcher and
    installed-version resolver are injectable so tests stay offline.
    """

    root_path = Path(root)
    resolve_latest = fetcher or fetch_latest_stable
    resolve_installed = installed or installed_version
    grouped = _group_declarations(detect_declarations(root_path))

    candidates: list[DependencyCandidate] = []
    for _key, declarations in grouped.items():
        declaration = _chosen_declaration(declarations)
        current = declaration.version
        latest = resolve_latest(declaration.name)
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
            )
        )
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
