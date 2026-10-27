"""Streamlit front end for AutoMigrate.

This module contains presentation only. Every migration is performed by
``automigrate.graph.run_migration``.
"""

from __future__ import annotations

import os
from typing import Any

import streamlit as st

from automigrate.apidiff.detector import ApiDiffError
from automigrate.graph import MigrationResult, run_migration
from automigrate.llm.chain import available_models
from automigrate.tools.git import GitRepo

STAGE_LABELS = (
    "Dependency usages detected",
    "Migration plan generated",
    "Minimal patch applied",
    "Running tests",
    "Diagnosing failures",
    "Repairing or safely stopping",
)

_STAGE_INDEX = {
    "INGEST": 0,
    "DETECT": 0,
    "RETRIEVE": 1,
    "PLAN": 1,
    "PATCH": 2,
    "VALIDATE": 3,
    "DIAGNOSE": 4,
    "REPORT": 5,
    "DONE": 5,
    "FLAGGED": 5,
}


def _new_activity() -> dict[str, Any]:
    return {"done": set(), "current": None}


def _advance_activity(
    activity: dict[str, Any], stage: str, message: str
) -> None:
    if message.startswith("baseline:") or message == "cloning repository":
        return
    index = _STAGE_INDEX.get(stage.upper())
    if index is None:
        return
    activity["done"] = {item for item in activity["done"] if item < index}
    activity["done"].update(range(index))
    activity["current"] = index


def _finish_activity(activity: dict[str, Any]) -> None:
    activity["done"] = set(range(len(STAGE_LABELS)))
    activity["current"] = len(STAGE_LABELS) - 1


def _activity_markdown(
    activity: dict[str, Any], final_status: str | None = None
) -> str:
    lines = ["#### Agent Activity", ""]
    for index, label in enumerate(STAGE_LABELS):
        if final_status is not None or index in activity["done"]:
            mark = "\u2713"
        elif index == activity["current"]:
            mark = "\u2192"
        else:
            mark = "\u25cb"
        lines.append(f"{mark} {label}")
    if final_status is not None:
        lines.extend(["", f"**Final status: {final_status}**"])
    return "\n\n".join(lines)


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


def main() -> None:
    st.set_page_config(page_title="AUTO-MIGRATE", layout="wide")
    st.title("AUTO-MIGRATE")
    st.markdown("### Agentic AI Dependency Migration")

    models = available_models()
    with st.form("migration"):
        repository = st.text_input(
            "Repository",
            placeholder="C:\\path\\to\\repo or https://github.com/org/repo",
        )
        dependency = st.text_input("Dependency")
        current_version = st.text_input("Current Version")
        target_version = st.text_input("Target Version")
        model = st.selectbox("Model", models)
        submitted = st.form_submit_button("START MIGRATION")

    stored_result = st.session_state.get("migration_result")
    activity = st.session_state.get("activity") or _new_activity()
    final_status = (
        stored_result.state.status.value
        if isinstance(stored_result, MigrationResult)
        else None
    )
    activity_slot = st.empty()
    _render_activity(activity_slot, activity, final_status)

    if submitted:
        if not repository.strip():
            st.error("Enter a repository folder path or GitHub URL.")
            return
        if (
            not dependency.strip()
            or not current_version.strip()
            or not target_version.strip()
        ):
            st.error(
                "Dependency, Current Version, and Target Version are required."
            )
            return
        if not os.environ.get("OPENAI_API_KEY"):
            st.error(
                "OPENAI_API_KEY is not set. Set it in the environment before "
                "starting a migration."
            )
            return

        activity = _new_activity()
        st.session_state["activity"] = activity
        st.session_state.pop("migration_result", None)

        def progress(stage: str, message: str) -> None:
            _advance_activity(activity, stage, message)
            _render_activity(activity_slot, activity)

        try:
            with st.spinner("Running migration..."):
                result = run_migration(
                    repository.strip(),
                    dependency.strip(),
                    current_version.strip(),
                    target_version.strip(),
                    model=model,
                    progress_callback=progress,
                )
        except ApiDiffError as exc:
            _render_activity(activity_slot, activity)
            st.error(f"API diff failed: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 - show any failure in the UI
            _render_activity(activity_slot, activity)
            st.error(f"Migration failed: {exc}")
            return

        _finish_activity(activity)
        st.session_state["activity"] = activity
        st.session_state["migration_result"] = result
        _render_activity(activity_slot, activity, result.state.status.value)
        stored_result = result

    if isinstance(stored_result, MigrationResult):
        _show_result(stored_result)


if __name__ == "__main__":
    main()
