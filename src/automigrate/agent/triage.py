"""LLM triage: choose which outdated dependencies to migrate first."""

from __future__ import annotations

from collections.abc import Sequence

from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, Field

from automigrate.agent.parsing import LLMParseError, parse_json_object
from automigrate.ingest.dependencies import normalize_name
from automigrate.ingest.versions import DependencyCandidate
from automigrate.llm.base import LLMAdapter

SYSTEM_PROMPT = (
    "You are a careful Python dependency-migration triage assistant. "
    "Choose only dependencies from the supplied candidate list. "
    "Prefer high-usage dependencies with clear version gaps. Return only valid JSON."
)


class TriageError(RuntimeError):
    pass


class TriageValidationError(TriageError):
    pass


class TriageDecision(BaseModel):
    dependency: str
    from_version: str
    to_version: str
    reason: str = ""


class TriageSkip(BaseModel):
    dependency: str
    reason: str


class TriageResult(BaseModel):
    migrations: list[TriageDecision] = Field(default_factory=list)
    skipped: list[TriageSkip] = Field(default_factory=list)
    summary: str = ""


def _candidate_block(candidates: Sequence[DependencyCandidate]) -> str:
    lines: list[str] = []
    for candidate in candidates:
        symbols = ", ".join(candidate.sample_symbols) or "none"
        breaking = candidate.breaking_count()
        breaking_text = (
            "not checked"
            if breaking is None
            else ("confirmed" if breaking > 0 else "none")
        )
        lines.append(
            "- dependency: {name}\n"
            "  current: {current}\n"
            "  latest: {latest}\n"
            "  declared_in: {source}\n"
            "  usage_count: {count}\n"
            "  breaking_usage: {breaking}\n"
            "  sample_symbols: {symbols}".format(
                name=candidate.name,
                current=candidate.current_version or "unknown",
                latest=candidate.latest_version or "unknown",
                source=candidate.source_file,
                count=candidate.usage_count,
                breaking=breaking_text,
                symbols=symbols,
            )
        )
    return "\n".join(lines)


def build_triage_prompt(
    candidates: Sequence[DependencyCandidate],
    *,
    previous_error: str | None = None,
) -> str:
    retry = ""
    if previous_error:
        retry = (
            "\nYour previous answer was rejected: "
            f"{previous_error}\nReturn a corrected JSON object only.\n"
        )
    return (
        "Choose the order in which outdated dependencies should be migrated now.\n"
        "Triage is prioritization, not veto: dependencies with a confirmed "
        "breaking usage (a removed, renamed, or changed-parameter name that the "
        "repository actually uses) must be migrated.\n"
        "A dependency marked as having no breaking usage must not be migrated.\n"
        "You may skip any dependency that has no confirmed breaking usage, and "
        "every skip must include a reason.\n"
        "Use only names and versions from the candidate list.\n"
        "Return JSON with this exact shape:\n"
        '{"migrations": [{"dependency": "name", "from_version": "1.0.0", '
        '"to_version": "2.0.0", "reason": "why"}], '
        '"skipped": [{"dependency": "name", "reason": "why"}], '
        '"summary": "one sentence"}\n'
        "If you recommend no migrations, return an empty migrations list.\n"
        f"{retry}\nCandidates:\n{_candidate_block(candidates)}\n"
    )


def _should_auto_select(candidate: DependencyCandidate) -> bool:
    """Auto-select only claims the repository can prove are affected.

    Candidates from older callers that predate the breaking-usage check keep
    the previous usage-count behaviour.
    """

    breaking = candidate.breaking_count()
    if breaking is not None:
        return breaking > 0
    return candidate.usage_count > 0


def _skip_reason(candidate: DependencyCandidate) -> str:
    breaking = candidate.breaking_count()
    if breaking == 0:
        return "outdated, no breaking usage found"
    return "zero usages; not selected by triage"


def _same_version(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return False
    if left.strip() == right.strip():
        return True
    try:
        return Version(left) == Version(right)
    except InvalidVersion:
        return False


def _parse_skips(
    data: dict,
    by_name: dict[str, DependencyCandidate],
    selected: set[str],
) -> list[TriageSkip]:
    raw_skips = data.get("skipped", [])
    if not isinstance(raw_skips, list):
        raise TriageValidationError("skipped must be a list")

    skipped: list[TriageSkip] = []
    seen: set[str] = set()
    for raw in raw_skips:
        if not isinstance(raw, dict):
            raise TriageValidationError("each skip must be an object")
        dependency = raw.get("dependency")
        reason = raw.get("reason")
        if not isinstance(dependency, str) or not dependency.strip():
            raise TriageValidationError("skip dependency must be a non-empty string")
        if not isinstance(reason, str) or not reason.strip():
            raise TriageValidationError(
                f"skip reason must be a non-empty string: {dependency}"
            )
        key = normalize_name(dependency)
        candidate = by_name.get(key)
        if candidate is None:
            raise TriageValidationError(f"unknown dependency: {dependency}")
        if candidate.usage_count > 0 and candidate.is_migratable():
            raise TriageValidationError(
                f"cannot skip dependency with confirmed usages: {candidate.name}"
            )
        if key in selected:
            raise TriageValidationError(
                f"dependency cannot be both selected and skipped: {candidate.name}"
            )
        if key in seen:
            raise TriageValidationError(f"duplicate skip: {dependency}")
        seen.add(key)
        skipped.append(TriageSkip(dependency=candidate.name, reason=reason.strip()))
    return skipped


def _validate(
    data: dict,
    candidates: Sequence[DependencyCandidate],
    *,
    max_dependencies: int,
) -> TriageResult:
    migrations = data.get("migrations")
    if not isinstance(migrations, list):
        raise TriageValidationError("migrations must be a list")

    by_name = {normalize_name(candidate.name): candidate for candidate in candidates}
    selected: list[TriageDecision] = []
    seen: set[str] = set()
    for raw in migrations:
        if not isinstance(raw, dict):
            raise TriageValidationError("each migration must be an object")
        dependency = raw.get("dependency")
        from_version = raw.get("from_version")
        to_version = raw.get("to_version")
        reason = raw.get("reason", "")
        if not isinstance(dependency, str) or not dependency.strip():
            raise TriageValidationError("dependency must be a non-empty string")
        key = normalize_name(dependency)
        candidate = by_name.get(key)
        if candidate is None:
            raise TriageValidationError(f"unknown dependency: {dependency}")
        if candidate.status != "outdated":
            raise TriageValidationError(
                f"dependency is not outdated: {candidate.name}"
            )
        if not candidate.is_migratable():
            raise TriageValidationError(
                f"outdated, no breaking usage found: {candidate.name}"
            )
        if not _same_version(str(from_version), candidate.current_version):
            raise TriageValidationError(
                f"wrong from_version for {candidate.name}: {from_version}"
            )
        if not _same_version(str(to_version), candidate.latest_version):
            raise TriageValidationError(
                f"wrong to_version for {candidate.name}: {to_version}"
            )
        if key in seen:
            raise TriageValidationError(f"duplicate dependency: {dependency}")
        seen.add(key)
        selected.append(
            TriageDecision(
                dependency=candidate.name,
                from_version=candidate.current_version or "",
                to_version=candidate.latest_version or "",
                reason=str(reason),
            )
        )

    skipped = _parse_skips(data, by_name, seen)
    if max_dependencies > 0:
        selected = selected[:max_dependencies]
    summary = data.get("summary", "")
    return TriageResult(
        migrations=selected,
        skipped=skipped,
        summary=str(summary),
    )


def _finalize_selection(
    result: TriageResult,
    candidates: Sequence[DependencyCandidate],
    *,
    max_dependencies: int,
) -> TriageResult:
    selected = list(result.migrations)
    selected_names = {normalize_name(item.dependency) for item in selected}
    remaining_used = sorted(
        (
            candidate
            for candidate in candidates
            if _should_auto_select(candidate)
            and normalize_name(candidate.name) not in selected_names
        ),
        key=lambda candidate: (-candidate.usage_count, normalize_name(candidate.name)),
    )
    for candidate in remaining_used:
        if max_dependencies > 0 and len(selected) >= max_dependencies:
            break
        selected.append(
            TriageDecision(
                dependency=candidate.name,
                from_version=candidate.current_version or "",
                to_version=candidate.latest_version or "",
                reason="confirmed usages always migrate",
            )
        )
        selected_names.add(normalize_name(candidate.name))

    skipped = list(result.skipped)
    skipped_names = {normalize_name(item.dependency) for item in skipped}
    for candidate in sorted(candidates, key=lambda item: normalize_name(item.name)):
        key = normalize_name(candidate.name)
        if key in selected_names or key in skipped_names:
            continue
        if candidate.is_migratable() and candidate.usage_count > 0:
            continue
        skipped.append(
            TriageSkip(
                dependency=candidate.name,
                reason=_skip_reason(candidate),
            )
        )
        skipped_names.add(key)

    return TriageResult(
        migrations=selected,
        skipped=skipped,
        summary=result.summary,
    )


def deterministic_triage(
    candidates: Sequence[DependencyCandidate],
    *,
    max_dependencies: int = 0,
    reason: str = "LLM triage unavailable",
) -> TriageResult:
    """Select outdated candidates by usage count, then name."""

    ordered = sorted(
        (candidate for candidate in candidates if candidate.status == "outdated"),
        key=lambda candidate: (-candidate.usage_count, normalize_name(candidate.name)),
    )
    migratable = [candidate for candidate in ordered if candidate.is_migratable()]
    skipped = [
        TriageSkip(dependency=candidate.name, reason=_skip_reason(candidate))
        for candidate in ordered
        if not candidate.is_migratable()
    ]
    if max_dependencies > 0:
        migratable = migratable[:max_dependencies]
    migrations = [
        TriageDecision(
            dependency=candidate.name,
            from_version=candidate.current_version or "",
            to_version=candidate.latest_version or "",
            reason=f"deterministic fallback: {reason}",
        )
        for candidate in migratable
    ]
    return TriageResult(
        migrations=migrations,
        skipped=skipped,
        summary=(
            f"{reason}. Deterministic fallback selected {len(migrations)} "
            "outdated dependency(ies) by usage count."
        ),
    )


def triage_candidates(
    llm: LLMAdapter | None,
    candidates: Sequence[DependencyCandidate],
    *,
    max_dependencies: int = 0,
) -> TriageResult:
    """Ask the LLM which candidates to migrate; retry once, then fall back."""

    outdated = [candidate for candidate in candidates if candidate.status == "outdated"]
    if not outdated:
        return TriageResult(migrations=[], summary="No outdated dependencies found.")
    if llm is None:
        return deterministic_triage(outdated, max_dependencies=max_dependencies)

    last_error = "invalid triage response"
    for attempt in range(2):
        prompt = build_triage_prompt(
            outdated,
            previous_error=last_error if attempt else None,
        )
        try:
            response = llm.complete(
                prompt, system=SYSTEM_PROMPT, stage="TRIAGE", iteration=0
            )
            data = parse_json_object(response)
            result = _validate(data, outdated, max_dependencies=max_dependencies)
            if not result.migrations:
                fallback = deterministic_triage(
                    outdated,
                    max_dependencies=max_dependencies,
                    reason="LLM recommended none",
                )
                fallback.summary = (
                    "LLM recommended none; deterministic fallback selected "
                    f"{len(fallback.migrations)}"
                )
                return fallback
            return _finalize_selection(
                result,
                outdated,
                max_dependencies=max_dependencies,
            )
        except (LLMParseError, TriageValidationError) as exc:
            last_error = str(exc)

    return deterministic_triage(
        outdated,
        max_dependencies=max_dependencies,
        reason=f"invalid LLM triage after retry: {last_error}",
    )
