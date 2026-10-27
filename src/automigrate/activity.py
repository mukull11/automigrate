"""Progress tracking for the agent activity panel."""

from __future__ import annotations

from typing import Any

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

_TERMINAL_STAGES = {"REPORT", "DONE", "FLAGGED"}
_COMPLETED_STATUSES = {"SUCCESSFUL", "UNVERIFIED"}


def new_activity() -> dict[str, Any]:
    return {"done": set(), "current": None, "error": None}


def _mark_complete(activity: dict[str, Any]) -> None:
    activity["done"] = set(range(len(STAGE_LABELS)))
    activity["current"] = len(STAGE_LABELS) - 1
    activity["error"] = None


def _report_reason(message: str) -> str:
    _, _, reason = message.partition(":")
    return reason.strip() or message


def advance_activity(
    activity: dict[str, Any], stage: str, message: str
) -> None:
    if message.startswith("baseline:") or message == "cloning repository":
        return
    name = stage.upper()
    index = _STAGE_INDEX.get(name)
    if index is None:
        return
    if name in _TERMINAL_STAGES:
        status = message.removeprefix("final status ").split(":", 1)[0].strip()
        if status in _COMPLETED_STATUSES:
            _mark_complete(activity)
            return
        failing = activity["current"] if activity["current"] is not None else index
        activity["done"] = {item for item in activity["done"] if item < failing}
        activity["done"].update(range(failing))
        activity["current"] = failing
        activity["error"] = {"stage": failing, "message": _report_reason(message)}
        return
    activity["done"] = {item for item in activity["done"] if item < index}
    activity["done"].update(range(index))
    activity["current"] = index
    activity["error"] = None


def fail_activity(activity: dict[str, Any], message: str) -> None:
    failing = activity["current"] if activity["current"] is not None else 0
    activity["done"] = {item for item in activity["done"] if item < failing}
    activity["done"].update(range(failing))
    activity["current"] = failing
    activity["error"] = {"stage": failing, "message": message}


def finish_activity(
    activity: dict[str, Any], final_status: str | None = None
) -> None:
    if final_status in _COMPLETED_STATUSES:
        _mark_complete(activity)
    elif activity["error"] is None:
        fail_activity(activity, final_status or "migration did not complete")


def activity_markdown(
    activity: dict[str, Any], final_status: str | None = None
) -> str:
    lines = ["#### Agent Activity", ""]
    error = activity["error"] or {}
    error_stage = error.get("stage")
    for index, label in enumerate(STAGE_LABELS):
        if index == error_stage:
            mark = "\u2717"
        elif index in activity["done"]:
            mark = "\u2713"
        elif index == activity["current"]:
            mark = "\u2192"
        else:
            mark = "\u25cb"
        lines.append(f"{mark} {label}")
        if index == error_stage and error.get("message"):
            lines.append(f"    {error['message']}")
    if final_status is not None:
        lines.extend(["", f"**Final status: {final_status}**"])
    return "\n\n".join(lines)
