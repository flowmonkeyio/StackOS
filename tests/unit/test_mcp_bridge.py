"""Plugin MCP bridge surface tests."""

from __future__ import annotations

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
    _bridge_cache_step_context,
    _bridge_compact_profile,
    _bridge_filter_tool_list_response,
    _bridge_toolbox_describe,
)
from stackos.mcp.contract import verb_is_mutating
from stackos.mcp.permissions import SKILL_TOOL_GRANTS, SYSTEM_SKILL
from stackos.mcp.server import ToolRegistry
from stackos.mcp.tools import register_all


def _tool(name: str) -> dict[str, object]:
    return {
        "name": name,
        "description": f"{name} description",
        "inputSchema": {"type": "object"},
        "outputSchema": {"type": "object"},
    }


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
        ]
    }
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
    assert set(payload["denied_tool_names"]) == {
        "artifact.create",
        "auth.start",
        "learning.update",
    }
    assert payload["unknown_tool_names"] == ["integration.test", "missing"]
    assert "admin_gated_tool_names" not in payload


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
    tokens_by_run: dict[int, str] = {}

    _bridge_cache_step_context(
        response,
        allowed_by_run=allowed_by_run,
        tokens_by_run=tokens_by_run,
    )

    assert allowed_by_run == {9: set(_AGENT_STEP_GATED_TOOL_NAMES) | {"resource.upsert"}}


def test_bridge_base_toolbox_includes_product_state_but_not_vendor_surface() -> None:
    assert "plugin.list" in _AGENT_VISIBLE_TOOL_NAMES
    assert "action.describe" in _AGENT_VISIBLE_TOOL_NAMES
    assert "action.validate" in _AGENT_VISIBLE_TOOL_NAMES
    assert "action.run" in _AGENT_VISIBLE_TOOL_NAMES
    assert "agentRequest.list" in _AGENT_VISIBLE_TOOL_NAMES
    assert "agentRequest.get" in _AGENT_VISIBLE_TOOL_NAMES
    assert "agentRequest.claim" in _AGENT_VISIBLE_TOOL_NAMES
    assert "agentRequest.release" in _AGENT_VISIBLE_TOOL_NAMES
    assert "agentRequest.linkRunPlan" in _AGENT_VISIBLE_TOOL_NAMES
    assert "agentRequest.prepareRunPlan" in _AGENT_VISIBLE_TOOL_NAMES
    assert "agentRequest.complete" in _AGENT_VISIBLE_TOOL_NAMES
    assert "agentRequest.ignore" in _AGENT_VISIBLE_TOOL_NAMES
    assert "catalog.describe" in _AGENT_VISIBLE_TOOL_NAMES
    assert "capability.list" in _AGENT_VISIBLE_TOOL_NAMES
    assert "provider.describe" in _AGENT_VISIBLE_TOOL_NAMES
    assert "resource.query" in _AGENT_VISIBLE_TOOL_NAMES
    assert "artifact.get" in _AGENT_VISIBLE_TOOL_NAMES
    assert "auth.status" in _AGENT_VISIBLE_TOOL_NAMES
    assert "auth.test" in _AGENT_VISIBLE_TOOL_NAMES
    assert "communicationProfile.list" in _AGENT_VISIBLE_TOOL_NAMES
    assert "communicationProfile.get" in _AGENT_VISIBLE_TOOL_NAMES
    assert "communicationProfile.upsert" in _AGENT_VISIBLE_TOOL_NAMES
    assert "communicationSurface.list" in _AGENT_VISIBLE_TOOL_NAMES
    assert "communicationSurface.upsert" in _AGENT_VISIBLE_TOOL_NAMES
    assert "communicationContact.list" in _AGENT_VISIBLE_TOOL_NAMES
    assert "communicationContact.upsert" in _AGENT_VISIBLE_TOOL_NAMES
    assert "communicationMembership.list" in _AGENT_VISIBLE_TOOL_NAMES
    assert "communicationMembership.upsert" in _AGENT_VISIBLE_TOOL_NAMES
    assert "communicationTarget.list" in _AGENT_VISIBLE_TOOL_NAMES
    assert "communicationTarget.resolve" in _AGENT_VISIBLE_TOOL_NAMES
    assert "communicationTarget.upsert" in _AGENT_VISIBLE_TOOL_NAMES
    assert "communicationRoute.list" in _AGENT_VISIBLE_TOOL_NAMES
    assert "communicationRoute.upsert" in _AGENT_VISIBLE_TOOL_NAMES
    assert "communicationContext.query" in _AGENT_VISIBLE_TOOL_NAMES
    assert "communicationProfile.list" in _AGENT_VISIBLE_TOOL_NAMES
    assert "communicationProfile.get" in _AGENT_VISIBLE_TOOL_NAMES
    assert "communicationProfile.upsert" in _AGENT_VISIBLE_TOOL_NAMES
    assert "toolProfile.resolve" in _AGENT_VISIBLE_TOOL_NAMES
    assert "localAgentChat.createMessage" in _AGENT_VISIBLE_TOOL_NAMES
    assert "context.query" in _AGENT_VISIBLE_TOOL_NAMES
    assert "context.timeline" in _AGENT_VISIBLE_TOOL_NAMES
    assert "learning.query" in _AGENT_VISIBLE_TOOL_NAMES
    assert "experiment.query" in _AGENT_VISIBLE_TOOL_NAMES
    assert "decision.query" in _AGENT_VISIBLE_TOOL_NAMES
    assert "workflowTemplate.list" in _AGENT_VISIBLE_TOOL_NAMES
    assert "workflowTemplate.describe" in _AGENT_VISIBLE_TOOL_NAMES
    assert "workflowTemplate.validate" in _AGENT_VISIBLE_TOOL_NAMES
    assert "runPlan.create" in _AGENT_VISIBLE_TOOL_NAMES
    assert "runPlan.validate" in _AGENT_VISIBLE_TOOL_NAMES
    assert "runPlan.start" in _AGENT_VISIBLE_TOOL_NAMES
    assert "runPlan.get" in _AGENT_VISIBLE_TOOL_NAMES
    assert "runPlan.list" in _AGENT_VISIBLE_TOOL_NAMES
    assert "runPlan.claimStep" in _AGENT_STEP_GATED_TOOL_NAMES
    assert "runPlan.recordStep" in _AGENT_STEP_GATED_TOOL_NAMES
    assert "runPlan.update" not in _AGENT_STEP_GATED_TOOL_NAMES
    assert "action.execute" in _AGENT_RUN_PLAN_GATED_TOOL_NAMES
    assert "agentRequest.create" in _AGENT_RUN_PLAN_GATED_TOOL_NAMES


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
    assert "integration.set" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "integration.test" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "integration.list" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "integration.remove" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "cost.queryProject" in _AGENT_BASE_TOOLBOX_NAMES
    assert "plugin.enable" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "plugin.disable" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "resource.upsert" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "artifact.create" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "agentRequest.create" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "auth.start" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "auth.revoke" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "project.create" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "project.list" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "project.setActive" not in _AGENT_BASE_TOOLBOX_NAMES
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
    assert "learning.update" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "action.execute" not in _AGENT_BASE_TOOLBOX_NAMES


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


def test_bridge_system_grant_matches_agent_operation_surface() -> None:
    system_tools = SKILL_TOOL_GRANTS[SYSTEM_SKILL]
    assert system_tools >= _AGENT_BASE_TOOLBOX_NAMES
    direct_safe_reads = {"context.query"}
    assert (_AGENT_RUN_PLAN_GATED_TOOL_NAMES - direct_safe_reads).isdisjoint(system_tools)
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
