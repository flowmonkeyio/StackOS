"""Plugin MCP bridge surface tests."""

from __future__ import annotations

import json
from typing import Any

from content_stack.mcp.bridge import (
    _AGENT_ADMIN_GATED_TOOL_NAMES,
    _AGENT_BASE_TOOLBOX_NAMES,
    _AGENT_SETUP_TOOLBOX_NAMES,
    _AGENT_VISIBLE_TOOL_NAMES,
    _AGENT_VISIBLE_TOOL_ORDER,
    AgentBridgeProxy,
    _bridge_cache_step_context,
    _bridge_filter_tool_list_response,
    _bridge_toolbox_describe,
)
from content_stack.mcp.contract import verb_is_mutating
from content_stack.mcp.permissions import SKILL_TOOL_GRANTS, SYSTEM_SKILL
from content_stack.mcp.server import ToolRegistry
from content_stack.mcp.tools import register_all


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
                    "result": {"tools": [_tool("article.get"), _tool("integration.set")]},
                }
            )
        tool_name = body["params"]["name"]
        if tool_name == "procedure.currentStep":
            return _Response(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "structuredContent": {
                            "run_id": 7,
                            "run_token": "tok",
                            "current_step": {"allowed_tools": ["article.get"]},
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
        _tool("article.get"),
        _tool("integration.set"),
        _tool("plugin.enable"),
        _tool("resource.upsert"),
        _tool("artifact.create"),
        _tool("cost.queryProject"),
        _tool("dataforseo.serp"),
    ]
    daemon_response = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": daemon_tools}})

    filtered = json.loads(_bridge_filter_tool_list_response(daemon_response))
    names = [tool["name"] for tool in filtered["result"]["tools"]]

    assert names[: len(_AGENT_VISIBLE_TOOL_ORDER)] == list(_AGENT_VISIBLE_TOOL_ORDER)
    assert "toolbox.describe" in names
    assert "toolbox.call" in names
    assert "article.get" not in names
    assert "integration.set" not in names
    assert "plugin.enable" not in names
    assert "resource.upsert" not in names
    assert "artifact.create" not in names
    assert "cost.queryProject" not in names
    assert "dataforseo.serp" not in names


def test_bridge_toolbox_describes_setup_and_current_step_tools_only() -> None:
    catalog = {
        name: _tool(name)
        for name in [
            "integration.set",
            "article.get",
            "dataforseo.serp",
            "cost.queryProject",
            "openaiImages.generate",
        ]
    }
    allowed_by_run = {7: {"article.get", "dataforseo.serp"}}

    response = _bridge_toolbox_describe(
        42,
        catalog=catalog,
        arguments={
            "run_id": 7,
            "tool_names": [
                "integration.set",
                "article.get",
                "dataforseo.serp",
                "cost.queryProject",
                "openaiImages.generate",
                "missing",
            ],
        },
        run_id=7,
        allowed_by_run=allowed_by_run,
    )
    payload = _structured(response)

    assert [tool["name"] for tool in payload["described_tools"]] == [
        "integration.set",
        "article.get",
        "dataforseo.serp",
        "cost.queryProject",
    ]
    assert payload["current_step_tool_names"] == ["article.get", "dataforseo.serp"]
    assert payload["denied_tool_names"] == ["openaiImages.generate"]
    assert payload["unknown_tool_names"] == ["missing"]


def test_bridge_caches_run_token_and_step_grants() -> None:
    response = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "structuredContent": {
                    "run_id": 7,
                    "run_token": "tok",
                    "current_step": {
                        "allowed_tools": ["article.get", "voice.get", 123, ""],
                    },
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
    assert allowed_by_run == {7: {"article.get", "voice.get"}}


def test_bridge_base_toolbox_includes_product_state_but_not_vendor_surface() -> None:
    assert "plugin.list" in _AGENT_VISIBLE_TOOL_NAMES
    assert "catalog.describe" in _AGENT_VISIBLE_TOOL_NAMES
    assert "capability.list" in _AGENT_VISIBLE_TOOL_NAMES
    assert "provider.describe" in _AGENT_VISIBLE_TOOL_NAMES
    assert "resource.query" in _AGENT_VISIBLE_TOOL_NAMES
    assert "artifact.get" in _AGENT_VISIBLE_TOOL_NAMES
    assert "integration.set" in _AGENT_BASE_TOOLBOX_NAMES
    assert "article.setDraft" in _AGENT_BASE_TOOLBOX_NAMES
    assert "cost.queryProject" in _AGENT_BASE_TOOLBOX_NAMES
    assert "publish.recordPublish" in _AGENT_BASE_TOOLBOX_NAMES
    assert "plugin.enable" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "plugin.disable" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "resource.upsert" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "artifact.create" not in _AGENT_BASE_TOOLBOX_NAMES
    assert {
        "artifact.create",
        "plugin.enable",
        "plugin.disable",
        "resource.upsert",
    } == _AGENT_ADMIN_GATED_TOOL_NAMES
    assert "dataforseo.serp" not in _AGENT_BASE_TOOLBOX_NAMES
    assert "openaiImages.generate" not in _AGENT_BASE_TOOLBOX_NAMES


def test_bridge_setup_surface_covers_observer_ui_mutations() -> None:
    observer_ui_mutations = {
        "procedure.run",
        "procedure.resume",
        "procedure.fork",
        "procedure.executeProgrammaticStep",
        "topic.create",
        "topic.bulkCreate",
        "article.create",
        "article.bulkCreate",
        "article.setBrief",
        "article.setOutline",
        "article.setDraft",
        "article.setEdited",
        "article.markDrafted",
        "article.markEeatPassed",
        "article.markPublished",
        "article.markRefreshDue",
        "article.createVersion",
        "asset.create",
        "asset.update",
        "asset.remove",
        "budget.set",
        "budget.update",
        "cluster.create",
        "project.create",
        "project.update",
        "project.activate",
        "project.setActive",
        "compliance.add",
        "compliance.update",
        "compliance.remove",
        "drift.snapshot",
        "drift.diff",
        "eeat.bulkSet",
        "eeat.toggle",
        "gsc.rollup",
        "gsc.bulkIngest",
        "gscOauth.start",
        "integration.set",
        "integration.test",
        "integration.remove",
        "interlink.apply",
        "interlink.bulkApply",
        "interlink.dismiss",
        "interlink.repair",
        "interlink.suggest",
        "publish.recordExternal",
        "publish.recordPublish",
        "publish.setCanonical",
        "redirect.create",
        "schedule.remove",
        "schedule.set",
        "schedule.toggle",
        "schema.set",
        "schema.validate",
        "source.add",
        "source.update",
        "target.add",
        "target.update",
        "target.remove",
        "target.setPrimary",
        "topic.approve",
        "topic.reject",
        "topic.bulkUpdateStatus",
        "voice.set",
        "voice.setActive",
    }

    assert observer_ui_mutations <= _AGENT_BASE_TOOLBOX_NAMES


def test_bridge_agent_operation_surface_matches_registered_daemon_tools() -> None:
    registry = ToolRegistry()
    register_all(registry)
    registered = set(registry._tools)

    assert registered >= _AGENT_BASE_TOOLBOX_NAMES


def test_bridge_system_grant_matches_agent_operation_surface() -> None:
    assert SKILL_TOOL_GRANTS[SYSTEM_SKILL] == _AGENT_BASE_TOOLBOX_NAMES


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
        and name not in _AGENT_ADMIN_GATED_TOOL_NAMES
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
    client = _FakeClient()
    payload = {
        "jsonrpc": "2.0",
        "id": 99,
        "method": "tools/call",
        "params": {
            "name": "toolbox.call",
            "arguments": {
                "tool_name": "article.get",
                "run_id": 7,
                "arguments": {"article_id": 12},
            },
        },
    }

    response = proxy.handle(client, payload=payload, line=json.dumps(payload), request_id=99)
    structured = _structured(response)

    assert structured["tool"] == "article.get"
    assert structured["arguments"] == {"article_id": 12, "run_token": "tok"}
    assert [call["method"] for call in client.calls] == ["tools/list", "tools/call", "tools/call"]


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
                "tool_name": "integration.set",
                "run_id": 7,
                "arguments": {"project_id": 1, "kind": "wordpress", "payload": {}},
            },
        },
    }

    response = proxy.handle(client, payload=payload, line=json.dumps(payload), request_id=101)
    structured = _structured(response)

    assert structured["tool"] == "integration.set"
    assert structured["arguments"] == {
        "project_id": 1,
        "kind": "wordpress",
        "payload": {},
    }


def test_bridge_proxy_rejects_hidden_direct_tool_calls() -> None:
    proxy = AgentBridgeProxy(url="http://daemon/mcp", headers={})
    client = _FakeClient()
    payload = {
        "jsonrpc": "2.0",
        "id": 100,
        "method": "tools/call",
        "params": {"name": "article.get", "arguments": {"article_id": 12}},
    }

    response = proxy.handle(client, payload=payload, line=json.dumps(payload), request_id=100)
    envelope = json.loads(response)

    assert envelope["result"]["isError"] is True
    assert envelope["result"]["structuredContent"]["code"] == -32007
    assert client.calls == []
