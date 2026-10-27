"""Streamlit front end for AutoMigrate.

This module contains presentation only. Every migration is performed by
``automigrate.graph.run_migration``.
"""

from __future__ import annotations

import streamlit as st

from automigrate.apidiff.detector import ApiDiffError
from automigrate.graph import MigrationResult, run_migration
from automigrate.llm.chain import available_models
from automigrate.tools.git import GitRepo


def _diff_text(result: MigrationResult) -> str:
    repo = GitRepo(result.workspace_path)
    patches = []
    for sha in result.commits:
        patch = repo.diff_commit(sha)
        if patch.strip():
            patches.append(patch.rstrip())
    return "\n\n".join(patches)


def _show_detected(result: MigrationResult) -> None:
    diff = result.state.api_diff
    st.subheader("Detected API changes")
    if diff is None:
        st.info("No API diff was recorded.")
        return
    if not diff.has_changes and not diff.added:
        st.info("No public API changes were detected between the two versions.")
        return
    sections = [
        ("Removed", [f"`{item.qualified_name}` {item.signature}" for item in diff.removed]),
        (
            "Renamed",
            [
                f"`{change.old.qualified_name}` -> `{change.new.qualified_name}`"
                for change in diff.renamed
            ],
        ),
        (
            "Moved",
            [
                f"`{change.old.qualified_name}` -> `{change.new.qualified_name}`"
                for change in diff.moved
            ],
        ),
        (
            "Moved modules",
            [
                f"`{move.old_module}` -> `{move.new_module}`"
                for move in diff.moved_modules
            ],
        ),
        (
            "Changed parameters",
            [
                f"`{change.symbol.qualified_name}`: "
                f"{change.old_params} -> {change.new_params}"
                for change in diff.changed_parameters
            ],
        ),
        ("Added", [f"`{item.qualified_name}`" for item in diff.added]),
    ]
    for title, rows in sections:
        if rows:
            st.markdown(f"**{title}**")
            for row in rows:
                st.markdown(f"- {row}")


def _show_result(result: MigrationResult) -> None:
    state = result.state
    st.subheader(f"Status: {state.status.value}")
    if state.flag_reason:
        st.warning(state.flag_reason)

    baseline_col, latest_col, iteration_col = st.columns(3)
    baseline_col.metric(
        "Tests before",
        f"{state.baseline.passed} passed",
        f"{state.baseline.failed + state.baseline.errors} failed",
        delta_color="off",
    )
    latest_col.metric(
        "Tests after",
        f"{state.latest.passed} passed",
        f"{state.latest.failed + state.latest.errors} failed",
        delta_color="off",
    )
    iteration_col.metric("Iterations", state.iteration)

    _show_detected(result)

    if state.latest.failed_test_names:
        st.write("Unresolved tests")
        st.dataframe(
            {"test": state.latest.failed_test_names},
            use_container_width=True,
            hide_index=True,
        )

    st.write("Session")
    st.code(result.session_id, language=None)
    st.write("Workspace")
    st.code(str(result.workspace_path), language=None)

    diff = _diff_text(result)
    st.subheader("Diff")
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
    st.set_page_config(page_title="AutoMigrate", layout="wide")
    st.title("AutoMigrate")
    st.caption("Test-guided dependency major-version migration")

    models = available_models()
    with st.form("migration"):
        repository = st.text_input(
            "Repository", placeholder="C:\\path\\to\\repo or https://github.com/org/repo"
        )
        dependency = st.text_input("Dependency")
        current_version = st.text_input("Current version")
        target_version = st.text_input("Target version")
        model = st.selectbox("Model", models)
        submitted = st.form_submit_button("Run migration")

    if submitted:
        if not repository.strip():
            st.error("Enter a repository folder path or GitHub URL.")
            return
        if not dependency.strip() or not current_version.strip() or not target_version.strip():
            st.error("Dependency, current version, and target version are required.")
            return

        lines: list[str] = []
        stage_log = st.empty()
        stage_log.info("Starting migration...")

        def progress(stage: str, message: str) -> None:
            lines.append(f"**{stage}** {message}")
            stage_log.markdown("\n\n".join(lines))

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
            st.error(f"API diff failed: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 - show any failure in the UI
            st.exception(exc)
            return
        st.session_state["result"] = result

    result = st.session_state.get("result")
    if isinstance(result, MigrationResult):
        _show_result(result)


if __name__ == "__main__":
    main()
