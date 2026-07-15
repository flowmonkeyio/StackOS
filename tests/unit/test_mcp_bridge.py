"""Plugin MCP bridge surface tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from stackos.mcp.bridge import (
    _AGENT_ADMIN_GATED_TOOL_NAMES,
    _AGENT_BASE_TOOLBOX_NAMES,
    _AGENT_GATED_TOOL_NAMES,
    _AGENT_GLOBAL_DISCOVERY_TOOL_NAMES,
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
from stackos.mcp.bridge.catalog import _bridge_toolbox_specs
from stackos.mcp.contract import verb_is_mutating
from stackos.mcp.permissions import SKILL_TOOL_GRANTS, SYSTEM_SKILL
from stackos.mcp.server import STACKOS_MCP_INSTRUCTIONS, ToolRegistry, _to_tool
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


def test_stackos_mcp_instructions_match_bridge_project_scope_contract() -> None:
    assert "inject the current `project_id`" in STACKOS_MCP_INSTRUCTIONS
    assert "omit injected fields" in STACKOS_MCP_INSTRUCTIONS
    assert "All project-scoped tools require explicit `project_id`" not in (
        STACKOS_MCP_INSTRUCTIONS
    )


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
                            _tool(
                                "project.delete",
                                operation_name="project.delete",
                                grant_policy="local-admin-project-write",
                            ),
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


class _RunPlanControllerRefreshClient:
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
                            _tool("run.get"),
                            _tool("runPlan.claimStep"),
                            _tool("runPlan.get"),
                            _tool("runPlan.list"),
                            _tool("runPlan.recordStep"),
                            _tool("resource.upsert"),
                        ]
                    },
                }
            )
        tool_name = body["params"]["name"]
        arguments = body["params"]["arguments"]
        if tool_name == "run.get":
            if arguments.get("response_mode") != "raw":
                return _Response(
                    {
                        "jsonrpc": "2.0",
                        "id": body["id"],
                        "result": {
                            "structuredContent": {
                                "ok": True,
                                "operation": "run.get",
                                "status": "running",
                                "project_id": 2,
                                "run_id": 78,
                                "data": {"id": 78, "status": "running"},
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
                            "id": 78,
                            "project_id": 2,
                            "status": "running",
                            "client_session_id": "tok-controller",
                            "metadata_json": {
                                "skill_name": "stackos/run-plan-controller",
                                "run_plan_id": 82,
                            },
                        }
                    },
                }
            )
        if tool_name == "runPlan.get":
            assert arguments.get("response_mode") == "raw"
            return _Response(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "structuredContent": {
                            "data": {
                                "id": 82,
                                "run_id": 78,
                                "status": "started",
                                "steps": [
                                    {
                                        "step_id": "verify-delivery",
                                        "status": "pending",
                                        "allowed_tools": [],
                                    }
                                ],
                            },
                            "run_id": 78,
                            "project_id": 2,
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
                        "arguments": arguments,
                    }
                },
            }
        )


class _RunPlanControllerListFallbackClient(_RunPlanControllerRefreshClient):
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
                            _tool("run.get"),
                            _tool("runPlan.claimStep"),
                            _tool("runPlan.get"),
                            _tool("runPlan.list"),
                            _tool("runPlan.recordStep"),
                            _tool("resource.upsert"),
                        ]
                    },
                }
            )
        tool_name = body["params"]["name"]
        arguments = body["params"]["arguments"]
        if tool_name == "run.get":
            assert arguments.get("response_mode") == "raw"
            return _Response(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "structuredContent": {
                            "id": 78,
                            "project_id": 2,
                            "status": "running",
                            "metadata_json": {"source": "compact-resume"},
                        }
                    },
                }
            )
        if tool_name == "runPlan.list":
            assert arguments.get("response_mode") == "raw"
            return _Response(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "structuredContent": {
                            "data": {"items": [{"id": 82, "run_id": 78}]},
                            "run_id": 78,
                            "project_id": 2,
                        }
                    },
                }
            )
        if tool_name == "runPlan.get":
            assert arguments.get("response_mode") == "raw"
            return _Response(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "structuredContent": {
                            "data": {
                                "id": 82,
                                "run_id": 78,
                                "run_token": "tok-step",
                                "status": "started",
                                "steps": [
                                    {
                                        "step_id": "verify-delivery",
                                        "status": "running",
                                        "allowed_tools": ["resource.upsert"],
                                    }
                                ],
                            },
                            "run_id": 78,
                            "project_id": 2,
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
                        "arguments": arguments,
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
    assert statuses["artifact.create"]["reason_code"] == "not_granted_to_active_step"
    assert statuses["project.update"]["reason_code"] == "local_admin_required"
    assert statuses["project.update"]["grant_policy"] == "local-admin-project-write"
    assert statuses["resource.upsert"]["reason_code"] == "active_step_granted"
    assert statuses["action.execute"]["operation"]["name"] == "action.execute"
    assert statuses["missing"]["reason_code"] == "unknown_tool"


def test_bridge_toolbox_describe_without_names_returns_discovery_recipe() -> None:
    catalog = {
        "operation.list": _tool("operation.list", operation_name="operation.list"),
        "operation.describe": _tool("operation.describe", operation_name="operation.describe"),
        "tracker.status": _tool("tracker.status", operation_name="tracker.status"),
        "runPlan.create": _tool("runPlan.create", operation_name="runPlan.create"),
    }

    response = _bridge_toolbox_describe(
        42,
        catalog=catalog,
        arguments={},
        run_id=None,
        allowed_by_run={},
    )
    payload = _structured(response)

    assert payload["described_tools"] == []
    assert "available_tool_names" not in payload
    assert payload["discovery"]["next_calls"][0] == {
        "tool": "toolbox.call",
        "arguments": {
            "tool_name": "operation.list",
            "arguments": {
                "surface": "mcp",
                "mode": "grouped",
                "response_mode": "compact",
            },
        },
    }
    assert payload["discovery"]["next_calls"][1] == {
        "tool": "toolbox.describe",
        "arguments": {"tool_names": ["tracker.status"]},
    }
    assert "inputSchema" not in json.dumps(payload)


def test_bridge_toolbox_describe_rejects_broad_schema_dump_without_exact_names() -> None:
    catalog = {
        "operation.list": _tool("operation.list", operation_name="operation.list"),
        "tracker.status": _tool("tracker.status", operation_name="tracker.status"),
    }

    response = _bridge_toolbox_describe(
        42,
        catalog=catalog,
        arguments={"include_schemas": True},
        run_id=None,
        allowed_by_run={},
    )
    envelope = json.loads(response)
    payload = envelope["result"]["structuredContent"]

    assert envelope["result"]["isError"] is True
    assert payload["described_tools"] == []
    assert payload["error"]["code"] == "broad_schema_dump_requires_tool_names"
    assert payload["error"]["next_actions"][0]["arguments"]["tool_name"] == "operation.list"
    assert "available_tool_names" not in payload
    assert "inputSchema" not in json.dumps(payload)


def test_bridge_toolbox_describe_virtual_schema_matches_scoped_schema_contract() -> None:
    toolbox = next(tool for tool in _bridge_toolbox_specs() if tool["name"] == "toolbox.describe")
    include_description = toolbox["inputSchema"]["properties"]["include_schemas"]["description"]

    assert "tool_names must contain exact tool" in include_description
    assert "Broad schema dumps are rejected" in include_description
    assert "include all" not in include_description


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
    assert "guide.gettingStarted" in _AGENT_SETUP_TOOLBOX_NAMES
    assert "guide.gettingStarted" in _AGENT_GLOBAL_DISCOVERY_TOOL_NAMES
    assert "schema.get" in _AGENT_SETUP_TOOLBOX_NAMES
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
    assert "runPlan.getStep" in _AGENT_SETUP_TOOLBOX_NAMES
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
        "artifact.read",
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
        "executionContext.artifact.read",
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


def test_bridge_leaves_tracker_compaction_to_operation_responses() -> None:
    assert _bridge_compact_structured("tracker.status", {"tracker": {"id": 1}}) is None
    assert _bridge_compact_structured("tracker.brief", {"ticket": {"key": "deliver"}}) is None


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
        "artifact.read",
        "executionContext.artifact.read",
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
    schema_spec = registry.get("schema.get")
    assert list_spec.operation_name == "operation.list"
    assert describe_spec.operation_name == "operation.describe"
    assert schema_spec.operation_name == "schema.get"
    assert _to_tool(list_spec).model_dump(by_alias=True)["_meta"]["operation_name"] == (
        "operation.list"
    )
    assert _to_tool(describe_spec).model_dump(by_alias=True)["_meta"]["operation_name"] == (
        "operation.describe"
    )
    assert _to_tool(schema_spec).model_dump(by_alias=True)["_meta"]["operation_name"] == (
        "schema.get"
    )

    listed = asyncio.run(
        list_spec.handler(
            list_spec.input_model.model_validate({"surface": "mcp"}),
            None,  # type: ignore[arg-type]
            ProgressEmitter(None, None),
        )
    )
    listed_names = {
        operation_name for group in listed.groups for operation_name in group.operation_names
    }
    assert listed.items == []
    assert "operation.list" in listed_names
    assert "operation.describe" in listed_names
    assert "schema.get" in listed_names
    assert "communication.send" in listed_names

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

    schema = asyncio.run(
        schema_spec.handler(
            schema_spec.input_model.model_validate({"schema_ref": "stackos.action-output.v1"}),
            None,  # type: ignore[arg-type]
            ProgressEmitter(None, None),
        )
    )
    assert schema.schema_ref == "stackos.action-output.v1"
    assert schema.schema_data["properties"]["schema_version"]["const"] == (
        "stackos.action-output.v1"
    )


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


def test_bridge_proxy_denied_active_step_tool_reports_not_granted_to_step() -> None:
    proxy = AgentBridgeProxy(url="http://daemon/mcp", headers={})
    proxy.tokens_by_run[7] = "tok"
    proxy.allowed_by_run[7] = {"action.execute"}
    client = _FakeClient()
    payload = {
        "jsonrpc": "2.0",
        "id": 100,
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
                    "data_json": {"body": "blocked by active step grant"},
                },
            },
        },
    }

    response = proxy.handle(client, payload=payload, line=json.dumps(payload), request_id=100)
    envelope = json.loads(response)
    data = envelope["result"]["structuredContent"]["data"]

    assert envelope["result"]["isError"] is True
    assert data["reason"] == "not_granted_to_active_step"
    assert data["active_step_tool_names"] == ["action.execute"]


def test_bridge_proxy_denied_admin_tool_keeps_admin_reason_with_active_step() -> None:
    proxy = AgentBridgeProxy(url="http://daemon/mcp", headers={})
    proxy.tokens_by_run[7] = "tok"
    proxy.allowed_by_run[7] = {"resource.upsert"}
    client = _FakeClient()
    payload = {
        "jsonrpc": "2.0",
        "id": 102,
        "method": "tools/call",
        "params": {
            "name": "toolbox.call",
            "arguments": {
                "tool_name": "project.delete",
                "run_id": 7,
                "arguments": {"project_id": 1},
            },
        },
    }

    response = proxy.handle(client, payload=payload, line=json.dumps(payload), request_id=102)
    envelope = json.loads(response)
    data = envelope["result"]["structuredContent"]["data"]

    assert envelope["result"]["isError"] is True
    assert data["reason"] == "local_admin_required"
    assert data["active_step_tool_names"] == ["resource.upsert"]


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


def test_bridge_proxy_refreshes_controller_run_with_raw_internal_reads() -> None:
    proxy = AgentBridgeProxy(url="http://daemon/mcp", headers={})
    client = _RunPlanControllerRefreshClient()
    describe_payload = {
        "jsonrpc": "2.0",
        "id": 105,
        "method": "tools/call",
        "params": {
            "name": "toolbox.describe",
            "arguments": {
                "run_id": 78,
                "tool_names": ["runPlan.claimStep", "runPlan.recordStep"],
            },
        },
    }

    describe_response = proxy.handle(
        client,
        payload=describe_payload,
        line=json.dumps(describe_payload),
        request_id=105,
    )
    described = _structured(describe_response)

    call_payload = {
        "jsonrpc": "2.0",
        "id": 106,
        "method": "tools/call",
        "params": {
            "name": "toolbox.call",
            "arguments": {
                "run_id": 78,
                "tool_name": "runPlan.claimStep",
                "arguments": {"run_plan_id": 82, "step_id": "verify-delivery"},
            },
        },
    }
    call_response = proxy.handle(
        client,
        payload=call_payload,
        line=json.dumps(call_payload),
        request_id=106,
    )
    called = _structured(call_response)

    statuses = {item["name"]: item for item in described["tool_statuses"]}
    assert described["run_plan_controller_tool_names"] == [
        "runPlan.claimStep",
        "runPlan.recordStep",
    ]
    assert statuses["runPlan.claimStep"]["allowed"] is True
    assert statuses["runPlan.claimStep"]["reason_code"] == "run_plan_controller"
    assert called["tool"] == "runPlan.claimStep"
    assert called["arguments"]["run_token"] == "tok-controller"
    internal_calls = [
        call["params"]["arguments"]
        for call in client.calls
        if call["method"] == "tools/call" and call["params"]["name"] in {"run.get", "runPlan.get"}
    ]
    assert internal_calls[0]["response_mode"] == "raw"
    assert internal_calls[1]["response_mode"] == "raw"


def test_bridge_proxy_refreshes_step_context_from_raw_list_fallback() -> None:
    proxy = AgentBridgeProxy(url="http://daemon/mcp", headers={})
    client = _RunPlanControllerListFallbackClient()
    payload = {
        "jsonrpc": "2.0",
        "id": 107,
        "method": "tools/call",
        "params": {
            "name": "toolbox.describe",
            "arguments": {
                "run_id": 78,
                "tool_names": ["resource.upsert"],
            },
        },
    }

    response = proxy.handle(client, payload=payload, line=json.dumps(payload), request_id=107)
    described = _structured(response)

    statuses = {item["name"]: item for item in described["tool_statuses"]}
    assert statuses["resource.upsert"]["allowed"] is True
    assert statuses["resource.upsert"]["reason_code"] == "active_step_granted"
    internal_calls = [
        call["params"]
        for call in client.calls
        if call["method"] == "tools/call"
        and call["params"]["name"] in {"run.get", "runPlan.list", "runPlan.get"}
    ]
    assert [call["name"] for call in internal_calls] == [
        "run.get",
        "runPlan.list",
        "runPlan.get",
    ]
    assert all(call["arguments"].get("response_mode") == "raw" for call in internal_calls)


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
