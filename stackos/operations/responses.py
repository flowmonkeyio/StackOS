"""Agent-facing operation response shaping."""

from __future__ import annotations

import copy
from typing import Any, cast

from stackos.agent_responses import (
    compact_tracker_brief,
    compact_tracker_changed,
    compact_tracker_history_page,
    compact_tracker_next,
    compact_tracker_search,
    compact_tracker_snapshot,
    compact_tracker_status,
    compact_tracker_task,
    compact_tracker_ticket,
    compact_tracker_verify,
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

_MAX_COMPACT_LIST_ITEMS = 25
_MAX_COMPACT_STRING_CHARS = 800
_MAX_SCHEMA_DESCRIPTION_CHARS = 240
_TERMINAL_TRACKER_STATUSES = {"complete", "deferred", "aborted", "failed", "skipped"}
_CONTEXT_PAGE_OPERATIONS = {
    "context.query",
    "context.timeline",
    "learning.query",
    "experiment.query",
    "decision.query",
}

_REF_FIELD_SUFFIXES = ("_id", "_ids", "_key", "_keys", "_ref", "_refs", "_token")
_URL_FIELD_SUFFIXES = ("_url", "_urls")
_SCALAR_KEEP_FIELDS = frozenset(
    {
        "id",
        "index",
        "key",
        "title",
        "name",
        "description",
        "domain",
        "version",
        "schema_version",
        "schema_ref",
        "schema_operation",
        "tool",
        "reason",
        "role",
        "role_class",
        "requirement",
        "label",
        "type",
        "required",
        "optional",
        "required_when",
        "recommended_tool",
        "approver",
        "agent_type",
        "skill_type",
        "generic_preset",
        "adaptation_required",
        "do_not_use_verbatim",
        "instruction",
        "required_agent_action",
        "ok",
        "valid",
        "status",
        "state",
        "phase",
        "availability_status",
        "created",
        "updated",
        "update_mode",
        "deleted",
        "connected",
        "dry_run",
        "driver",
        "error",
        "count",
        "bytes",
        "sha256",
        "path",
        "uri",
        "json_path",
        "max_bytes",
        "content_available",
        "content_truncated",
        "content_type",
        "mime_type",
        "size_bytes",
        "hidden_count",
        "created_count",
        "updated_count",
        "deleted_count",
        "dependency_count",
        "warning_count",
        "error_count",
        "connected_count",
        "issue_count",
        "ready_count",
        "exposed_action_count",
        "executable_action_count",
        "hidden_action_count",
        "total_action_count",
        "required_action_count",
        "optional_action_count",
        "credential_count",
        "connected_credential_count",
        "rev",
        "etag",
        "run_id",
        "run_plan_id",
        "step_id",
        "next_step_id",
        "template_key",
        "workflow_key",
        "provider_key",
        "project_name",
        "project_slug",
        "action",
        "action_ref",
        "action_call_id",
        "message_ref",
        "message",
        "mission",
        "read_instructions",
        "operation",
        "thread_ref",
        "target_ref",
        "surface_ref",
        "actor_ref",
        "credential_ref",
        "record_id",
        "record_key",
        "resource_key",
        "resource",
        "capability",
        "auth_ref",
        "approval_ref",
        "plugin_slug",
        "public_base_url",
        "artifact_id",
        "artifact_ref",
        "learning_id",
        "category",
        "code",
        "binding_was_created",
        "binding_id",
        "binding_kind",
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
        "structurally_ready",
        "context_status",
        "required_providers_ready",
        "execution_ready",
        "route_group",
        "prerequisite_count",
        "mutating",
        "executable",
        "visible_by_default",
        "hidden_reason",
        "external_provider",
        "requires_integration",
        "requires_credential",
        "allows_credential",
        "exposure_state",
        "risk_level",
        "auth_type",
        "auth_method_key",
        "credential_state",
        "budget_state",
        "budget_kind",
        "remaining_usd",
        "secret_policy",
        "severity",
        "slug",
        "source",
        "source_kind",
        "source_provider",
        "setup",
        "summary",
        "used_usd",
        "auto_bootstrap",
        "claimed_by",
        "daemon_reached",
        "check_source",
        "framework",
        "git_remote_url",
        "last_known_root",
        "needs_connect",
        "normalized_repo_name",
        "position",
        "project_scoped_tools_usable",
        "project_identity_required",
        "project_identity_guidance",
        "project_extension_enabled",
        "profile_complete",
        "purpose",
        "repairable",
        "repo_fingerprint",
        "runtime",
        "thread_id",
        "workspace_binding_id",
        "workspace_alias",
        "workspace_bound",
        "call_via",
        "base_url",
        "display_name",
        "provider_account_id",
        "profile_key",
        "setup_required",
        "connector_registered",
        "execution_available",
        "payload_format",
        "interactive",
        "input_context_truncated",
        "content_omitted",
    }
)
_LIST_KEEP_FIELDS = frozenset(
    {
        "allowed_tools",
        "action_refs_json",
        "actions",
        "action_contracts",
        "agent_requirements",
        "applies_to_steps",
        "applies_to_workflows",
        "approval_gates",
        "approval_refs",
        "approval_refs_json",
        "attachment_refs",
        "auth_methods",
        "auth_requirements",
        "capability_requirements",
        "changed_fields",
        "cleared_fields",
        "commands",
        "consistency_issues",
        "connections",
        "context_requirements",
        "context_refs",
        "context_refs_json",
        "default_input_keys",
        "dependency_keys",
        "depends_on",
        "depends_on_json",
        "errors",
        "examples",
        "file_refs",
        "fields",
        "guidance",
        "handoff_inputs",
        "handoff_outputs",
        "handoff_notes",
        "input_refs",
        "input_refs_json",
        "inputs",
        "instructions_json",
        "learning_hooks",
        "message_refs",
        "mention_patterns",
        "missing",
        "must_do",
        "must_not_do",
        "issues",
        "next_operations",
        "optional_agents",
        "optional_skill_presets",
        "output_refs",
        "output_refs_json",
        "outputs",
        "policies",
        "providers",
        "prerequisites",
        "profile_missing",
        "preserved_fields",
        "project_name",
        "project_slug",
        "prompt_assembly_order",
        "policy_refs_json",
        "provider_results",
        "recommended_agents",
        "recommended_skill_presets",
        "recommended_tools",
        "required_agents",
        "required_context_refs",
        "required_input_keys",
        "required_outputs",
        "required_skill_presets",
        "resource_refs",
        "resource_refs_json",
        "resource_contracts",
        "responsibilities",
        "returns",
        "route_groups",
        "routes",
        "safe_stopping_points",
        "scopes",
        "selected_context_keys",
        "self_check",
        "setup_guidance",
        "skill_requirements",
        "skill_preset_requirements",
        "steps",
        "success_criteria",
        "success_criteria_json",
        "template_override_keys",
        "ticket_keys",
        "unresolved_requirements",
        "unresolved_skill_preset_requirements",
        "warnings",
        "when_not_to_use",
        "when_to_use",
        "workflow_roles",
    }
)
_MAPPING_KEEP_FIELDS = frozenset(
    {
        "access",
        "access_policy",
        "account",
        "arguments",
        "artifact",
        "availability",
        "credential",
        "execution_context",
        "handoff_policy",
        "identity",
        "manifest",
        "next_action",
        "next_step",
        "operating_contract",
        "preset",
        "project_adaptation",
        "prompt_contract",
        "provider",
        "provider_setup",
        "project_extension",
        "recommended_arguments",
        "response_policy",
        "send_policy",
        "setup_state",
        "spec",
        "surfaces",
        "template",
        "tool_profile",
        "trigger",
        "trigger_policy",
        "ui_health",
    }
)
_SCHEMA_FIELD_NAMES = frozenset(
    {
        "input_schema",
        "input_schema_json",
        "output_schema",
        "output_schema_json",
        "schema",
        "schema_json",
    }
)
_CONTENT_OMIT_FIELDS = frozenset({"content"})
_VERBATIM_KEEP_FIELDS = frozenset(
    {
        "action_execution_guidance",
        "binding",
        "candidate_workspaces",
        "candidate_projects",
        "content_model_json",
        "direct_dependency_handoffs",
        "experience",
        "extension",
        "expected_outputs_json",
        "input_values_json",
        "project",
        "public",
        "step_context_json",
        "exposure",
        "provenance",
        "setup",
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
    policy = spec.effective_response_policy
    explicit = isinstance(arguments, dict) and "response_mode" in arguments
    raw_value = arguments.get("response_mode") if explicit and isinstance(arguments, dict) else None
    mode: ResponseMode
    if raw_value is None:
        mode = _default_response_mode_for_surface(
            spec.name,
            policy.default_mode,
            policy.allowed_modes,
            surface,
        )
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


def _default_response_mode_for_surface(
    operation_name: str,
    policy_default: ResponseMode,
    allowed_modes: tuple[ResponseMode, ...],
    surface: str,
) -> ResponseMode:
    if (
        surface == "cli"
        and operation_name in {"action.run", "action.execute"}
        and "raw" in allowed_modes
    ):
        return "raw"
    return policy_default


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
        if spec.name == "tracker.history":
            out = _compact_page(spec, payload)
            out["items"] = compact_tracker_history_page(payload).get("items", [])
            return out
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
        compact_data = _compact_data(spec.name, payload)
        out = _base_payload(spec, payload)
        out["data"] = compact_data
        project_id = compact_data.get("project_id")
        if out.get("project_id") is None and isinstance(project_id, int):
            out["project_id"] = project_id
        warnings = _warnings(payload)
        if warnings:
            out["warnings"] = warnings
        rev = compact_data.get("rev")
        if isinstance(rev, int):
            out["rev"] = rev
        return out
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
    for key in (
        "context_refs",
        "filters_json",
        "hidden_count",
        "connected_count",
        "ready_count",
        "exposed_action_count",
        "executable_action_count",
        "hidden_action_count",
        "filters",
        "next_calls",
    ):
        if key in payload:
            out[key] = (
                copy.deepcopy(payload[key]) if key == "next_calls" else _compact_value(payload[key])
            )
    return out


def _compact_context_item(item: dict[str, Any]) -> dict[str, Any]:
    out = _compact_mapping(item)
    if "occurred_at" in item:
        out["occurred_at"] = _compact_text(item["occurred_at"])
    fields = item.get("fields")
    if isinstance(fields, dict):
        out["fields"] = {str(key): _compact_context_field(value) for key, value in fields.items()}
    return out


def _compact_context_field(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _compact_context_field(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_compact_context_field(item) for item in value[:_MAX_COMPACT_LIST_ITEMS]]
    return _compact_text(value)


def _compact_resource_record(record: dict[str, Any]) -> dict[str, Any]:
    out = _compact_mapping(record)
    data_json = record.get("data_json")
    if isinstance(data_json, (dict, list)):
        out["data_json"] = _compact_context_field(data_json)
    for key in ("created_at", "updated_at"):
        if key in record:
            out[key] = _compact_text(record[key])
    return out


def _compact_resource_get(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    resource = data.get("resource")
    record = data.get("record")
    if isinstance(resource, dict):
        out["resource"] = _compact_mapping(resource)
    if isinstance(record, dict):
        out["record"] = _compact_resource_record(record)
    return out


def _compact_resource_query(data: dict[str, Any]) -> dict[str, Any]:
    raw_resources = data.get("resources")
    resources = raw_resources if isinstance(raw_resources, list) else []
    raw_records = data.get("records")
    records = raw_records if isinstance(raw_records, list) else []
    compact_resources = [
        _compact_mapping(item)
        for item in resources[:_MAX_COMPACT_LIST_ITEMS]
        if isinstance(item, dict)
    ]
    compact_records = [
        _compact_resource_record(item)
        for item in records[:_MAX_COMPACT_LIST_ITEMS]
        if isinstance(item, dict)
    ]
    out: dict[str, Any] = {
        "resources": compact_resources,
        "resources_count": len(resources),
        "records": compact_records,
        "records_count": len(records),
        "total_estimate": data.get("total_estimate", len(records)),
        "next_cursor": data.get("next_cursor"),
    }
    if len(resources) > _MAX_COMPACT_LIST_ITEMS:
        out["resources_truncated"] = True
    if len(records) > _MAX_COMPACT_LIST_ITEMS:
        out["records_truncated"] = True
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
        out["groups"] = [
            _compact_operation_group(item) for item in groups if isinstance(item, dict)
        ]
    return out


def _compact_operation_group(value: dict[str, Any]) -> dict[str, Any]:
    names = value.get("operation_names")
    return {
        "category": _compact_value(value.get("category")),
        "count": _compact_value(value.get("count")),
        "operation_names": [name for name in names if isinstance(name, str)]
        if isinstance(names, list)
        else [],
    }


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
    if operation_name in {"action.run", "action.execute"}:
        return _compact_action_execution(data)
    if operation_name == "resource.get":
        return _compact_resource_get(data)
    if operation_name == "resource.query":
        return _compact_resource_query(data)
    if operation_name == "tracker.get":
        return compact_tracker_snapshot(data)
    if operation_name == "tracker.status":
        return compact_tracker_status(data)
    if operation_name in {"tracker.next", "tracker.blockers"}:
        return compact_tracker_next(data)
    if operation_name in {"tracker.brief", "tracker.why"}:
        return compact_tracker_brief(data)
    if operation_name == "tracker.verify":
        return compact_tracker_verify(data)
    if operation_name == "tracker.history":
        return compact_tracker_history_page(data)
    if operation_name == "tracker.changed":
        return compact_tracker_changed(data)
    if operation_name == "tracker.search":
        return compact_tracker_search(data)
    if operation_name.startswith("tracker."):
        return _compact_tracker_mutation(data)
    if operation_name.startswith("runPlan."):
        return _compact_run_plan(data)
    if operation_name == "workflowTemplate.list":
        return _compact_workflow_template_list(data)
    if operation_name == "workflowTemplate.describe":
        return _compact_workflow_template_describe(data)
    if operation_name == "workflowTemplate.authoringGuide":
        return _compact_workflow_authoring_guide(data)
    if operation_name == "agentPreset.resolveForWorkflow":
        return _compact_agent_preset_resolution(data)
    if operation_name == "skillPreset.resolveForWorkflow":
        return _compact_skill_preset_resolution(data)
    if operation_name == "agentPreset.list":
        return _compact_agent_preset_list(data)
    if operation_name == "workflowExtension.list":
        return _compact_workflow_extension_list(data)
    if operation_name.startswith("workflowExtension."):
        return _compact_workflow_extension(data)
    if operation_name in _CONTEXT_PAGE_OPERATIONS:
        return _compact_context_item(data)
    return _compact_mapping(data)


def _compact_action_execution(data: dict[str, Any]) -> dict[str, Any]:
    call_value = data.get("action_call")
    call = cast(dict[str, Any], call_value) if isinstance(call_value, dict) else {}
    compact_value = data.get("compact")
    compact_hint = cast(dict[str, Any], compact_value) if isinstance(compact_value, dict) else {}
    output_value = data.get("output_json")
    output_json = cast(dict[str, Any], output_value) if isinstance(output_value, dict) else {}
    file_value = output_json.get("file")
    file_pointer = cast(dict[str, Any], file_value) if isinstance(file_value, dict) else {}
    summary_value = output_json.get("summary")
    summary = cast(dict[str, Any], summary_value) if isinstance(summary_value, dict) else {}

    action_ref = data.get("action_ref")
    if action_ref is None and call:
        plugin_slug = call.get("plugin_slug")
        action_key = call.get("action_key")
        if isinstance(plugin_slug, str) and isinstance(action_key, str):
            action_ref = f"{plugin_slug}.{action_key}"

    out: dict[str, Any] = {
        "status": data.get("status") or call.get("status"),
        "action_ref": action_ref,
        "action_call_id": data.get("action_call_id") or call.get("id"),
        "provider_key": data.get("provider_key") or call.get("provider_key"),
        "operation": data.get("operation") or call.get("operation"),
        "credential_ref": data.get("credential_ref") or call.get("credential_ref"),
        "cost_cents": data.get("cost_cents"),
        "dry_run": data.get("dry_run"),
        "replayed": data.get("replayed"),
    }
    if file_pointer:
        out["output"] = {
            key: file_pointer.get(key)
            for key in (
                "output_mode",
                "path",
                "content_type",
                "schema_version",
                "schema_ref",
                "schema_operation",
                "semantic_name",
                "bytes",
                "sha256",
            )
            if file_pointer.get(key) is not None
        }
        out["output"]["output_mode"] = "file"
    elif compact_hint:
        out["output"] = copy.deepcopy(compact_hint)
    else:
        out["output"] = _compact_mapping(output_json) if output_json else {}
    if summary:
        out["summary"] = _compact_mapping(summary)
    return {key: value for key, value in out.items() if value is not None}


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
    task_rollup = _compact_tracker_task_rollup(task, ticket)
    if task_rollup:
        out["task_rollup"] = task_rollup
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


def _compact_tracker_task_rollup(task: Any, ticket: Any) -> dict[str, Any]:
    if not isinstance(task, dict):
        return {}
    status = _compact_value(task.get("status"))
    evidence_present = bool(task.get("completion_evidence_json"))
    out: dict[str, Any] = {
        "task_key": _compact_value(task.get("key")),
        "status": status,
        "lane_key": _compact_value(task.get("lane_key")),
        "completion_evidence_present": evidence_present,
    }
    if isinstance(ticket, dict):
        out["updated_by_ticket_key"] = _compact_value(ticket.get("key"))
    if isinstance(status, str) and status in _TERMINAL_TRACKER_STATUSES and not evidence_present:
        out["completion_evidence_needs_explicit_update"] = True
        out["note"] = (
            "Ticket terminal updates can roll up the parent task status; ticket evidence is "
            "not copied to the task."
        )
    return {
        key: value
        for key, value in out.items()
        if value is not None and value != "" and value != [] and value != {}
    }


def _compact_run_plan_step(step: dict[str, Any]) -> dict[str, Any]:
    keys: tuple[str, ...] = (
        "id",
        "step_id",
        "title",
        "status",
        "position",
        "claimed_by",
        "run_id",
        "claimed_at",
        "started_at",
        "completed_at",
        "depends_on",
        "depends_on_json",
        "context_refs",
        "context_refs_json",
        "resource_refs",
        "resource_refs_json",
        "action_refs",
        "action_refs_json",
        "output_refs",
        "output_refs_json",
        "allowed_tools",
    )
    if step.get("status") == "running":
        keys += (
            "purpose",
            "input_refs_json",
            "policy_refs_json",
            "approval_refs_json",
            "instructions_json",
            "success_criteria_json",
            "expected_outputs_json",
            "direct_dependency_handoffs",
            "action_execution_guidance",
        )
    return {key: _compact_value(step[key]) for key in keys if step.get(key) is not None}


def _compact_run_plan_steps(steps: list[Any]) -> list[dict[str, Any]]:
    return [_compact_run_plan_step(step) for step in steps if isinstance(step, dict)]


def _compact_run_plan(data: dict[str, Any]) -> dict[str, Any]:
    out = _compact_mapping(data)
    if data.get("id") is not None and data.get("key") is not None:
        out["run_plan_id"] = data.get("id")
        out["run_plan_key"] = data.get("key")
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
            out["plan"]["step_count"] = len(steps)
            out["plan"]["steps"] = _compact_run_plan_steps(steps)
            if running:
                out["running_step_ids"] = [step.get("step_id") for step in running]
    run = data.get("run")
    if isinstance(run, dict):
        out["run"] = _compact_mapping(run)
    steps = data.get("steps")
    if isinstance(steps, list):
        out["step_count"] = len(steps)
        out["steps"] = _compact_run_plan_steps(steps)
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


def _compact_workflow_extension_list(data: dict[str, Any]) -> dict[str, Any]:
    extensions = data.get("extensions")
    extension_items = extensions if isinstance(extensions, list) else []
    out = _compact_mapping(data)
    out["extensions"] = [
        _compact_workflow_extension(item) if isinstance(item, dict) else _compact_value(item)
        for item in extension_items[:_MAX_COMPACT_LIST_ITEMS]
    ]
    out["extensions_count"] = len(extension_items)
    if len(extension_items) > _MAX_COMPACT_LIST_ITEMS:
        out["extensions_truncated"] = True
    return out


def _compact_workflow_template_list(data: dict[str, Any]) -> dict[str, Any]:
    templates = data.get("templates")
    template_items = templates if isinstance(templates, list) else []
    return {
        "templates": [
            _compact_mapping(item) if isinstance(item, dict) else _compact_value(item)
            for item in template_items[:_MAX_COMPACT_LIST_ITEMS]
        ],
        "templates_count": len(template_items),
        "templates_truncated": len(template_items) > _MAX_COMPACT_LIST_ITEMS,
        "include_shadowed": bool(data.get("include_shadowed")),
    }


def _compact_workflow_template_describe(data: dict[str, Any]) -> dict[str, Any]:
    out = _compact_mapping(data)
    spec = data.get("spec")
    if isinstance(spec, dict):
        compact_spec = out.get("spec")
        if isinstance(compact_spec, dict) and isinstance(spec.get("metadata_json"), dict):
            compact_spec["metadata_json"] = copy.deepcopy(spec["metadata_json"])
    return out


def _compact_workflow_authoring_guide(data: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "schema_version",
        "source_of_truth_operation",
        "title",
        "summary",
        "audience",
        "principles",
        "complete_package_scope",
        "package_authoring_path",
        "reasoning_gates",
        "mechanical_gates",
        "independent_signoff",
        "decision_path",
        "template_contract_fields",
        "template_must_not_include",
        "extension_uses",
        "execution_path",
        "canonical_operations",
        "minimal_template_yaml",
        "examples",
    )
    return {key: _compact_value(data[key]) for key in keys if key in data}


def _compact_agent_preset_list(data: dict[str, Any]) -> dict[str, Any]:
    presets = data.get("presets")
    preset_items = presets if isinstance(presets, list) else []
    return {
        "presets": [
            _compact_mapping(item) if isinstance(item, dict) else _compact_value(item)
            for item in preset_items[:_MAX_COMPACT_LIST_ITEMS]
        ],
        "presets_count": len(preset_items),
        "presets_truncated": len(preset_items) > _MAX_COMPACT_LIST_ITEMS,
        "include_shadowed": bool(data.get("include_shadowed")),
    }


def _compact_agent_preset_resolution(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    workflow = data.get("workflow")
    if isinstance(workflow, dict):
        out["workflow"] = _compact_mapping(workflow)
    skill_requirements = _mapping_items(data.get("skill_requirements"))
    out["skill_requirements"] = [_compact_mapping(item) for item in skill_requirements]
    out["skill_requirements_count"] = len(skill_requirements)
    for key in ("required_agents", "recommended_agents", "optional_agents"):
        items = _mapping_items(data.get(key))
        out[key] = [_compact_resolved_agent(item) for item in items]
        out[f"{key}_count"] = len(items)
    for key in (
        "required_skill_presets",
        "recommended_skill_presets",
        "optional_skill_presets",
    ):
        items = _mapping_items(data.get(key))
        out[key] = [_compact_resolved_skill_preset(item) for item in items]
        out[f"{key}_count"] = len(items)
    for key in ("unresolved_requirements", "unresolved_skill_preset_requirements"):
        items = _mapping_items(data.get(key))
        out[key] = [_compact_mapping(item) for item in items]
        out[f"{key}_count"] = len(items)
    guidance = _string_items(data.get("setup_guidance"))
    out["setup_guidance"] = guidance[:_MAX_COMPACT_LIST_ITEMS]
    out["setup_guidance_count"] = len(guidance)
    if len(guidance) > _MAX_COMPACT_LIST_ITEMS:
        out["setup_guidance_truncated"] = True
    return out


def _compact_skill_preset_resolution(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    workflow = data.get("workflow")
    if isinstance(workflow, dict):
        out["workflow"] = _compact_mapping(workflow)
    for key in (
        "required_skill_presets",
        "recommended_skill_presets",
        "optional_skill_presets",
    ):
        items = _mapping_items(data.get(key))
        out[key] = [_compact_resolved_skill_preset(item) for item in items]
        out[f"{key}_count"] = len(items)
    unresolved = _mapping_items(data.get("unresolved_skill_preset_requirements"))
    out["unresolved_skill_preset_requirements"] = [
        _compact_mapping(item) for item in unresolved
    ]
    out["unresolved_skill_preset_requirements_count"] = len(unresolved)
    guidance = _string_items(data.get("setup_guidance"))
    out["setup_guidance"] = guidance[:_MAX_COMPACT_LIST_ITEMS]
    out["setup_guidance_count"] = len(guidance)
    if len(guidance) > _MAX_COMPACT_LIST_ITEMS:
        out["setup_guidance_truncated"] = True
    return out


def _compact_resolved_agent(item: dict[str, Any]) -> dict[str, Any]:
    loaded = _safe_dict(item.get("preset"))
    summary = _safe_dict(loaded.get("summary"))
    spec = _safe_dict(loaded.get("preset"))
    contract = _safe_dict(spec.get("prompt_contract"))
    preset = {
        key: summary.get(key)
        for key in (
            "key",
            "name",
            "version",
            "role",
            "role_class",
            "source",
            "plugin_slug",
            "generic_preset",
            "adaptation_required",
        )
        if summary.get(key) is not None
    }
    for key in (
        "mission",
        "responsibilities",
        "must_do",
        "must_not_do",
        "handoff_inputs",
        "handoff_outputs",
        "success_criteria",
        "self_check",
    ):
        if contract.get(key) is not None:
            preset[key] = _compact_value(contract.get(key))
    tools = _string_items(spec.get("recommended_tools"))
    preset["recommended_tools"] = tools[:_MAX_COMPACT_LIST_ITEMS]
    preset["recommended_tools_count"] = len(tools)
    if len(tools) > _MAX_COMPACT_LIST_ITEMS:
        preset["recommended_tools_truncated"] = True
    return {
        "role": item.get("role"),
        "requirement": item.get("requirement"),
        "purpose": item.get("purpose"),
        "applies_to_steps": _string_items(item.get("applies_to_steps")),
        "handoff_notes": _string_items(item.get("handoff_notes")),
        "preset": preset,
        "project_adaptation": _compact_adaptation(item.get("project_adaptation")),
    }


def _compact_resolved_skill_preset(item: dict[str, Any]) -> dict[str, Any]:
    loaded = _safe_dict(item.get("preset"))
    summary = _safe_dict(loaded.get("summary"))
    spec = _safe_dict(loaded.get("preset"))
    contract = _safe_dict(spec.get("operating_contract"))
    preset = {
        key: summary.get(key)
        for key in (
            "key",
            "name",
            "version",
            "skill_type",
            "source",
            "plugin_slug",
            "generic_preset",
            "adaptation_required",
        )
        if summary.get(key) is not None
    }
    for key in (
        "mission",
        "responsibilities",
        "must_do",
        "must_not_do",
        "required_outputs",
        "success_criteria",
        "self_check",
    ):
        if contract.get(key) is not None:
            preset[key] = _compact_value(contract.get(key))
    tools = _string_items(spec.get("recommended_tools"))
    preset["recommended_tools"] = tools[:_MAX_COMPACT_LIST_ITEMS]
    preset["recommended_tools_count"] = len(tools)
    if len(tools) > _MAX_COMPACT_LIST_ITEMS:
        preset["recommended_tools_truncated"] = True
    return {
        "requirement": item.get("requirement"),
        "purpose": item.get("purpose"),
        "applies_to_steps": _string_items(item.get("applies_to_steps")),
        "setup_notes": _string_items(item.get("setup_notes")),
        "preset": preset,
        "project_adaptation": _compact_adaptation(item.get("project_adaptation")),
    }


def _compact_adaptation(value: Any) -> dict[str, Any]:
    adaptation = _safe_dict(value)
    return {
        key: _compact_value(adaptation.get(key))
        for key in (
            "generic_preset",
            "adaptation_required",
            "do_not_use_verbatim",
            "adaptation_status",
            "instruction",
            "required_agent_action",
            "prompt_assembly_order",
            "required_context_refs",
            "conditional_context_refs",
        )
        if adaptation.get(key) is not None
    }


def _mapping_items(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _string_items(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _compact_text(value: Any, *, limit: int = _MAX_COMPACT_STRING_CHARS) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return f"{value[:limit]}..."
    return value


_SCHEMA_FRAGMENT_KEYS = (
    "title",
    "type",
    "format",
    "const",
    "pattern",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
)
_MAX_COMPACT_SCHEMA_DEPTH = 3


def _compact_schema(value: Any, *, include_properties: bool = True) -> dict[str, Any]:
    return _compact_schema_fragment(
        value,
        include_properties=include_properties,
        depth=0,
    )


def _compact_schema_fragment(
    value: Any,
    *,
    include_properties: bool,
    depth: int,
) -> dict[str, Any]:
    schema = _safe_dict(value)
    if not schema:
        return {}
    out: dict[str, Any] = {
        key: schema.get(key) for key in _SCHEMA_FRAGMENT_KEYS if schema.get(key) is not None
    }
    description = schema.get("description")
    if isinstance(description, str):
        out["description"] = _compact_text(
            description,
            limit=_MAX_SCHEMA_DESCRIPTION_CHARS,
        )
    default = schema.get("default")
    if isinstance(default, str | int | float | bool):
        out["default"] = _compact_text(default)
    enum = schema.get("enum")
    if isinstance(enum, list):
        out["enum"] = enum[:_MAX_COMPACT_LIST_ITEMS]
        out["enum_count"] = len(enum)
        if len(enum) > _MAX_COMPACT_LIST_ITEMS:
            out["enum_truncated"] = True
    required = schema.get("required")
    if isinstance(required, list):
        out["required"] = [str(item) for item in required if isinstance(item, str)]

    properties = schema.get("properties")
    if isinstance(properties, dict):
        names = [str(key) for key in properties]
        out["property_count"] = len(names)
        if include_properties and depth < _MAX_COMPACT_SCHEMA_DEPTH:
            out["properties"] = {
                key: _compact_schema_fragment(
                    properties.get(key),
                    include_properties=True,
                    depth=depth + 1,
                )
                for key in names[:_MAX_COMPACT_LIST_ITEMS]
            }
        else:
            out["property_names"] = names[:_MAX_COMPACT_LIST_ITEMS]
        if len(names) > _MAX_COMPACT_LIST_ITEMS:
            out["properties_truncated"] = True

    items = schema.get("items")
    if isinstance(items, dict):
        out["items"] = _compact_schema_fragment(
            items,
            include_properties=include_properties,
            depth=depth + 1,
        )

    for union_key in ("anyOf", "oneOf"):
        raw_union = schema.get(union_key)
        if not isinstance(raw_union, list):
            continue
        out[union_key] = [
            _compact_schema_fragment(
                option,
                include_properties=include_properties,
                depth=depth + 1,
            )
            for option in raw_union[:_MAX_COMPACT_LIST_ITEMS]
            if isinstance(option, dict)
        ]
        if len(raw_union) > _MAX_COMPACT_LIST_ITEMS:
            out[f"{union_key}_truncated"] = True

    additional_properties = schema.get("additionalProperties")
    if isinstance(additional_properties, bool):
        out["additionalProperties"] = additional_properties
    defs = schema.get("$defs")
    if isinstance(defs, dict):
        out["definition_count"] = len(defs)
    return {key: item for key, item in out.items() if item not in (None, {}, [])}


def _compact_mapping(value: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, item in value.items():
        if key in _CONTENT_OMIT_FIELDS:
            out[f"{key}_omitted"] = True
            continue
        if key in _SCHEMA_FIELD_NAMES:
            out[key] = _compact_schema(item) if isinstance(item, dict) else _compact_value(item)
            continue
        if key in _VERBATIM_KEEP_FIELDS:
            out[key] = copy.deepcopy(item)
            continue
        if key in _MAPPING_KEEP_FIELDS and isinstance(item, dict):
            out[key] = _compact_mapping(item)
            continue
        if (
            key in _SCALAR_KEEP_FIELDS
            or key.endswith(_REF_FIELD_SUFFIXES)
            or key.endswith(_URL_FIELD_SUFFIXES)
        ):
            out[key] = _compact_value(item)
            continue
        if key in _LIST_KEEP_FIELDS:
            out[key] = _compact_value(item)
            if isinstance(item, list):
                out[f"{key}_count"] = len(item)
                if len(item) > _MAX_COMPACT_LIST_ITEMS:
                    out[f"{key}_truncated"] = True
            continue
        if key in {"errors", "warnings"}:
            out[key] = _compact_value(item)
    return out


def _compact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _compact_mapping(value)
    if isinstance(value, list):
        return [_compact_value(item) for item in value[:_MAX_COMPACT_LIST_ITEMS]]
    return _compact_text(value)


def _ids_and_refs(value: dict[str, Any]) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"warnings", "errors"}:
            continue
        if (
            key in _SCALAR_KEEP_FIELDS
            or key.endswith(_REF_FIELD_SUFFIXES)
            or key.endswith(_URL_FIELD_SUFFIXES)
        ):
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
