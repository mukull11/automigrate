from automigrate.activity import (
    STAGE_LABELS,
    activity_markdown,
    advance_activity,
    finish_activity,
    new_activity,
)


def _run_to_plan(activity) -> None:
    advance_activity(activity, "INGEST", "2 Python files; lib is declared")
    advance_activity(activity, "DETECT", "found 2 usages of lib")
    advance_activity(activity, "RETRIEVE", "retrieved 1 evidence chunks")
    advance_activity(activity, "PLAN", "planning iteration 1")


def test_planning_failure_is_shown_and_later_stages_stay_pending() -> None:
    activity = new_activity()
    _run_to_plan(activity)

    advance_activity(
        activity,
        "REPORT",
        "final status REQUIRES_HUMAN_REVIEW: planner failed: invalid JSON",
    )
    finish_activity(activity, "REQUIRES_HUMAN_REVIEW")
    markdown = activity_markdown(activity, "REQUIRES_HUMAN_REVIEW")

    assert f"\u2713 {STAGE_LABELS[0]}" in markdown
    assert f"\u2717 {STAGE_LABELS[1]}" in markdown
    assert "planner failed: invalid JSON" in markdown
    for index in range(2, len(STAGE_LABELS)):
        assert f"\u2713 {STAGE_LABELS[index]}" not in markdown
        assert f"\u2717 {STAGE_LABELS[index]}" not in markdown


def test_successful_run_checks_every_stage() -> None:
    activity = new_activity()
    _run_to_plan(activity)
    advance_activity(activity, "PATCH", "applied 1 block")
    advance_activity(activity, "VALIDATE", "iteration 1: all tests pass")
    advance_activity(activity, "REPORT", "final status SUCCESSFUL")
    finish_activity(activity, "SUCCESSFUL")
    markdown = activity_markdown(activity, "SUCCESSFUL")

    for label in STAGE_LABELS:
        assert f"\u2713 {label}" in markdown
    assert "\u2717" not in markdown


def test_entering_a_later_stage_marks_only_earlier_stages_done() -> None:
    activity = new_activity()
    _run_to_plan(activity)

    assert activity["done"] == {0}
    assert activity["current"] == 1
    assert activity["error"] is None

    advance_activity(activity, "PATCH", "applied 1 block")

    assert activity["done"] == {0, 1}
    assert activity["current"] == 2
