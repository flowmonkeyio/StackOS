"""Plugin MCP bridge surface tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from stackos.mcp.bridge import (
    _AGENT_ADMIN_GATED_TOOL_NAMES,
    _AGENT_BASE_TOOLBOX_NAMES,
    _AGENT_GATED_TOOL_NAMES,
    _AGENT_RUN_PLAN_GATED_TOOL_NAMES,
    _AGENT_SETUP_TOOLBOX_NAMES,
    _AGENT_STEP_GATED_TOOL_NAMES,
    _AGENT_VISIBLE_TOOL_NAMES,
    _AGENT_VISIBLE_TOOL_ORDER,
    AgentBridgeProxy,
    _bridge_allowed_tool_names,
    _bridge_cache_controller_run_context,
    _bridge_cache_step_context,
    _bridge_compact_profile,
    _bridge_compact_structured,
    _bridge_filter_tool_list_response,
    _bridge_forward_arguments,
    _bridge_toolbox_describe,
)
from stackos.mcp.contract import verb_is_mutating
from stackos.mcp.permissions import SKILL_TOOL_GRANTS, SYSTEM_SKILL
from stackos.mcp.server import ToolRegistry, _to_tool
from stackos.mcp.streaming import ProgressEmitter
from stackos.mcp.tools import register_all
from stackos.operations.registry import build_operation_registry


def _tool(
    name: str,
    *,
    operation_name: str | None = None,
    grant_policy: str | None = None,
    response_policy: dict[str, object] | None = None,
) -> dict[str, object]:
    out: dict[str, object] = {
        "name": name,
        "description": f"{name} description",
        "inputSchema": {"type": "object"},
        "outputSchema": {"type": "object"},
    }
    if operation_name is not None or grant_policy is not None or response_policy is not None:
        meta: dict[str, object] = {}
        if operation_name is not None:
            meta["operation_name"] = operation_name
        if grant_policy is not None:
            meta["grant_policy"] = grant_policy
        if response_policy is not None:
            meta["response_policy"] = response_policy
        out["_meta"] = meta
    return out


def _structured(response_text: str) -> dict[str, object]:
    envelope = json.loads(response_text)
    return envelope["result"]["structuredContent"]


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.text = json.dumps(payload)

    def raise_for_status(self) -> None:
        return None


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def post(self, _url: str, *, content: str, headers: dict[str, str]) -> _Response:
        del headers
        body = json.loads(content)
        self.calls.append(body)
        if body["method"] == "tools/list":
            return _Response(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "tools": [
                            _tool("resource.query"),
                            _tool("budget.set"),
                            _tool("resource.upsert"),
                            _tool("runPlan.get"),
                        ]
                    },
                }
            )
        tool_name = body["params"]["name"]
        if tool_name == "runPlan.get":
            return _Response(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "structuredContent": {
                            "data": {
                                "id": body["params"]["arguments"]["run_plan_id"],
                                "run_id": 9,
                                "steps": [
                                    {
                                        "step_id": "write",
                                        "status": "running",
                                        "allowed_tools": ["resource.upsert"],
                                    }
                                ],
                            },
                            "run_id": 9,
                        }
                    },
                }
            )
        return _Response(
            {
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "structuredContent": {
                        "tool": tool_name,
                        "arguments": body["params"]["arguments"],
                    }
                },
            }
        )


class _RefreshingCatalogClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.tool_list_calls = 0

    def post(self, _url: str, *, content: str, headers: dict[str, str]) -> _Response:
        del headers
        body = json.loads(content)
        self.calls.append(body)
        if body["method"] == "tools/list":
            self.tool_list_calls += 1
            tools = [_tool("budget.set")]
            if self.tool_list_calls > 1:
                tools.append(_tool("agentPreset.resolveForWorkflow"))
            return _Response(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"tools": tools},
                }
            )
        tool_name = body["params"]["name"]
        return _Response(
            {
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "structuredContent": {
                        "tool": tool_name,
                        "arguments": body["params"]["arguments"],
                    }
                },
            }
        )


def test_bridge_tools_list_hides_daemon_internals() -> None:
    daemon_tools = [
        *[_tool(name) for name in _AGENT_VISIBLE_TOOL_ORDER],
        _tool("auth.start"),
        _tool("plugin.enable"),
        _tool("resource.upsert"),
        _tool("agentRequest.create"),
        _tool("artifact.create"),
        _tool("cost.queryProject"),
        _tool("learning.update"),
    ]
    daemon_response = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": daemon_tools}})

    filtered = json.loads(_bridge_filter_tool_list_response(daemon_response))
    names = [tool["name"] for tool in filtered["result"]["tools"]]

    assert names[: len(_AGENT_VISIBLE_TOOL_ORDER)] == list(_AGENT_VISIBLE_TOOL_ORDER)
    assert "toolbox.describe" in names
    assert "toolbox.call" in names
    assert "auth.start" not in names
    assert "plugin.enable" not in names
    assert "resource.upsert" not in names
    assert "agentRequest.create" not in names
    assert "artifact.create" not in names
    assert "cost.queryProject" not in names
    assert "learning.update" not in names


def test_bridge_toolbox_describes_setup_and_current_step_tools_only() -> None:
    catalog = {
        name: _tool(name)
        for name in [
            "auth.start",
            "resource.upsert",
            "action.execute",
            "cost.queryProject",
            "artifact.create",
            "learning.update",
            "project.update",
        ]
    }
    catalog["action.execute"] = _tool("action.execute", operation_name="action.execute")
    catalog["project.update"] = _tool(
        "project.update",
        operation_name="project.update",
        grant_policy="local-admin-project-write",
    )
    catalog["resource.upsert"] = _tool("resource.upsert")
    allowed_by_run = {7: {"resource.upsert", "action.execute"}}

    response = _bridge_toolbox_describe(
        42,
        catalog=catalog,
        arguments={
            "run_id": 7,
            "tool_names": [
                "auth.start",
                "integration.test",
                "resource.upsert",
                "action.execute",
                "cost.queryProject",
                "artifact.create",
                "learning.update",
                "project.update",
                "missing",
            ],
        },
        run_id=7,
        allowed_by_run=allowed_by_run,
    )
    payload = _structured(response)

    assert [tool["name"] for tool in payload["described_tools"]] == [
        "resource.upsert",
        "action.execute",
        "cost.queryProject",
    ]
    assert payload["active_step_tool_names"] == ["action.execute", "resource.upsert"]
    assert payload["tool_categories"]["active_step"] == ["action.execute", "resource.upsert"]
    assert payload["tool_categories"]["operation_backed"] == ["action.execute"]
    assert set(payload["denied_tool_names"]) == {
        "artifact.create",
        "auth.start",
        "learning.update",
        "project.update",
    }
    assert payload["unknown_tool_names"] == ["integration.test", "missing"]
    assert "admin_gated_tool_names" not in payload
    statuses = {item["name"]: item for item in payload["tool_statuses"]}
    assert statuses["auth.start"]["reason_code"] == "local_admin_required"
    assert statuses["artifact.create"]["reason_code"] == "run_plan_step_grant_required"
    assert statuses["project.update"]["reason_code"] == "local_admin_required"
    assert statuses["project.update"]["grant_policy"] == "local-admin-project-write"
    assert statuses["resource.upsert"]["reason_code"] == "active_step_granted"
    assert statuses["action.execute"]["operation"]["name"] == "action.execute"
    assert statuses["missing"]["reason_code"] == "unknown_tool"


def test_bridge_toolbox_allows_daemon_registered_direct_operations() -> None:
    catalog = {
        "custom.reopen": _tool(
            "custom.reopen",
            operation_name="custom.reopen",
            grant_policy="direct-tracker-write",
        ),
        "custom.admin": _tool(
            "custom.admin",
            operation_name="custom.admin",
            grant_policy="local-admin-project-write",
        ),
        "custom.step": _tool(
            "custom.step",
            operation_name="custom.step",
            grant_policy="run-plan-step-grant",
        ),
    }

    response = _bridge_toolbox_describe(
        42,
        catalog=catalog,
        arguments={
            "tool_names": [
                "custom.reopen",
                "custom.admin",
                "custom.step",
            ],
        },
        run_id=None,
        allowed_by_run={},
    )
    payload = _structured(response)

    assert [tool["name"] for tool in payload["described_tools"]] == ["custom.reopen"]
    assert set(payload["denied_tool_names"]) == {"custom.admin", "custom.step"}
    statuses = {item["name"]: item for item in payload["tool_statuses"]}
    assert statuses["custom.reopen"]["reason_code"] == "available"
    assert statuses["custom.reopen"]["operation"]["grant_policy"] == "direct-tracker-write"
    assert statuses["custom.admin"]["reason_code"] == "local_admin_required"
    assert statuses["custom.step"]["reason_code"] == "tool_not_available_in_current_bridge_scope"
    assert "custom.reopen" in _bridge_allowed_tool_names(
        None,
        {},
        catalog=catalog,
    )


def test_bridge_forwards_policy_default_response_mode() -> None:
    catalog = {
        "action.run": _tool(
            "action.run",
            operation_name="action.run",
            response_policy={"default_mode": "raw", "allowed_modes": ["raw"]},
        )
    }
    catalog["action.run"]["inputSchema"] = {
        "type": "object",
        "properties": {"response_mode": {"type": "string"}},
    }

    forwarded = _bridge_forward_arguments(
        catalog=catalog,
        tool_name="action.run",
        arguments={},
        response_mode="compact",
    )

    assert forwarded["response_mode"] == "raw"


def test_bridge_caches_run_token_and_step_grants() -> None:
    response = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "structuredContent": {
                    "data": {
                        "run_id": 7,
                        "run_token": "tok",
                        "step_id": "write",
                        "allowed_tools": ["resource.upsert", "action.execute", 123, ""],
                    }
                }
            },
        }
    )
    allowed_by_run: dict[int, set[str]] = {}
    tokens_by_run: dict[int, str] = {}

    _bridge_cache_step_context(
        response,
        allowed_by_run=allowed_by_run,
        tokens_by_run=tokens_by_run,
    )

    assert tokens_by_run == {7: "tok"}
    assert allowed_by_run == {
        7: set(_AGENT_STEP_GATED_TOOL_NAMES) | {"action.execute", "resource.upsert"}
    }


def test_bridge_caches_run_plan_controller_grants() -> None:
    response = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "structuredContent": {
                    "data": {
                        "run_id": 9,
                        "run_token": "tok-plan",
                        "plan": {"id": 3},
                    }
                }
            },
        }
    )
    allowed_by_run: dict[int, set[str]] = {}
    tokens_by_run: dict[int, str] = {}
    plans_by_run: dict[int, int] = {}

    _bridge_cache_step_context(
        response,
        allowed_by_run=allowed_by_run,
        tokens_by_run=tokens_by_run,
        plans_by_run=plans_by_run,
    )

    assert tokens_by_run == {9: "tok-plan"}
    assert allowed_by_run == {9: set(_AGENT_STEP_GATED_TOOL_NAMES)}
    assert plans_by_run == {9: 3}


def test_bridge_recovers_controller_grants_from_run_record() -> None:
    response = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "structuredContent": {
                    "id": 9,
                    "project_id": 2,
                    "status": "running",
                    "client_session_id": "tok-resume",
                    "metadata_json": {
                        "skill_name": "stackos/run-plan-controller",
                        "run_plan_id": 22,
                    },
                }
            },
        }
    )
    allowed_by_run: dict[int, set[str]] = {}
    tokens_by_run: dict[int, str] = {}
    plans_by_run: dict[int, int] = {}

    _bridge_cache_controller_run_context(
        response,
        allowed_by_run=allowed_by_run,
        tokens_by_run=tokens_by_run,
        plans_by_run=plans_by_run,
    )

    assert tokens_by_run == {9: "tok-resume"}
    assert allowed_by_run == {9: set(_AGENT_STEP_GATED_TOOL_NAMES)}
    assert plans_by_run == {9: 22}


def test_bridge_caches_claimed_run_plan_step_grants() -> None:
    response = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "structuredContent": {
                    "data": {
                        "step_id": "write",
                        "status": "running",
                        "allowed_tools": ["resource.upsert", 123, ""],
                    },
                    "run_id": 9,
                    "project_id": 1,
                }
            },
        }
    )
    allowed_by_run: dict[int, set[str]] = {}
    tokens_by_run: dict[int, str] = {9: "tok"}

    _bridge_cache_step_context(
        response,
        allowed_by_run=allowed_by_run,
        tokens_by_run=tokens_by_run,
    )

    assert allowed_by_run == {9: set(_AGENT_STEP_GATED_TOOL_NAMES) | {"resource.upsert"}}


def test_bridge_does_not_advertise_step_tools_without_cached_token() -> None:
    response = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "structuredContent": {
                    "data": {
                        "id": 3,
                        "run_id": 9,
                        "steps": [
                            {
                                "step_id": "write",
                                "status": "running",
                                "allowed_tools": ["resource.upsert"],
                            }
                        ],
                    },
                    "run_id": 9,
                }
            },
        }
    )
    allowed_by_run: dict[int, set[str]] = {}
    tokens_by_run: dict[int, str] = {}

    _bridge_cache_step_context(
        response,
        allowed_by_run=allowed_by_run,
        tokens_by_run=tokens_by_run,
    )

    assert allowed_by_run == {}


def test_bridge_base_toolbox_includes_product_state_but_not_vendor_surface() -> None:
    assert set(_AGENT_VISIBLE_TOOL_ORDER) == _AGENT_VISIBLE_TOOL_NAMES
    assert {"workspace.startSession", "workspace.resolve"} <= _AGENT_VISIBLE_TOOL_NAMES
    assert "browser.page.call" in _AGENT_VISIBLE_TOOL_NAMES
    assert "browser.context.call" in _AGENT_VISIBLE_TOOL_NAMES
    assert "browser.handle.call" in _AGENT_VISIBLE_TOOL_NAMES
    assert "browser.script.run" in _AGENT_VISIBLE_TOOL_NAMES
    assert "operation.list" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "operation.describe" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "workspace.bootstrap" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "project.list" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "project.get" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "project.create" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "plugin.list" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "readiness.check" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "action.describe" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "action.list" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "action.validate" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "action.run" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "integration.list" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "agentPreset.list" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "agentPreset.describe" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "agentPreset.resolveForWorkflow" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "skillPreset.list" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "skillPreset.describe" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "skillPreset.resolveForWorkflow" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "agentRequest.list" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "agentRequest.get" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "agentRequest.claim" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "agentRequest.release" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "agentRequest.linkRunPlan" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "agentRequest.prepareRunPlan" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "agentRequest.complete" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "agentRequest.ignore" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "catalog.describe" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "capability.list" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "provider.describe" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "resource.query" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "artifact.get" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "auth.status" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "auth.test" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "communicationProfile.list" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "communicationProfile.get" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "communicationProfile.upsert" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "communication.send" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "communication.reply" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "communicationSurface.list" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "communicationSurface.upsert" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "communicationContact.list" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "communicationContact.upsert" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "communicationMembership.list" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "communicationMembership.upsert" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "communicationTarget.list" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "communicationTarget.resolve" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "communicationTarget.upsert" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "communicationRoute.list" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "communicationRoute.upsert" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "communicationContext.query" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "toolProfile.resolve" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "localAgentChat.createMessage" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "context.query" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "context.timeline" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "learning.query" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "experiment.query" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "decision.query" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "workflowExtension.list" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "workflowExtension.get" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "workflowExtension.delete" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "workflowExtension.validate" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "workflowExtension.upsert" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "workflowTemplate.authoringGuide" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "workflowTemplate.list" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "workflowTemplate.describe" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "workflowTemplate.validate" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "runPlan.create" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "runPlan.validate" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "runPlan.start" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "runPlan.abort" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "runPlan.checkConsistency" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "runPlan.get" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "runPlan.list" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "runPlan.reopen" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "tracker.rejectTask" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "tracker.reopen" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "runPlan.claimStep" in _AGENT_STEP_GATED_TOOL_NAMES
    assert "runPlan.recordStep" in _AGENT_STEP_GATED_TOOL_NAMES
    assert "runPlan.update" not in _AGENT_STEP_GATED_TOOL_NAMES
    assert "action.execute" in _AGENT_RUN_PLAN_GATED_TOOL_NAMES
    assert "agentRequest.create" in _AGENT_RUN_PLAN_GATED_TOOL_NAMES
    assert "communication.send" in _AGENT_RUN_PLAN_GATED_TOOL_NAMES
    assert "communication.reply" in _AGENT_RUN_PLAN_GATED_TOOL_NAMES


def test_bridge_compacts_action_describe_with_capability_metadata() -> None:
    compact = _bridge_compact_structured(
        "action.describe",
        {
            "manifest": {
                "action_ref": "utils.image.generate",
                "plugin_slug": "utils",
                "action_key": "image.generate",
                "provider_key": "openai-images",
                "capability_key": "image-generation",
                "risk_level": "cost",
                "operation": "image.generate",
                "requires_credential": True,
                "input_schema_json": {
                    "type": "object",
                    "required": ["prompt"],
                    "properties": {
                        "prompt": {"type": "string", "maxLength": 32000},
                    },
                },
                "config_json": {
                    "capability_metadata": {
                        "modes": ["text-to-image"],
                        "limits": {"prompt_max_chars": 32000},
                    },
                    "docs": ["https://developers.openai.com/api/docs/guides/image-generation"],
                },
            },
            "availability": {"status": "ready", "executable": True},
            "connector_registered": True,
            "execution_available": True,
            "provider_setup": {
                "provider_key": "openai-images",
                "local_setup_url": (
                    "http://127.0.0.1:5180/projects/1/connections?provider_key=openai-images"
                ),
                "api_key_url": "https://platform.openai.com/api-keys",
                "docs_url": "https://developers.openai.com/api/docs/guides/image-generation",
            },
            "execution_context": {
                "decision_rule": "If provider/account scope repeats, use context_ref.",
                "discover": {
                    "operation": "executionContext.discover",
                    "arguments": {
                        "project_id": 1,
                        "action_ref": "utils.image.generate",
                    },
                },
                "pass_context_ref_to": ["action.validate", "action.run", "action.execute"],
            },
        },
    )

    assert compact is not None
    assert compact["capability_metadata"]["modes"] == ["text-to-image"]
    assert compact["capability_metadata"]["limits"]["prompt_max_chars"] == 32000
    assert compact["input"]["properties"]["prompt"] == {"type": "string"}
    assert compact["provider_setup"]["api_key_url"] == "https://platform.openai.com/api-keys"
    assert compact["execution_context"]["discover"]["operation"] == "executionContext.discover"
    assert compact["execution_context"]["pass_context_ref_to"] == [
        "action.validate",
        "action.run",
        "action.execute",
    ]


def test_bridge_compacts_communication_profile_without_flat_provider_fields() -> None:
    compact = _bridge_compact_profile(
        {
            "record_id": 12,
            "project_id": 1,
            "profile_ref": "communication-profile:support",
            "key": "support",
            "enabled": True,
            "identity": {"display_name": "Support", "purpose": "Help", "voice": "Calm"},
            "provider_facets": {
                "telegram-bot": {
                    "auth_profile_key": "support-telegram",
                    "bot_username": "support_bot",
                    "ingress_mode": "webhook",
                },
                "slack-bot": {"auth_profile_key": "support-slack", "bot_user_id": "U123"},
            },
            "access_policy": {
                "dm_mode": "all",
                "group_mode": "all",
                "user_mode": "allowlist",
                "allowed_user_refs": ["telegram-user:555"],
            },
            "trigger_policy": {"commands": [{"command": "/support"}]},
            "response_policy": {"origin_required": True},
            "send_policy": {"mode": "explicit-targets"},
            "handoff_policy": {"mode": "explicit-targets"},
            "approval_policy": {"mode": "none"},
        }
    )

    assert compact["profile_ref"] == "communication-profile:support"
    assert compact["provider_facets"]["telegram-bot"]["auth_profile_key"] == "support-telegram"
    assert compact["provider_facets"]["slack-bot"]["auth_profile_key"] == "support-slack"
    assert compact["send_policy"] == {"mode": "explicit-targets"}
    assert "auth_profile_key" not in compact
    assert "bot_username" not in compact
    assert "context.query" in _AGENT_RUN_PLAN_GATED_TOOL_NAMES
    assert "resource.upsert" in _AGENT_RUN_PLAN_GATED_TOOL_NAMES
    assert "artifact.create" in _AGENT_RUN_PLAN_GATED_TOOL_NAMES
    assert "artifact.update" in _AGENT_RUN_PLAN_GATED_TOOL_NAMES
    assert "artifact.archive" in _AGENT_RUN_PLAN_GATED_TOOL_NAMES
    assert "artifact.supersede" in _AGENT_RUN_PLAN_GATED_TOOL_NAMES
    assert "integration.set" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "integration.test" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "integration.list" in _AGENT_BASE_TOOLBOX_NAMES
    assert "integration.remove" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "cost.queryProject" in _AGENT_BASE_TOOLBOX_NAMES
    assert "plugin.enable" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "plugin.disable" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "resource.upsert" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "artifact.create" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "artifact.update" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "artifact.archive" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "artifact.supersede" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "action.list" in _AGENT_BASE_TOOLBOX_NAMES
    assert "agentRequest.create" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "auth.start" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "auth.revoke" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "project.create" in _AGENT_BASE_TOOLBOX_NAMES
    assert "project.list" in _AGENT_BASE_TOOLBOX_NAMES
    assert "learning.create" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "experiment.recordDecision" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "decision.record" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "workflowTemplate.save" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "workflowTemplate.fork" not in _AGENT_BASE_TOOLBOX_NAMES
    assert {
        "auth.revoke",
        "auth.start",
        "plugin.enable",
        "plugin.disable",
        "runPlan.update",
        "workflowTemplate.fork",
        "workflowTemplate.save",
    } == _AGENT_ADMIN_GATED_TOOL_NAMES
    assert {
        "action.execute",
        "agentRequest.create",
        "artifact.create",
        "artifact.update",
        "artifact.archive",
        "artifact.supersede",
        "browser.context.call",
        "browser.handle.call",
        "browser.method.manifest",
        "browser.page.call",
        "browser.page.screenshot",
        "browser.page.snapshot",
        "browser.profile.create",
        "browser.profile.list",
        "browser.runtime.status",
        "browser.script.inject",
        "browser.script.run",
        "browser.session.list",
        "browser.session.start",
        "browser.session.status",
        "browser.session.stop",
        "communication.reply",
        "communication.send",
        "context.query",
        "context.snapshot",
        "decision.record",
        "experiment.create",
        "experiment.recordDecision",
        "experiment.recordObservation",
        "learning.create",
        "learning.update",
        "resource.upsert",
    } == _AGENT_RUN_PLAN_GATED_TOOL_NAMES
    assert _AGENT_GATED_TOOL_NAMES == (
        _AGENT_ADMIN_GATED_TOOL_NAMES | _AGENT_RUN_PLAN_GATED_TOOL_NAMES
    )
    assert "artifact.create" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "artifact.update" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "artifact.archive" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "artifact.supersede" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "learning.update" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "action.execute" not in _AGENT_BASE_TOOLBOX_NAMES


def test_bridge_compacts_tracker_brief_for_agent_context() -> None:
    compact = _bridge_compact_structured(
        "tracker.brief",
        {
            "ticket": {
                "id": 7,
                "key": "deliver",
                "title": "Deliver",
                "status": "in-progress",
                "task_key": "workflow-1",
                "priority_key": "p1",
                "lane_key": "implementation",
                "assignee": "codex",
                "blocked_by": [],
                "blocker_reason": None,
                "dependency_keys": ["prepare"],
                "outcome": None,
                "run_plan_id": 4,
                "run_plan_step_id": 12,
                "run_id": 9,
                "agent_request_id": None,
                "reference_count": 2,
                "link_count": 1,
                "context_json": {"large": "omitted"},
            },
            "task": {
                "id": 3,
                "key": "workflow-1",
                "title": "Workflow",
                "status": "in-progress",
                "priority_key": "p1",
                "lane_key": "implementation",
                "owner": "ops",
                "task_type": "workflow",
                "source_kind": "workflow",
                "source_json": {
                    "template_key": "demo",
                    "run_plan_key": "demo-run",
                    "run_plan_id": 4,
                },
                "metadata_json": {"large": "omitted"},
            },
            "dependencies": [{"key": "prepare", "title": "Prepare", "status": "complete"}],
            "dependents": [],
            "references": [{"id": 1, "ref_type": "file", "ref": "README.md", "title": "Readme"}],
            "links": [{"id": 2, "link_kind": "run-plan", "ref": "run-plan:4"}],
            "workflow_handoff": {
                "run_plan_id": 4,
                "run_plan_step_id": 12,
                "run_id": 9,
                "step_id": "deliver",
                "run_plan_key": "demo-run",
                "template_key": "demo",
                "next_operations": [
                    "runPlan.get",
                    "runPlan.claimStep",
                    "toolbox.describe",
                    "runPlan.recordStep",
                ],
                "notes": ["verbose guidance omitted from compact response"],
            },
            "suggested_next_actions": ["finish the work"],
        },
    )

    assert compact == {
        "ticket": {
            "key": "deliver",
            "title": "Deliver",
            "status": "in-progress",
            "task_key": "workflow-1",
            "priority_key": "p1",
            "lane_key": "implementation",
            "assignee": "codex",
            "dependency_keys": ["prepare"],
            "run_plan_id": 4,
            "run_plan_step_id": 12,
            "run_id": 9,
            "reference_count": 2,
            "link_count": 1,
        },
        "task": {
            "key": "workflow-1",
            "title": "Workflow",
            "status": "in-progress",
            "priority_key": "p1",
            "lane_key": "implementation",
            "owner": "ops",
            "task_type": "workflow",
            "source_kind": "workflow",
            "template_key": "demo",
            "run_plan_key": "demo-run",
            "run_plan_id": 4,
        },
        "dependencies": [
            {
                "key": "prepare",
                "title": "Prepare",
                "status": "complete",
            }
        ],
        "references": [{"ref_type": "file", "ref": "README.md", "title": "Readme"}],
        "links": [
            {
                "link_kind": "run-plan",
                "ref": "run-plan:4",
            }
        ],
        "workflow_handoff": {
            "run_plan_id": 4,
            "run_plan_step_id": 12,
            "run_id": 9,
            "step_id": "deliver",
            "run_plan_key": "demo-run",
            "template_key": "demo",
            "next_operations": [
                "runPlan.get",
                "runPlan.claimStep",
                "toolbox.describe",
                "runPlan.recordStep",
            ],
        },
        "suggested_next_actions": ["finish the work"],
    }


def test_bridge_setup_surface_covers_core_setup_mutations() -> None:
    core_setup_mutations = {
        "budget.set",
        "budget.update",
        "schedule.remove",
        "schedule.set",
        "schedule.toggle",
    }

    assert core_setup_mutations <= _AGENT_BASE_TOOLBOX_NAMES


def test_bridge_agent_operation_surface_matches_registered_daemon_tools() -> None:
    registry = ToolRegistry()
    register_all(registry)
    registered = set(registry._tools)

    assert registered >= _AGENT_BASE_TOOLBOX_NAMES


def test_daemon_mcp_tools_are_operation_backed_without_expanding_bridge_surface() -> None:
    registry = ToolRegistry()
    register_all(registry)
    operations = {operation.name for operation in build_operation_registry().all()}

    tool_names = {spec.name for spec in registry.all()}
    assert tool_names == operations
    assert all(spec.operation_name == spec.name for spec in registry.all())


def test_bridge_system_grant_matches_agent_operation_surface() -> None:
    system_tools = SKILL_TOOL_GRANTS[SYSTEM_SKILL]
    assert system_tools >= _AGENT_BASE_TOOLBOX_NAMES
    direct_safe_tools = {
        "context.query",
        "communication.reply",
        "communication.send",
        *[name for name in _AGENT_RUN_PLAN_GATED_TOOL_NAMES if name.startswith("browser.")],
    }
    assert (_AGENT_RUN_PLAN_GATED_TOOL_NAMES - direct_safe_tools).isdisjoint(system_tools)
    assert _AGENT_ADMIN_GATED_TOOL_NAMES.isdisjoint(system_tools)


def test_registered_product_mutations_are_agent_reachable() -> None:
    registry = ToolRegistry()
    register_all(registry)
    registered = set(registry._tools)
    agent_surface = _AGENT_VISIBLE_TOOL_NAMES | _AGENT_SETUP_TOOLBOX_NAMES

    hidden_mutations = {
        name
        for name in registered
        if verb_is_mutating(name)
        and name not in agent_surface
        and name not in _AGENT_GATED_TOOL_NAMES
        and name not in _AGENT_STEP_GATED_TOOL_NAMES
        and not name.startswith("project.")
        and not name.startswith(
            (
                "dataforseo.",
                "firecrawl.",
                "googlePaa.",
                "jina.",
                "openaiImages.",
                "reddit.",
                "ahrefs.",
            )
        )
    }

    assert hidden_mutations == set()


def test_bridge_setup_surface_is_bootstrap_granted() -> None:
    system_tools = SKILL_TOOL_GRANTS[SYSTEM_SKILL]

    assert set(_AGENT_VISIBLE_TOOL_ORDER) <= system_tools
    assert system_tools >= _AGENT_SETUP_TOOLBOX_NAMES


def test_operation_discovery_tools_return_operation_spec_guidance() -> None:
    registry = ToolRegistry()
    register_all(registry)

    list_spec = registry.get("operation.list")
    describe_spec = registry.get("operation.describe")
    assert list_spec.operation_name == "operation.list"
    assert describe_spec.operation_name == "operation.describe"
    assert _to_tool(list_spec).model_dump(by_alias=True)["_meta"]["operation_name"] == (
        "operation.list"
    )
    assert _to_tool(describe_spec).model_dump(by_alias=True)["_meta"]["operation_name"] == (
        "operation.describe"
    )

    listed = asyncio.run(
        list_spec.handler(
            list_spec.input_model.model_validate({"surface": "mcp"}),
            None,  # type: ignore[arg-type]
            ProgressEmitter(None, None),
        )
    )
    listed_names = {item.name for item in listed.items}
    assert "operation.list" in listed_names
    assert "operation.describe" in listed_names
    assert "communication.send" in listed_names
    assert all(item.surfaces["mcp"].enabled for item in listed.items)

    described = asyncio.run(
        describe_spec.handler(
            describe_spec.input_model.model_validate(
                {"name": "communication.send", "surface": "mcp"}
            ),
            None,  # type: ignore[arg-type]
            ProgressEmitter(None, None),
        )
    )
    assert described.name == "communication.send"
    assert described.grant_policy == "direct-communication-send"
    assert "properties" in described.input_schema
    assert described.prerequisites

    self_described = asyncio.run(
        describe_spec.handler(
            describe_spec.input_model.model_validate(
                {"name": "operation.describe", "surface": "mcp"}
            ),
            None,  # type: ignore[arg-type]
            ProgressEmitter(None, None),
        )
    )
    assert self_described.name == "operation.describe"
    assert self_described.grant_policy == "direct-read"
    assert "name" in self_described.input_schema["properties"]


def test_bridge_proxy_forwards_step_tool_with_cached_run_token() -> None:
    proxy = AgentBridgeProxy(url="http://daemon/mcp", headers={})
    proxy.tokens_by_run[7] = "tok"
    proxy.allowed_by_run[7] = {"resource.upsert"}
    client = _FakeClient()
    payload = {
        "jsonrpc": "2.0",
        "id": 99,
        "method": "tools/call",
        "params": {
            "name": "toolbox.call",
            "arguments": {
                "tool_name": "resource.upsert",
                "run_id": 7,
                "arguments": {
                    "project_id": 1,
                    "plugin_slug": "core",
                    "resource_key": "learning",
                    "data_json": {"body": "ok"},
                },
            },
        },
    }

    response = proxy.handle(client, payload=payload, line=json.dumps(payload), request_id=99)
    structured = _structured(response)

    assert structured["tool"] == "resource.upsert"
    assert structured["arguments"]["run_token"] == "tok"
    assert [call["method"] for call in client.calls] == ["tools/list", "tools/call"]


def test_bridge_proxy_does_not_inject_step_token_for_setup_tool() -> None:
    proxy = AgentBridgeProxy(url="http://daemon/mcp", headers={})
    client = _FakeClient()
    payload = {
        "jsonrpc": "2.0",
        "id": 101,
        "method": "tools/call",
        "params": {
            "name": "toolbox.call",
            "arguments": {
                "tool_name": "budget.set",
                "run_id": 7,
                "arguments": {
                    "project_id": 1,
                    "kind": "firecrawl",
                    "monthly_budget_usd": 25.0,
                },
            },
        },
    }

    response = proxy.handle(client, payload=payload, line=json.dumps(payload), request_id=101)
    structured = _structured(response)

    assert structured["tool"] == "budget.set"
    assert structured["arguments"] == {
        "project_id": 1,
        "kind": "firecrawl",
        "monthly_budget_usd": 25.0,
    }


def test_bridge_proxy_denied_run_plan_tool_returns_repair_steps() -> None:
    proxy = AgentBridgeProxy(url="http://daemon/mcp", headers={})
    client = _FakeClient()
    payload = {
        "jsonrpc": "2.0",
        "id": 104,
        "method": "tools/call",
        "params": {
            "name": "toolbox.call",
            "arguments": {
                "tool_name": "resource.upsert",
                "arguments": {
                    "project_id": 1,
                    "plugin_slug": "core",
                    "resource_key": "learning",
                    "data_json": {"body": "ok"},
                },
            },
        },
    }

    response = proxy.handle(client, payload=payload, line=json.dumps(payload), request_id=104)
    envelope = json.loads(response)
    data = envelope["result"]["structuredContent"]["data"]

    assert envelope["result"]["isError"] is True
    assert data["reason"] == "run_plan_step_grant_required"
    assert "runPlan.start" in data["repair"]["steps"][1]
    assert "run_id" in data["repair"]["retry_arguments"]


def test_bridge_proxy_refreshes_stale_toolbox_catalog_once() -> None:
    proxy = AgentBridgeProxy(url="http://daemon/mcp", headers={})
    client = _RefreshingCatalogClient()
    payload = {
        "jsonrpc": "2.0",
        "id": 103,
        "method": "tools/call",
        "params": {
            "name": "toolbox.call",
            "arguments": {
                "tool_name": "agentPreset.resolveForWorkflow",
                "arguments": {"workflow_key": "engineering.tracked-delivery"},
            },
        },
    }

    response = proxy.handle(client, payload=payload, line=json.dumps(payload), request_id=103)
    structured = _structured(response)

    assert structured["tool"] == "agentPreset.resolveForWorkflow"
    assert structured["arguments"] == {"workflow_key": "engineering.tracked-delivery"}
    assert [call["method"] for call in client.calls] == [
        "tools/list",
        "tools/list",
        "tools/call",
    ]


def test_bridge_proxy_injects_run_plan_token_for_granted_tool() -> None:
    proxy = AgentBridgeProxy(url="http://daemon/mcp", headers={})
    proxy.tokens_by_run[9] = "tok-plan"
    proxy.plans_by_run[9] = 3
    client = _FakeClient()
    payload = {
        "jsonrpc": "2.0",
        "id": 102,
        "method": "tools/call",
        "params": {
            "name": "toolbox.call",
            "arguments": {
                "tool_name": "resource.upsert",
                "run_id": 9,
                "arguments": {
                    "project_id": 1,
                    "plugin_slug": "core",
                    "resource_key": "learning",
                    "data_json": {"body": "ok"},
                },
            },
        },
    }

    response = proxy.handle(client, payload=payload, line=json.dumps(payload), request_id=102)
    structured = _structured(response)

    assert structured["tool"] == "resource.upsert"
    assert structured["arguments"]["run_token"] == "tok-plan"


def test_bridge_proxy_rejects_hidden_direct_tool_calls() -> None:
    proxy = AgentBridgeProxy(url="http://daemon/mcp", headers={})
    client = _FakeClient()
    payload = {
        "jsonrpc": "2.0",
        "id": 100,
        "method": "tools/call",
        "params": {
            "name": "learning.update",
            "arguments": {"project_id": 1, "learning_id": 2, "body": "ok"},
        },
    }

    response = proxy.handle(client, payload=payload, line=json.dumps(payload), request_id=100)
    envelope = json.loads(response)

    assert envelope["result"]["isError"] is True
    assert envelope["result"]["structuredContent"]["code"] == -32007
    assert client.calls == []
