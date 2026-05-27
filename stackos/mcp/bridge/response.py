"""Agent response modes and compact response shaping."""

from __future__ import annotations

import json
from typing import Any

from stackos.agent_responses import (
    compact_tracker_brief,
    compact_tracker_next,
    compact_tracker_status,
    compact_tracker_task,
    compact_tracker_ticket,
    compact_tracker_verify,
)

from .catalog import _bridge_tool_accepts_field
from .constants import _AGENT_COMPACT_DEFAULT_TOOL_NAMES, _AGENT_RESPONSE_MODE_FIELD
from .protocol import _bridge_as_int


def _bridge_response_mode(arguments: dict[str, Any]) -> str:
    raw = arguments.get(_AGENT_RESPONSE_MODE_FIELD)
    if raw in {"compact", "standard", "verbose"}:
        return str(raw)
    return "compact"


def _bridge_forward_arguments(
    *,
    catalog: dict[str, dict[str, Any]],
    tool_name: str,
    arguments: dict[str, Any],
    response_mode: str,
) -> dict[str, Any]:
    forwarded = dict(arguments)
    if not _bridge_tool_accepts_field(catalog, tool_name, _AGENT_RESPONSE_MODE_FIELD):
        forwarded.pop(_AGENT_RESPONSE_MODE_FIELD, None)
    if (
        response_mode == "verbose"
        and _bridge_tool_accepts_field(catalog, tool_name, "verbose")
        and "verbose" not in forwarded
    ):
        forwarded["verbose"] = True
    return forwarded


def _bridge_compact_tool_response(
    *,
    tool_name: str,
    response_text: str,
    response_mode: str,
) -> str:
    if response_mode != "compact" or tool_name not in _AGENT_COMPACT_DEFAULT_TOOL_NAMES:
        return response_text
    try:
        envelope = json.loads(response_text)
    except json.JSONDecodeError:
        return response_text
    if not isinstance(envelope, dict):
        return response_text
    result = envelope.get("result")
    if not isinstance(result, dict) or result.get("isError") is True:
        return response_text
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        return response_text
    compact = _bridge_compact_structured(tool_name, structured)
    if compact is None:
        return response_text
    text = json.dumps(compact, default=str, sort_keys=True)
    result["structuredContent"] = compact
    result["content"] = [{"type": "text", "text": text}]
    return json.dumps(envelope, default=str)


def _bridge_compact_structured(tool_name: str, structured: dict[str, Any]) -> dict[str, Any] | None:
    if tool_name in {
        "workspace.startSession",
        "workspace.resolve",
        "workspace.bootstrap",
        "workspace.connect",
    }:
        return _bridge_compact_workspace(structured)
    if tool_name == "auth.status":
        return _bridge_compact_auth_status(structured)
    if tool_name == "toolProfile.resolve":
        return _bridge_compact_tool_profile_resolve(structured)
    if tool_name == "communicationProfile.list":
        return _bridge_compact_profile_page(structured)
    if tool_name == "communicationProfile.get":
        return _bridge_compact_profile(structured)
    if tool_name == "action.describe":
        return _bridge_compact_action_describe(structured)
    if tool_name == "catalog.describe":
        return _bridge_compact_catalog_describe(structured)
    if tool_name == "tracker.status":
        return _bridge_compact_tracker_status(structured)
    if tool_name == "tracker.next":
        return _bridge_compact_tracker_next(structured)
    if tool_name == "tracker.brief":
        return _bridge_compact_tracker_brief(structured)
    if tool_name == "tracker.verify":
        return _bridge_compact_tracker_verify(structured)
    return None


def _bridge_compact_workspace(structured: dict[str, Any]) -> dict[str, Any]:
    data = structured.get("data") if isinstance(structured.get("data"), dict) else structured
    assert isinstance(data, dict)
    project_id = _bridge_as_int(structured.get("project_id")) or _bridge_as_int(
        data.get("project_id")
    )
    binding = data.get("binding") if isinstance(data.get("binding"), dict) else {}
    assert isinstance(binding, dict)
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    assert isinstance(project, dict)
    binding_id = (
        _bridge_as_int(data.get("workspace_binding_id"))
        or _bridge_as_int(data.get("id"))
        or _bridge_as_int(binding.get("id"))
    )
    compact_data: dict[str, Any] = {
        "workspace_bound": project_id is not None,
        "project_id": project_id,
        "workspace_binding_id": binding_id,
    }
    if isinstance(project.get("slug"), str):
        compact_data["project_slug"] = project["slug"]
    if isinstance(project.get("name"), str):
        compact_data["project_name"] = project["name"]
    if isinstance(data.get("needs_connect"), bool):
        compact_data["needs_connect"] = data["needs_connect"]
    elif isinstance(structured.get("needs_connect"), bool):
        compact_data["needs_connect"] = structured["needs_connect"]
    if isinstance(data.get("runtime"), str):
        compact_data["runtime"] = data["runtime"]
    if isinstance(data.get("client_session_id"), str):
        compact_data["client_session_id"] = data["client_session_id"]
    for field in ("repo_hints", "ui_paths", "next_step"):
        if field in data:
            compact_data[field] = data[field]
        elif field in structured:
            compact_data[field] = structured[field]
    candidates = data.get("candidate_projects", structured.get("candidate_projects"))
    if isinstance(candidates, list):
        compact_data["candidate_projects"] = [
            {
                "id": item.get("id"),
                "slug": item.get("slug"),
                "name": item.get("name"),
                "domain": item.get("domain"),
                "is_active": item.get("is_active"),
                "ui_paths": item.get("ui_paths"),
            }
            for item in candidates
            if isinstance(item, dict)
        ]
    for field in ("auto_bootstrap", "project_was_created", "binding_was_created"):
        if isinstance(data.get(field), bool):
            compact_data[field] = data[field]
    if "data" in structured:
        return {
            "data": compact_data,
            "project_id": project_id,
            "run_id": structured.get("run_id"),
        }
    return compact_data


def _bridge_compact_auth_status(structured: dict[str, Any]) -> dict[str, Any]:
    connections = [
        _bridge_compact_connection(item)
        for item in structured.get("connections", [])
        if isinstance(item, dict)
    ]
    by_provider: dict[str, list[dict[str, Any]]] = {}
    for connection in connections:
        key = str(connection.get("provider_key") or "")
        by_provider.setdefault(key, []).append(connection)
    providers: list[dict[str, Any]] = []
    for provider in structured.get("providers", []):
        if not isinstance(provider, dict):
            continue
        key = str(provider.get("key") or "")
        provider_connections = by_provider.get(key, [])
        providers.append(
            {
                "key": key,
                "name": provider.get("name"),
                "auth_type": provider.get("auth_type"),
                "status": "connected" if provider_connections else "missing",
                "credential_refs": [
                    item["credential_ref"]
                    for item in provider_connections
                    if isinstance(item.get("credential_ref"), str)
                ],
                "profile_keys": [
                    item["profile_key"]
                    for item in provider_connections
                    if isinstance(item.get("profile_key"), str)
                ],
                "setup_required": not bool(provider_connections),
            }
        )
    return {
        "project_id": structured.get("project_id"),
        "provider_key": structured.get("provider_key"),
        "providers": providers,
        "connections": connections,
    }


def _bridge_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _bridge_compact_connection(connection: dict[str, Any]) -> dict[str, Any]:
    account = _bridge_dict(connection.get("account"))
    return {
        "credential_ref": connection.get("credential_ref"),
        "provider_key": connection.get("provider_key"),
        "profile_key": connection.get("profile_key"),
        "status": connection.get("status"),
        "auth_type": connection.get("auth_type"),
        "display_name": account.get("display_name"),
        "provider_account_id": account.get("provider_account_id"),
        "setup_required": bool(connection.get("setup_required", False)),
    }


def _bridge_compact_tool_profile_resolve(structured: dict[str, Any]) -> dict[str, Any]:
    provider = _bridge_dict(structured.get("provider"))
    credential = _bridge_dict(structured.get("credential"))
    profile = _bridge_dict(structured.get("tool_profile"))
    identity = _bridge_dict(profile.get("identity"))
    access = _bridge_dict(profile.get("access_policy"))
    trigger = _bridge_dict(profile.get("trigger_policy"))
    account = _bridge_dict(credential.get("account"))
    return {
        "project_id": structured.get("project_id"),
        "provider_key": structured.get("provider_key"),
        "ready": bool(structured.get("ready")),
        "missing": structured.get("missing", []),
        "warnings": structured.get("warnings", []),
        "next_action": structured.get("next_action"),
        "provider": {
            "provider_key": provider.get("provider_key"),
            "plugin_slug": provider.get("plugin_slug"),
            "auth_type": provider.get("auth_type"),
            "setup_required": bool(provider.get("setup_required", False)),
        },
        "credential": {
            "credential_ref": credential.get("credential_ref"),
            "provider_key": credential.get("provider_key"),
            "profile_key": credential.get("profile_key"),
            "status": credential.get("status"),
            "display_name": account.get("display_name"),
            "provider_account_id": account.get("provider_account_id"),
            "setup_required": bool(credential.get("setup_required", False)),
        }
        if credential
        else None,
        "tool_profile": {
            "kind": profile.get("kind"),
            "key": profile.get("key"),
            "ref": profile.get("ref"),
            "enabled": profile.get("enabled"),
            "auth_profile_key": profile.get("auth_profile_key"),
            "identity": {
                "display_name": identity.get("display_name"),
                "purpose": identity.get("purpose"),
                "voice": identity.get("voice"),
            },
            "access": {
                "dm_mode": access.get("dm_mode"),
                "group_mode": access.get("group_mode"),
                "user_mode": access.get("user_mode"),
                "allowed_chat_refs": access.get("allowed_chat_refs", []),
                "allowed_user_refs": access.get("allowed_user_refs", []),
            },
            "trigger": {
                "dm_trigger": trigger.get("dm_trigger"),
                "group_trigger": trigger.get("group_trigger"),
                "command_count": len(trigger.get("commands", []) or []),
                "mention_patterns": trigger.get("mention_patterns", []),
            },
        }
        if profile
        else None,
    }


def _bridge_compact_profile_page(structured: dict[str, Any]) -> dict[str, Any]:
    return {
        "items": [
            _bridge_compact_profile(item)
            for item in structured.get("items", [])
            if isinstance(item, dict)
        ],
        "next_cursor": structured.get("next_cursor"),
        "total_estimate": structured.get("total_estimate"),
    }


def _bridge_compact_profile(profile: dict[str, Any]) -> dict[str, Any]:
    access = _bridge_dict(profile.get("access_policy"))
    trigger = _bridge_dict(profile.get("trigger_policy"))
    identity = _bridge_dict(profile.get("identity"))
    response = _bridge_dict(profile.get("response_policy"))
    provider_facets = {
        str(key): _bridge_dict(value)
        for key, value in _bridge_dict(profile.get("provider_facets")).items()
        if isinstance(value, dict)
    }
    commands = [
        {
            "command": item.get("command"),
            "enabled": item.get("enabled", True),
            "description": item.get("description"),
        }
        for item in trigger.get("commands", [])
        if isinstance(item, dict)
    ]
    return {
        "record_id": profile.get("record_id"),
        "project_id": profile.get("project_id"),
        "profile_ref": profile.get("profile_ref") or profile.get("external_id"),
        "key": profile.get("key"),
        "enabled": profile.get("enabled"),
        "provider_facets": provider_facets,
        "identity": {
            "display_name": identity.get("display_name"),
            "purpose": identity.get("purpose"),
            "voice": identity.get("voice"),
        },
        "access": {
            "dm_mode": access.get("dm_mode"),
            "group_mode": access.get("group_mode"),
            "user_mode": access.get("user_mode"),
            "allowed_chat_refs": access.get("allowed_chat_refs", []),
            "allowed_user_refs": access.get("allowed_user_refs", []),
            "denied_chat_refs_count": len(access.get("denied_chat_refs", []) or []),
            "denied_user_refs_count": len(access.get("denied_user_refs", []) or []),
        },
        "trigger": {
            "dm_trigger": trigger.get("dm_trigger"),
            "group_trigger": trigger.get("group_trigger"),
            "mention_patterns": trigger.get("mention_patterns", []),
            "reply_to_bot_triggers": trigger.get("reply_to_bot_triggers"),
            "commands": commands,
        },
        "response_policy": response,
        "send_policy": _bridge_dict(profile.get("send_policy")),
        "handoff_policy": _bridge_dict(profile.get("handoff_policy")),
        "approval_policy": _bridge_dict(profile.get("approval_policy")),
    }


def _bridge_compact_action_describe(structured: dict[str, Any]) -> dict[str, Any]:
    manifest = _bridge_dict(structured.get("manifest"))
    availability = _bridge_dict(structured.get("availability"))
    input_schema = _bridge_dict(manifest.get("input_schema_json"))
    properties = _bridge_dict(input_schema.get("properties"))
    raw_required = input_schema.get("required")
    required = raw_required if isinstance(raw_required, list) else []
    manifest_config = _bridge_dict(manifest.get("config_json"))
    compact_properties: dict[str, Any] = {}
    if isinstance(properties, dict):
        for key, prop in properties.items():
            if not isinstance(prop, dict):
                continue
            compact_properties[str(key)] = {
                name: prop.get(name)
                for name in ("type", "enum", "description")
                if prop.get(name) is not None
            }
    return {
        "action_ref": manifest.get("action_ref"),
        "plugin_slug": manifest.get("plugin_slug"),
        "action_key": manifest.get("action_key"),
        "provider_key": manifest.get("provider_key"),
        "capability_key": manifest.get("capability_key"),
        "risk_level": manifest.get("risk_level"),
        "operation": manifest.get("operation"),
        "requires_credential": manifest.get("requires_credential"),
        "connector_registered": structured.get("connector_registered"),
        "execution_available": structured.get("execution_available"),
        "availability": {
            "status": availability.get("status"),
            "executable": availability.get("executable"),
            "reasons": availability.get("reasons", []),
            "credential_refs": availability.get("credential_refs", []),
            "budget_state": availability.get("budget_state"),
        },
        "input": {
            "required": required if isinstance(required, list) else [],
            "properties": compact_properties,
        },
        "docs": manifest_config.get("docs", []),
    }


def _bridge_compact_catalog_describe(structured: dict[str, Any]) -> dict[str, Any]:
    plugins = []
    raw_plugins = structured.get("plugins")
    plugin_items: list[Any] = raw_plugins if isinstance(raw_plugins, list) else []
    for item in plugin_items:
        if not isinstance(item, dict):
            continue
        plugin = _bridge_dict(item.get("plugin"))
        raw_actions = item.get("actions")
        raw_resources = item.get("resources")
        raw_providers = item.get("providers")
        actions: list[Any] = raw_actions if isinstance(raw_actions, list) else []
        resources: list[Any] = raw_resources if isinstance(raw_resources, list) else []
        providers: list[Any] = raw_providers if isinstance(raw_providers, list) else []
        plugins.append(
            {
                "slug": plugin.get("slug"),
                "name": plugin.get("name"),
                "version": plugin.get("version"),
                "enabled_for_project": plugin.get("enabled_for_project"),
                "providers": [
                    provider.get("key") for provider in providers if isinstance(provider, dict)
                ],
                "actions": [
                    {
                        "action_ref": action_item.get("action_ref"),
                        "risk_level": action_item.get("risk_level"),
                        "provider_key": action_item.get("provider_key"),
                        "status": _bridge_dict(action_item.get("availability")).get("status"),
                    }
                    for action in actions
                    if isinstance(action, dict)
                    for action_item in [_bridge_dict(action)]
                ],
                "resources": [
                    resource.get("key") for resource in resources if isinstance(resource, dict)
                ],
            }
        )
    return {"plugins": plugins}


def _bridge_compact_tracker_status(structured: dict[str, Any]) -> dict[str, Any]:
    return compact_tracker_status(structured)


def _bridge_compact_tracker_next(structured: dict[str, Any]) -> dict[str, Any]:
    return compact_tracker_next(structured)


def _bridge_compact_tracker_brief(structured: dict[str, Any]) -> dict[str, Any]:
    return compact_tracker_brief(structured)


def _bridge_compact_tracker_verify(structured: dict[str, Any]) -> dict[str, Any]:
    return compact_tracker_verify(structured)


def _bridge_compact_tracker_task(task: dict[str, Any]) -> dict[str, Any]:
    return compact_tracker_task(task)


def _bridge_compact_tracker_ticket(ticket: dict[str, Any]) -> dict[str, Any]:
    return compact_tracker_ticket(ticket)
