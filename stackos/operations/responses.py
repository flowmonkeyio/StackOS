"""Agent-facing operation response shaping."""

from __future__ import annotations

import copy
from typing import Any

from stackos.agent_responses import (
    compact_tracker_snapshot,
    compact_tracker_task,
    compact_tracker_ticket,
)
from stackos.operations.spec import OperationSpec, ResponseMode
from stackos.repositories.base import ValidationError

_MODE_ALIASES: dict[str, ResponseMode] = {
    "compact": "compact",
    "raw": "raw",
    "standard": "raw",
    "verbose": "raw",
    "ack": "ack",
}

_REF_FIELD_SUFFIXES = ("_id", "_ids", "_key", "_keys", "_ref", "_refs", "_token")
_SCALAR_KEEP_FIELDS = frozenset(
    {
        "id",
        "index",
        "key",
        "title",
        "name",
        "ok",
        "valid",
        "status",
        "state",
        "phase",
        "availability_status",
        "created",
        "updated",
        "deleted",
        "dry_run",
        "driver",
        "error",
        "count",
        "created_count",
        "updated_count",
        "deleted_count",
        "dependency_count",
        "warning_count",
        "error_count",
        "rev",
        "etag",
        "run_id",
        "run_plan_id",
        "step_id",
        "next_step_id",
        "template_key",
        "workflow_key",
        "provider_key",
        "action",
        "action_ref",
        "action_call_id",
        "message_ref",
        "thread_ref",
        "target_ref",
        "surface_ref",
        "actor_ref",
        "credential_ref",
        "record_id",
        "record_key",
        "resource_key",
        "plugin_slug",
        "public_base_url",
        "artifact_id",
        "artifact_ref",
        "learning_id",
        "category",
        "binding_was_created",
        "body_preview",
        "cron_expr",
        "enabled",
        "grant_policy",
        "kind",
        "monthly_budget_usd",
        "task_key",
        "ticket_key",
        "parent_ticket_key",
        "project_was_created",
        "read_only",
        "ready",
        "remaining_usd",
        "secret_policy",
        "slug",
        "source",
        "source_kind",
        "source_provider",
        "summary",
        "used_usd",
        "auto_bootstrap",
        "claimed_by",
        "daemon_reached",
        "framework",
        "git_remote_url",
        "last_known_root",
        "needs_connect",
        "normalized_repo_name",
        "position",
        "project_scoped_tools_usable",
        "purpose",
        "repo_fingerprint",
        "runtime",
        "thread_id",
        "workspace_binding_id",
    }
)
_LIST_KEEP_FIELDS = frozenset(
    {
        "allowed_tools",
        "action_refs_json",
        "attachment_refs",
        "changed_fields",
        "context_refs_json",
        "default_input_keys",
        "dependency_keys",
        "depends_on_json",
        "errors",
        "file_refs",
        "guidance",
        "input_refs_json",
        "message_refs",
        "missing",
        "output_refs_json",
        "policy_refs_json",
        "provider_results",
        "recommended_tools",
        "required_input_keys",
        "resource_refs_json",
        "selected_context_keys",
        "success_criteria_json",
        "template_override_keys",
        "ticket_keys",
        "warnings",
    }
)
_VERBATIM_KEEP_FIELDS = frozenset(
    {
        "binding",
        "candidate_projects",
        "content_model_json",
        "extension",
        "fields",
        "next_step",
        "project",
        "provenance",
        "setup_state",
        "ui_health",
        "ui_urls",
    }
)


def resolve_response_mode(
    spec: OperationSpec,
    arguments: dict[str, Any] | None,
    *,
    surface: str,
) -> ResponseMode:
    """Resolve and validate the requested operation response mode."""
    _ = surface  # reserved for future per-surface defaults; adapters pass it consistently.
    policy = spec.effective_response_policy
    explicit = isinstance(arguments, dict) and "response_mode" in arguments
    raw_value = arguments.get("response_mode") if explicit and isinstance(arguments, dict) else None
    mode: ResponseMode
    if raw_value is None:
        mode = policy.default_mode
    elif isinstance(raw_value, str):
        alias = _MODE_ALIASES.get(raw_value)
        if alias is None:
            raise ValidationError(
                "response_mode is not supported",
                data={
                    "operation": spec.name,
                    "response_mode": raw_value,
                    "allowed_modes": sorted(_MODE_ALIASES),
                },
            )
        mode = alias
    else:
        raise ValidationError(
            "response_mode must be a string",
            data={"operation": spec.name, "response_mode": raw_value},
        )
    if mode not in policy.allowed_modes:
        raise ValidationError(
            "response_mode is not allowed for this operation",
            data={
                "operation": spec.name,
                "response_mode": mode,
                "allowed_modes": list(policy.allowed_modes),
                "raw_only_reason": policy.raw_only_reason,
                "side_effect": "not_started",
            },
        )
    return mode


def shape_operation_response(
    spec: OperationSpec,
    payload: dict[str, Any],
    *,
    response_mode: ResponseMode,
    idempotency_replay: bool = False,
) -> dict[str, Any]:
    """Return the caller-facing response shape without mutating canonical payload."""
    if response_mode == "raw":
        return _with_replay(copy.deepcopy(payload), idempotency_replay)
    if response_mode == "ack":
        return _with_replay(_ack_payload(spec, payload), idempotency_replay)
    return _with_replay(_compact_payload(spec, payload), idempotency_replay)


def _with_replay(payload: dict[str, Any], replayed: bool) -> dict[str, Any]:
    if replayed:
        payload["idempotency_replay"] = True
    return payload


def _base_payload(spec: OperationSpec, payload: dict[str, Any]) -> dict[str, Any]:
    data = _data(payload)
    return {
        "ok": True,
        "operation": spec.name,
        "status": _status(payload, data),
        "project_id": payload.get("project_id") or data.get("project_id"),
        "run_id": payload.get("run_id") or data.get("run_id"),
    }


def _compact_payload(spec: OperationSpec, payload: dict[str, Any]) -> dict[str, Any]:
    data = _data(payload)
    if "data" not in payload and "items" in payload:
        return _compact_page(spec, payload)
    if "data" not in payload and spec.name == "tracker.get":
        compact_data = _compact_data(spec.name, payload)
        out = _base_payload(spec, payload)
        out["data"] = compact_data
        project_id = compact_data.get("project_id")
        if out.get("project_id") is None and isinstance(project_id, int):
            out["project_id"] = project_id
        rev = compact_data.get("rev")
        if isinstance(rev, int):
            out["rev"] = rev
        return out
    if "data" not in payload:
        return copy.deepcopy(payload)
    compact_data = _compact_data(spec.name, data)
    out = _base_payload(spec, payload)
    out["data"] = compact_data
    warnings = _warnings(data)
    if warnings:
        out["warnings"] = warnings
    rev = data.get("rev")
    if isinstance(rev, int):
        out["rev"] = rev
    return out


def _ack_payload(spec: OperationSpec, payload: dict[str, Any]) -> dict[str, Any]:
    data = _data(payload)
    out = _base_payload(spec, payload)
    ids_refs = _ids_and_refs(_compact_data(spec.name, data))
    if ids_refs:
        out["refs"] = ids_refs
    warnings = _warnings(data)
    if warnings:
        out["warnings"] = warnings
    return out


def _compact_page(spec: OperationSpec, payload: dict[str, Any]) -> dict[str, Any]:
    if spec.name == "operation.list":
        return _compact_operation_list(payload)
    items = payload.get("items")
    compact_items = (
        [_compact_data(spec.name, item) for item in items] if isinstance(items, list) else []
    )
    out: dict[str, Any] = {
        "ok": True,
        "operation": spec.name,
        "status": "success",
        "count": len(compact_items),
        "items": compact_items,
    }
    for key in ("next_cursor", "total_estimate", "project_id", "run_id"):
        if key in payload:
            out[key] = payload[key]
    return out


def _compact_operation_list(payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("items")
    item_list = items if isinstance(items, list) else []
    compact_items = [
        _compact_operation_summary(item)
        for item in item_list
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    ]
    out: dict[str, Any] = {
        "ok": True,
        "operation": "operation.list",
        "status": "success",
        "count": len(compact_items),
        "items": compact_items,
    }
    groups = payload.get("groups")
    if isinstance(groups, list):
        out["groups"] = [_compact_mapping(item) for item in groups if isinstance(item, dict)]
    return out


def _compact_operation_summary(item: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": item.get("name"),
        "category": item.get("category"),
        "summary": item.get("summary"),
        "read_only": item.get("read_only"),
        "mutating": item.get("mutating"),
        "grant_policy": item.get("grant_policy"),
        "secret_policy": item.get("secret_policy"),
    }
    surfaces = item.get("surfaces")
    if isinstance(surfaces, dict):
        out["surfaces"] = [
            name
            for name, surface in surfaces.items()
            if isinstance(name, str)
            and isinstance(surface, dict)
            and surface.get("enabled") is True
        ]
    policy = item.get("response_policy")
    if isinstance(policy, dict):
        compact_policy: dict[str, Any] = {
            "default_mode": policy.get("default_mode"),
            "allowed_modes": policy.get("allowed_modes"),
            "ack_safe": policy.get("ack_safe"),
        }
        if policy.get("raw_only_reason"):
            compact_policy["raw_only_reason"] = policy.get("raw_only_reason")
        out["response_policy"] = compact_policy
    return {key: value for key, value in out.items() if value is not None}


def _compact_data(operation_name: str, data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"value": data}
    if operation_name == "tracker.get":
        return compact_tracker_snapshot(data)
    if operation_name.startswith("tracker."):
        return _compact_tracker_mutation(data)
    if operation_name.startswith("runPlan."):
        return _compact_run_plan(data)
    if operation_name.startswith("workflowExtension."):
        return _compact_workflow_extension(data)
    return _compact_mapping(data)


def _compact_tracker_mutation(data: dict[str, Any]) -> dict[str, Any]:
    out = _compact_mapping(data)
    task = data.get("task")
    if isinstance(task, dict):
        out["task"] = compact_tracker_task(task)
        if task.get("key") is not None:
            out["task_key"] = task.get("key")
    ticket = data.get("ticket")
    if isinstance(ticket, dict):
        out["ticket"] = compact_tracker_ticket(ticket)
        if ticket.get("key") is not None:
            out["ticket_key"] = ticket.get("key")
    tickets = data.get("tickets")
    if isinstance(tickets, list):
        out["ticket_count"] = len([item for item in tickets if isinstance(item, dict)])
        out["ticket_keys"] = [
            item["key"]
            for item in tickets
            if isinstance(item, dict) and isinstance(item.get("key"), str)
        ]
    dependencies = data.get("dependencies")
    if isinstance(dependencies, list):
        out["dependency_count"] = len(dependencies)
    results = data.get("results")
    if isinstance(results, list):
        out["result_count"] = len(results)
        out["results"] = [_compact_mapping(item) for item in results if isinstance(item, dict)]
    tracker = data.get("tracker")
    if isinstance(tracker, dict):
        out["tracker"] = _compact_mapping(tracker)
    return out


def _compact_run_plan(data: dict[str, Any]) -> dict[str, Any]:
    out = _compact_mapping(data)
    plan = data.get("plan")
    if isinstance(plan, dict):
        out["plan"] = _compact_mapping(plan)
        out["run_plan_id"] = plan.get("id")
        out["run_plan_key"] = plan.get("key")
        steps = plan.get("steps")
        if isinstance(steps, list):
            running = [
                step for step in steps if isinstance(step, dict) and step.get("status") == "running"
            ]
            out["step_count"] = len(steps)
            if running:
                out["running_step_ids"] = [step.get("step_id") for step in running]
    run = data.get("run")
    if isinstance(run, dict):
        out["run"] = _compact_mapping(run)
    steps = data.get("steps")
    if isinstance(steps, list):
        out["step_count"] = len(steps)
        out["steps"] = [_compact_mapping(step) for step in steps if isinstance(step, dict)]
    approvals = data.get("approval_requests")
    if isinstance(approvals, list):
        out["approval_requests"] = [
            _compact_mapping(item) for item in approvals if isinstance(item, dict)
        ]
    return out


def _compact_workflow_extension(data: dict[str, Any]) -> dict[str, Any]:
    out = _compact_mapping(data)
    for source, target in (
        ("input_defaults_json", "default_input_keys"),
        ("selected_context_json", "selected_context_keys"),
        ("template_overrides_json", "template_override_keys"),
    ):
        value = data.get(source)
        if isinstance(value, dict):
            out[target] = sorted(str(key) for key in value)
    required = data.get("required_input_keys_json")
    if isinstance(required, list):
        out["required_input_keys"] = [str(item) for item in required if isinstance(item, str)]
    return out


def _compact_mapping(value: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, item in value.items():
        if key in _VERBATIM_KEEP_FIELDS:
            out[key] = copy.deepcopy(item)
            continue
        if key in _SCALAR_KEEP_FIELDS or key.endswith(_REF_FIELD_SUFFIXES):
            out[key] = _compact_value(item)
            continue
        if key in _LIST_KEEP_FIELDS:
            out[key] = _compact_value(item)
            continue
        if key in {"errors", "warnings"}:
            out[key] = item
    return out


def _compact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _compact_mapping(value)
    if isinstance(value, list):
        return [_compact_value(item) for item in value]
    return value


def _ids_and_refs(value: dict[str, Any]) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"warnings", "errors"}:
            continue
        if key in _SCALAR_KEEP_FIELDS or key.endswith(_REF_FIELD_SUFFIXES):
            refs[key] = item
    return refs


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _status(payload: dict[str, Any], data: dict[str, Any]) -> str:
    for candidate in (
        data.get("status"),
        payload.get("status"),
        data.get("state"),
        data.get("phase"),
    ):
        if candidate is not None:
            return str(candidate)
    if data.get("valid") is False:
        return "invalid"
    if data.get("dry_run") is True:
        return "validated"
    return "success"


def _warnings(data: dict[str, Any]) -> list[Any]:
    warnings = data.get("warnings")
    return list(warnings) if isinstance(warnings, list) else []


__all__ = [
    "resolve_response_mode",
    "shape_operation_response",
]
