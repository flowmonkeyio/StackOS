"""Agent-facing compact response helpers."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


def compact_tracker_brief(value: Any) -> dict[str, Any]:
    data = _as_dict(value)
    return _drop_empty(
        {
            "ticket": compact_tracker_ticket(data.get("ticket")),
            "task": compact_tracker_task(data.get("task")),
            "dependencies": [
                compact_tracker_ticket(item)
                for item in _as_list(data.get("dependencies"))
                if isinstance(item, dict | BaseModel)
            ],
            "dependents": [
                compact_tracker_ticket(item)
                for item in _as_list(data.get("dependents"))
                if isinstance(item, dict | BaseModel)
            ],
            "references": [
                _compact_mapping(item, ("ref_type", "ref", "title"))
                for item in _as_list(data.get("references"))
                if isinstance(item, dict | BaseModel)
            ],
            "links": [
                _compact_mapping(
                    item,
                    (
                        "link_kind",
                        "ref",
                        "title",
                        "run_plan_id",
                        "run_plan_step_id",
                        "run_id",
                        "agent_request_id",
                    ),
                )
                for item in _as_list(data.get("links"))
                if isinstance(item, dict | BaseModel)
            ],
            "workflow_handoff": _compact_mapping(
                data.get("workflow_handoff"),
                (
                    "run_plan_id",
                    "run_plan_step_id",
                    "run_id",
                    "step_id",
                    "run_plan_key",
                    "template_key",
                    "next_operations",
                ),
            ),
            "suggested_next_actions": _as_list(data.get("suggested_next_actions")),
        }
    )


def compact_tracker_status(value: Any) -> dict[str, Any]:
    data = _as_dict(value)
    tracker = _as_dict(data.get("tracker"))
    return _drop_empty(
        {
            "project_id": _clean(data.get("project_id") or tracker.get("project_id")),
            "tracker_id": _clean(tracker.get("id")),
            "rev": _clean(data.get("rev") or tracker.get("rev")),
            "task_counts": _clean(data.get("task_counts")),
            "ticket_counts": _clean(data.get("ticket_counts")),
            "ready_ticket_count": _clean(data.get("ready_ticket_count")),
            "blocked_ticket_count": _clean(data.get("blocked_ticket_count")),
            "in_progress_ticket_count": _clean(data.get("in_progress_ticket_count")),
        }
    )


def compact_tracker_next(value: Any) -> dict[str, Any]:
    data = _as_dict(value)
    return _drop_empty(
        {
            "tickets": [
                compact_tracker_ticket(item)
                for item in _as_list(data.get("tickets"))
                if isinstance(item, dict | BaseModel)
            ],
            "blocked": [
                compact_tracker_ticket(item)
                for item in _as_list(data.get("blocked"))
                if isinstance(item, dict | BaseModel)
            ],
            "explanation": _clean(data.get("explanation")),
        }
    )


def compact_tracker_verify(value: Any) -> dict[str, Any]:
    data = _as_dict(value)
    return _drop_empty(
        {
            "ticket": compact_tracker_ticket(data.get("ticket")),
            "ready": _clean(data.get("ready")),
            "checks": [
                _compact_mapping(item, ("key", "passed", "detail"))
                for item in _as_list(data.get("checks"))
                if isinstance(item, dict | BaseModel)
            ],
            "suggested_next_actions": _as_list(data.get("suggested_next_actions")),
        }
    )


def compact_tracker_history_page(value: Any) -> dict[str, Any]:
    data = _as_dict(value)
    return _drop_empty(
        {
            "items": [
                compact_tracker_history_item(item)
                for item in _as_list(data.get("items"))
                if isinstance(item, dict | BaseModel)
            ],
            "next_cursor": _clean(data.get("next_cursor")),
            "total_estimate": _clean(data.get("total_estimate")),
        }
    )


def compact_tracker_changed(value: Any) -> dict[str, Any]:
    data = _as_dict(value)
    return _drop_empty(
        {
            "since_rev": _clean(data.get("since_rev")),
            "current_rev": _clean(data.get("current_rev")),
            "changes": [
                compact_tracker_history_item(item)
                for item in _as_list(data.get("changes"))
                if isinstance(item, dict | BaseModel)
            ],
        }
    )


def compact_tracker_search(value: Any) -> dict[str, Any]:
    data = _as_dict(value)
    return _drop_empty(
        {
            "tasks": [
                compact_tracker_task(item)
                for item in _as_list(data.get("tasks"))
                if isinstance(item, dict | BaseModel)
            ],
            "tickets": [
                compact_tracker_ticket(item)
                for item in _as_list(data.get("tickets"))
                if isinstance(item, dict | BaseModel)
            ],
        }
    )


def compact_tracker_history_item(value: Any) -> dict[str, Any]:
    return _compact_mapping(
        value,
        (
            "id",
            "rev",
            "actor",
            "change_kind",
            "entity_kind",
            "entity_key",
            "summary",
            "created_at",
        ),
    )


def compact_tracker_task(value: Any) -> dict[str, Any]:
    data = _as_dict(value)
    source = _as_dict(data.get("source_json"))
    result = _compact_mapping(
        data,
        (
            "key",
            "title",
            "goal",
            "description",
            "status",
            "priority_key",
            "lane_key",
            "owner",
            "task_type",
            "source_kind",
        ),
    )
    result.update(
        _drop_empty(
            {
                "template_key": _clean(source.get("template_key")),
                "run_plan_key": _clean(source.get("run_plan_key")),
                "run_plan_id": _clean(source.get("run_plan_id")),
            }
        )
    )
    return result


def compact_tracker_ticket(value: Any) -> dict[str, Any]:
    result = _compact_mapping(
        value,
        (
            "key",
            "title",
            "status",
            "task_key",
            "priority_key",
            "lane_key",
            "assignee",
            "blocked_by",
            "blocker_reason",
            "dependency_keys",
            "outcome",
            "run_plan_id",
            "run_plan_step_id",
            "run_id",
            "agent_request_id",
            "reference_count",
            "link_count",
        ),
    )
    for key in ("reference_count", "link_count"):
        if result.get(key) == 0:
            result.pop(key)
    return result


def _compact_mapping(value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    data = _as_dict(value)
    return _drop_empty({key: _clean(data.get(key)) for key in keys})


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return _drop_empty({str(key): _clean(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_clean(item) for item in value if not _is_empty(_clean(item))]
    return value


def _drop_empty(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if not _is_empty(value)}


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


__all__ = [
    "compact_tracker_brief",
    "compact_tracker_changed",
    "compact_tracker_history_item",
    "compact_tracker_history_page",
    "compact_tracker_next",
    "compact_tracker_search",
    "compact_tracker_status",
    "compact_tracker_task",
    "compact_tracker_ticket",
    "compact_tracker_verify",
]
