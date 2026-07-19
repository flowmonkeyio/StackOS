"""Pure normalization and validation for raw tracker ticket-list specs."""

from __future__ import annotations

from typing import Any

from stackos.db.models import (
    TrackerItemStatus,
    TrackerSourceKind,
    TrackerTicketKind,
)
from stackos.repositories.tracker.schema import TrackerListIssueOut
from stackos.repositories.tracker.utils import _clean_text, _slug


def normalize_ticket_key(
    raw: Any,
    *,
    index: int,
    seen_keys: set[str],
) -> tuple[dict[str, Any] | None, str | None, list[TrackerListIssueOut]]:
    """Validate one raw list entry and return its normalized key."""

    if not isinstance(raw, dict):
        return (
            None,
            None,
            [
                TrackerListIssueOut(
                    index=index,
                    field="tickets",
                    message="ticket must be an object",
                )
            ],
        )
    key = _slug(str(raw.get("key") or ""), fallback="", max_length=180)
    if not key:
        return (
            raw,
            None,
            [
                TrackerListIssueOut(
                    index=index,
                    field="key",
                    message="ticket key is required",
                )
            ],
        )
    issues: list[TrackerListIssueOut] = []
    if key in seen_keys:
        issues.append(
            TrackerListIssueOut(
                index=index,
                key=key,
                field="key",
                message="duplicate ticket key",
            )
        )
    return raw, key, issues


def normalize_ticket_spec(
    raw: dict[str, Any],
    *,
    index: int,
    key: str,
    list_created_by: Any,
) -> tuple[dict[str, Any], list[TrackerListIssueOut], list[str]]:
    """Normalize one keyed raw ticket spec and return ordered issues/warnings."""

    issues: list[TrackerListIssueOut] = []
    warnings: list[str] = []
    title = _clean_text(raw.get("title")) if raw.get("title") is not None else ""
    if not title:
        title = key
        warnings.append(f"ticket {key} has no title; key will be used as title")
    status = _list_enum(
        raw.get("status", TrackerItemStatus.NOT_STARTED.value),
        TrackerItemStatus,
        issues,
        index=index,
        key=key,
        field="status",
    )
    kind = _list_enum(
        raw.get("kind", TrackerTicketKind.TICKET.value),
        TrackerTicketKind,
        issues,
        index=index,
        key=key,
        field="kind",
    )
    source_kind = _list_enum(
        raw.get("source_kind", TrackerSourceKind.MANUAL.value),
        TrackerSourceKind,
        issues,
        index=index,
        key=key,
        field="source_kind",
    )
    dependency_keys = _json_string_list(
        raw.get("dependency_keys", []),
        issues,
        index=index,
        key=key,
        field="dependency_keys",
    )
    references_json = raw.get("references_json", [])
    if not isinstance(references_json, list):
        issues.append(
            TrackerListIssueOut(
                index=index,
                key=key,
                field="references_json",
                message="references_json must be a list",
            )
        )
        references_json = []
    elif any(not isinstance(item, dict) for item in references_json):
        issues.append(
            TrackerListIssueOut(
                index=index,
                key=key,
                field="references_json",
                message="references_json entries must be objects",
            )
        )
        references_json = []
    completion_evidence_json = raw.get("completion_evidence_json")
    if completion_evidence_json is not None and not isinstance(completion_evidence_json, dict):
        issues.append(
            TrackerListIssueOut(
                index=index,
                key=key,
                field="completion_evidence_json",
                message="completion_evidence_json must be an object",
            )
        )
        completion_evidence_json = None
    spec = {
        "index": index,
        "key": key,
        "title": title,
        "goal": _clean_text(raw.get("goal")),
        "status": status or TrackerItemStatus.NOT_STARTED,
        "kind": kind or TrackerTicketKind.TICKET,
        "assignee": raw.get("assignee"),
        "priority_key": str(raw.get("priority_key") or "p2"),
        "lane_key": str(raw.get("lane_key") or "implementation"),
        "parent_ticket_key": str(raw.get("parent_ticket_key") or "").strip() or None,
        "dependency_keys": dependency_keys,
        "blocker_reason": raw.get("blocker_reason"),
        "outcome": raw.get("outcome"),
        "effort": raw.get("effort"),
        "source_kind": source_kind or TrackerSourceKind.MANUAL,
        "source_json": raw.get("source_json") if isinstance(raw.get("source_json"), dict) else None,
        "definition_of_done_json": _json_string_list(
            raw.get("definition_of_done_json", []),
            issues,
            index=index,
            key=key,
            field="definition_of_done_json",
        ),
        "constraints_json": _json_string_list(
            raw.get("constraints_json", []),
            issues,
            index=index,
            key=key,
            field="constraints_json",
        ),
        "expected_changes_json": _json_string_list(
            raw.get("expected_changes_json", []),
            issues,
            index=index,
            key=key,
            field="expected_changes_json",
        ),
        "allowed_paths_json": _json_string_list(
            raw.get("allowed_paths_json", []),
            issues,
            index=index,
            key=key,
            field="allowed_paths_json",
        ),
        "references_json": references_json,
        "completion_evidence_json": completion_evidence_json,
        "context_json": raw.get("context_json")
        if isinstance(raw.get("context_json"), dict)
        else None,
        "metadata_json": raw.get("metadata_json")
        if isinstance(raw.get("metadata_json"), dict)
        else None,
        "run_plan_id": raw.get("run_plan_id") if isinstance(raw.get("run_plan_id"), int) else None,
        "run_plan_step_id": raw.get("run_plan_step_id")
        if isinstance(raw.get("run_plan_step_id"), int)
        else None,
        "created_by": raw.get("created_by") or list_created_by,
    }
    return spec, issues, warnings


def normalize_internal_dependency_declarations(
    raw_dependencies: Any,
    *,
    list_ticket_keys: set[str],
) -> tuple[list[tuple[str, str]], list[TrackerListIssueOut]]:
    """Normalize top-level dependency declarations between list tickets."""

    if raw_dependencies is None:
        return [], []
    if not isinstance(raw_dependencies, list):
        return (
            [],
            [TrackerListIssueOut(field="dependencies", message="dependencies must be a list")],
        )
    declarations: list[tuple[str, str]] = []
    issues: list[TrackerListIssueOut] = []
    for index, raw_dependency in enumerate(raw_dependencies):
        if not isinstance(raw_dependency, dict):
            issues.append(
                TrackerListIssueOut(
                    index=index,
                    field="dependencies",
                    message="dependency must be an object",
                )
            )
            continue
        ticket_key = _slug(
            str(raw_dependency.get("ticket_key") or ""),
            fallback="",
            max_length=180,
        )
        dependency_key = _slug(
            str(
                raw_dependency.get("depends_on_ticket_key")
                or raw_dependency.get("dependency_key")
                or ""
            ),
            fallback="",
            max_length=180,
        )
        if not ticket_key or not dependency_key:
            issues.append(
                TrackerListIssueOut(
                    index=index,
                    field="dependencies",
                    message="dependency requires ticket_key and depends_on_ticket_key",
                )
            )
            continue
        if ticket_key not in list_ticket_keys:
            issues.append(
                TrackerListIssueOut(
                    index=index,
                    key=ticket_key,
                    field="dependencies",
                    message="dependency ticket_key must refer to a list ticket",
                )
            )
            continue
        declarations.append((ticket_key, dependency_key))
    return declarations, issues


def self_dependency_issue(spec: dict[str, Any]) -> TrackerListIssueOut | None:
    """Return the structured self-dependency issue for one normalized spec."""

    if spec["key"] not in spec["dependency_keys"]:
        return None
    return TrackerListIssueOut(
        index=spec["index"],
        key=spec["key"],
        field="dependency_keys",
        message="ticket cannot depend on itself",
    )


def _list_enum(
    value: Any,
    enum_cls: Any,
    issues: list[TrackerListIssueOut],
    *,
    index: int,
    key: str,
    field: str,
) -> Any:
    try:
        return enum_cls(str(value))
    except ValueError:
        issues.append(
            TrackerListIssueOut(
                index=index,
                key=key,
                field=field,
                message=f"unsupported {field}: {value}",
            )
        )
        return None


def _json_string_list(
    value: Any,
    issues: list[TrackerListIssueOut],
    *,
    index: int,
    key: str,
    field: str,
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        issues.append(
            TrackerListIssueOut(
                index=index,
                key=key,
                field=field,
                message=f"{field} must be a list",
            )
        )
        return []
    return [str(item) for item in value]


__all__ = [
    "normalize_internal_dependency_declarations",
    "normalize_ticket_key",
    "normalize_ticket_spec",
    "self_dependency_issue",
]
