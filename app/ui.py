"""Streamlit front end for AutoMigrate.

This module contains presentation only. Every migration is performed by
``automigrate.graph.run_migration``.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from automigrate.activity import STAGE_LABELS
from automigrate.activity import (
    activity_markdown as _activity_markdown,
)
from automigrate.activity import (
    advance_activity as _advance_activity,
)
from automigrate.activity import (
    fail_activity as _fail_activity,
)
from automigrate.activity import (
    finish_activity as _finish_activity,
)
from automigrate.activity import (
    new_activity as _new_activity,
)
from automigrate.apidiff.detector import ApiDiffError
from automigrate.auto import AutoMigrationResult, run_auto_migration
from automigrate.graph import MigrationResult
from automigrate.ingest.dependencies import normalize_name
from automigrate.llm.keys import (
    MissingKeyError,
    provider_key_from_env,
    resolve_api_key,
)
from automigrate.llm.models import list_models
from automigrate.llm.providers import Provider, all_providers, get_provider
from automigrate.tools.git import GitRepo

_API_KEY_STATE = "_automigrate_api_key"

__all__ = ["STAGE_LABELS", "main"]


def _render_activity(
    slot: Any,
    activity: dict[str, Any],
    final_status: str | None = None,
) -> None:
    slot.markdown(_activity_markdown(activity, final_status))


def _diff_text(result: MigrationResult) -> str:
    try:
        repo = GitRepo(result.workspace_path)
        patches = []
        for sha in result.commits:
            patch = repo.diff_commit(sha)
            if patch.strip():
                patches.append(patch.rstrip())
    except Exception:  # noqa: BLE001 - the diff is optional presentation
        return ""
    return "\n\n".join(patches)


def _show_detected(result: MigrationResult) -> None:
    state = result.state
    diff = state.api_diff
    st.markdown("#### What was detected")
    if diff is None:
        st.info("No API diff was recorded.")
        return
    if not diff.has_changes and not diff.added:
        st.info("No public API changes were detected between the two versions.")
        return

    target = state.to_version
    sections = [
        (
            "Removed",
            [
                f"`{item.qualified_name}` {item.signature} "
                f"\u2014 removed in {target}"
                for item in diff.removed
            ],
        ),
        (
            "Renamed",
            [
                f"`{change.old.qualified_name}` \u2192 "
                f"`{change.new.qualified_name}` \u2014 {change.reason}"
                for change in diff.renamed
            ],
        ),
        (
            "Moved",
            [
                f"`{change.old.qualified_name}` \u2192 "
                f"`{change.new.qualified_name}` \u2014 {change.reason}"
                for change in diff.moved
            ]
            + [
                f"`{move.old_module}` \u2192 `{move.new_module}` "
                f"\u2014 {move.reason}"
                for move in diff.moved_modules
            ],
        ),
        (
            "Changed symbols",
            [
                f"`{change.symbol.qualified_name}`: "
                f"{change.old_params} \u2192 {change.new_params} "
                f"\u2014 {change.reason}"
                for change in diff.changed_parameters
            ],
        ),
    ]
    shown = False
    for title, rows in sections:
        if rows:
            shown = True
            st.markdown(f"**{title}**")
            for row in rows:
                st.markdown(f"- {row}")
    if not shown:
        st.info("No breaking public API changes were detected.")


def _test_summary(label: str, result: Any) -> None:
    st.markdown(f"**{label}**")
    st.write(
        f"{result.passed} passed, {result.failed} failed, "
        f"{result.errors} errors, {result.ruff_issue_count} ruff issues"
    )


def _show_result(result: MigrationResult) -> None:
    state = result.state
    st.markdown("### Result")
    st.markdown(f"**Status:** `{state.status.value}`")
    if state.flag_reason:
        st.warning(state.flag_reason)

    _show_detected(result)

    st.markdown("#### Test results")
    before_col, after_col = st.columns(2)
    with before_col:
        _test_summary("Before", state.baseline)
    with after_col:
        _test_summary("After", state.latest)

    if state.latest.failed_test_names:
        st.markdown("**Unresolved tests**")
        st.dataframe(
            {"test": state.latest.failed_test_names},
            use_container_width=True,
            hide_index=True,
        )

    st.markdown("#### Diff")
    diff = _diff_text(result)
    if not diff:
        st.info("No source changes were produced.")
    else:
        if state.status.value in {"REQUIRES_HUMAN_REVIEW", "FAILED"}:
            st.caption(
                "The workspace was rolled back to the baseline; this is the "
                "unverified patch that was discarded."
            )
        st.code(diff, language="diff")


def _candidate_rows(result: AutoMigrationResult) -> list[dict[str, Any]]:
    chosen = {
        normalize_name(item.dependency) for item in result.selected
    }
    rows: list[dict[str, Any]] = []
    for candidate in result.candidates:
        key = normalize_name(candidate.name)
        if key in chosen:
            decision = "chosen"
        elif candidate.status == "outdated":
            decision = "skipped"
        else:
            decision = candidate.status
        rows.append(
            {
                "Dependency": candidate.name,
                "Current": candidate.current_version or "unknown",
                "Latest": candidate.latest_version or "unknown",
                "Usages": candidate.usage_count,
                "Source": candidate.source_file,
                "Decision": decision,
            }
        )
    return rows


def _show_auto_result(result: AutoMigrationResult) -> None:
    st.markdown("### Overall result")
    st.markdown(f"**Status:** `{result.status}`")
    st.write(result.summary)
    st.caption(f"Output folder: {result.outputs_dir}")

    st.markdown("#### Detected dependencies")
    rows = _candidate_rows(result)
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("No dependency declarations were detected.")

    st.markdown("#### LLM triage")
    if result.triage is None:
        st.info("No triage decision was needed.")
    else:
        st.write(result.triage.summary or "No summary was returned.")
        for decision in result.triage.migrations:
            st.markdown(
                f"- `{decision.dependency}` {decision.from_version} \u2192 "
                f"{decision.to_version} \u2014 {decision.reason}"
            )
        for skipped in result.triage.skipped:
            st.markdown(
                f"- skipped `{skipped.dependency}` \u2014 {skipped.reason}"
            )

    if not result.runs:
        return
    st.markdown("#### Per-dependency migrations")
    for run in result.runs:
        label = (
            f"{run.dependency}: {run.from_version} \u2192 {run.to_version} "
            f"({run.status.value})"
        )
        with st.expander(label, expanded=len(result.runs) == 1):
            if run.error:
                st.error(run.error)
            elif run.result is not None:
                _show_result(run.result)


def _clear_api_key() -> None:
    st.session_state.pop(_API_KEY_STATE, None)


def _provider_label(provider: Provider) -> str:
    return f"{provider.display_name} ({provider.id})"


def _model_cache_key(provider_id: str) -> str:
    return f"_automigrate_models_{provider_id}"


def _load_models(provider: Provider, key: str | None, *, refresh: bool = False):
    cache_key = _model_cache_key(provider.id)
    if refresh:
        st.session_state.pop(cache_key, None)
    cached = st.session_state.get(cache_key)
    if cached is None:
        listing = list_models(
            provider,
            key or provider_key_from_env(provider),
            base_url=provider.base_url,
        )
        st.session_state[cache_key] = listing
        cached = listing
    return cached


_CUSTOM_MODEL = "__automigrate_custom_model__"


def main() -> None:
    st.set_page_config(page_title="AUTO-MIGRATE", layout="wide")
    st.title("AUTO-MIGRATE")
    st.markdown("### Agentic AI Dependency Migration")

    providers = all_providers()
    provider_ids = [provider.id for provider in providers]

    repository = st.text_input(
        "Repository",
        placeholder="C:\\path\\to\\repo or https://github.com/org/repo",
    )
    allow_undocumented = st.checkbox(
        "Allow undocumented replacements",
        help=(
            "Accept confident plans even when the documentation does not name "
            "a replacement. Defaults to off."
        ),
    )
    max_dependencies = st.number_input(
        "Max dependencies",
        min_value=0,
        value=0,
        step=1,
        help="0 attempts every outdated dependency. A positive value caps the run.",
    )
    provider_id = st.selectbox(
        "Provider",
        provider_ids,
        format_func=lambda value: _provider_label(get_provider(value)),
    )
    provider = get_provider(provider_id)

    listing = _load_models(provider, provider_key_from_env(provider))

    model_options = [*listing.models, _CUSTOM_MODEL]
    model = st.selectbox(
        "Model",
        model_options,
        format_func=lambda value: (
            "Type a model ID" if value == _CUSTOM_MODEL else value
        ),
    )
    if model == _CUSTOM_MODEL:
        model = st.text_input("Model ID")
    if listing.error:
        st.caption(f"Using fallback model list: {listing.error}")
    custom_base_url = None
    if provider.id == "custom":
        custom_base_url = st.text_input(
            "Base URL",
            placeholder="https://your-endpoint.example/v1",
        )

    api_key_field = st.text_input(
        "API key",
        type="password",
        key=_API_KEY_STATE,
    )
    effective_key = (api_key_field or "").strip() or provider_key_from_env(provider)
    if not (api_key_field or "").strip() and provider_key_from_env(provider):
        st.caption(f"Using {provider.key_env} from environment")

    st.button("Clear key", on_click=_clear_api_key)
    if st.button("Refresh model list"):
        _load_models(provider, effective_key, refresh=True)
        rerun = getattr(st, "rerun", None)
        if callable(rerun):
            rerun()
    submitted = st.button("START MIGRATION")

    stored_result = st.session_state.get("auto_result")
    activity = st.session_state.get("activity") or _new_activity()
    final_status = (
        stored_result.status
        if isinstance(stored_result, AutoMigrationResult)
        else None
    )
    activity_slot = st.empty()
    _render_activity(activity_slot, activity, final_status)

    if submitted:
        if not repository.strip():
            st.error("Enter a repository folder path or GitHub URL.")
            return
        if not (model or "").strip():
            st.error("Choose a model or type a model ID.")
            return
        try:
            resolved_key = resolve_api_key(provider, api_key_field)
        except MissingKeyError as exc:
            st.error(str(exc))
            return

        activity = _new_activity()
        st.session_state["activity"] = activity
        st.session_state.pop("auto_result", None)

        def progress(stage: str, message: str) -> None:
            _advance_activity(activity, stage, message)
            _render_activity(activity_slot, activity)

        try:
            with st.spinner("Discovering and migrating dependencies..."):
                result = run_auto_migration(
                    repository.strip(),
                    model=model.strip(),
                    provider=provider.id,
                    base_url=(custom_base_url or "").strip() or None,
                    api_key=resolved_key,
                    allow_undocumented=allow_undocumented,
                    max_dependencies=int(max_dependencies),
                    progress_callback=progress,
                )
        except ApiDiffError as exc:
            _fail_activity(activity, f"API diff failed: {exc}")
            _render_activity(activity_slot, activity)
            st.error(f"API diff failed: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 - show any failure in the UI
            _fail_activity(activity, f"Migration failed: {exc}")
            _render_activity(activity_slot, activity)
            st.error(f"Migration failed: {exc}")
            return

        activity_status = "SUCCESSFUL" if result.succeeded else result.status
        _finish_activity(activity, activity_status)
        st.session_state["activity"] = activity
        st.session_state["auto_result"] = result
        _render_activity(activity_slot, activity, activity_status)
        stored_result = result

    if isinstance(stored_result, AutoMigrationResult):
        _show_auto_result(stored_result)


if __name__ == "__main__":
    main()
