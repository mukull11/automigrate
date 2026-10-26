"""Streamlit demo front end for AutoMigrate.

This module contains presentation only. Every migration is performed by
``automigrate.graph.run_migration``.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from automigrate.evaluation import discover_cases, load_case
from automigrate.graph import MigrationResult, run_migration
from automigrate.tools.git import GitRepo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "benchmarks" / "cases"


def _case_names() -> list[str]:
    try:
        return [case.name for case in discover_cases(DEFAULT_CASES)]
    except (FileNotFoundError, ValueError):
        return []


def _resolve_case_folder(raw: str) -> Path:
    path = Path(raw.strip())
    return path if path.is_absolute() else ROOT / path


def _diff_text(result: MigrationResult) -> str:
    repo = GitRepo(result.workspace_path)
    patches = []
    for sha in result.commits:
        patch = repo.diff_commit(sha)
        if patch.strip():
            patches.append(patch.rstrip())
    return "\n\n".join(patches)


def _show_result(result: MigrationResult) -> None:
    state = result.state
    st.subheader(f"Status: {state.status.value}")
    if state.flag_reason:
        st.warning(state.flag_reason)

    baseline_col, latest_col, iteration_col = st.columns(3)
    baseline_col.metric(
        "Baseline tests",
        f"{state.baseline.passed} passed",
        f"{state.baseline.failed + state.baseline.errors} failed",
        delta_color="off",
    )
    latest_col.metric(
        "Latest tests",
        f"{state.latest.passed} passed",
        f"{state.latest.failed + state.latest.errors} failed",
        delta_color="off",
    )
    iteration_col.metric("Iterations", state.iteration)

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
        if state.status.value != "SUCCESSFUL":
            st.caption(
                "The workspace was rolled back to the baseline; this is the "
                "unverified patch that was discarded."
            )
        st.code(diff, language="diff")


def main() -> None:
    st.set_page_config(page_title="AutoMigrate", layout="wide")
    st.title("AutoMigrate")
    st.caption("Test-guided dependency major-version migration")

    names = _case_names()
    selected = st.selectbox("Benchmark case", names or ["<none found>"])
    case_folder = DEFAULT_CASES / selected if names else DEFAULT_CASES / "01_rename"

    defaults = None
    if (case_folder / "case.yaml").is_file():
        try:
            defaults = load_case(case_folder)
        except ValueError as exc:
            st.error(str(exc))

    with st.form("migration"):
        raw_folder = st.text_input("Case folder", str(case_folder))
        dependency = st.text_input(
            "Dependency", defaults.dependency if defaults else "lib"
        )
        from_col, to_col = st.columns(2)
        from_version = from_col.text_input(
            "From version", defaults.from_version if defaults else "1.x"
        )
        to_version = to_col.text_input(
            "To version", defaults.to_version if defaults else "2.x"
        )
        llm = st.selectbox("LLM mode", ["mock", "anthropic", "openai"])
        submitted = st.form_submit_button("Run migration")

    if submitted:
        folder = _resolve_case_folder(raw_folder)
        repo = folder / "repo"
        docs = folder / "docs"
        if not repo.is_dir():
            st.error(f"Case repo not found: {repo}")
            return
        with st.spinner("Running migration..."):
            try:
                result = run_migration(
                    repo,
                    dependency,
                    from_version,
                    to_version,
                    docs_dir=docs if docs.is_dir() else None,
                    llm=llm,
                )
            except Exception as exc:  # noqa: BLE001 - show any failure in the UI
                st.exception(exc)
                return
        st.session_state["result"] = result

    result = st.session_state.get("result")
    if isinstance(result, MigrationResult):
        _show_result(result)


if __name__ == "__main__":
    main()
