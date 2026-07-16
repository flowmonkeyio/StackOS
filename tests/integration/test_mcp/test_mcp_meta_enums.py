"""``meta.enums`` returns StackOS core enums and transitions."""

from __future__ import annotations

from .conftest import MCPClient


def test_meta_enums_returns_core_payload(mcp_client: MCPClient) -> None:
    payload = mcp_client.call_tool_structured("meta.enums", {"response_mode": "raw"})

    assert {
        "runs_status",
        "runs_kind",
        "run_steps_status",
        "run_plans_status",
        "run_plan_steps_status",
        "approval_requests_status",
        "action_calls_status",
        "plugins_source",
        "allowed_transitions",
    } <= set(payload)


def test_meta_enums_status_values_are_strings(mcp_client: MCPClient) -> None:
    payload = mcp_client.call_tool_structured("meta.enums", {"response_mode": "raw"})

    for key in ("runs_status", "run_plans_status", "action_calls_status"):
        assert all(isinstance(value, str) for value in payload[key])


def test_meta_enums_transitions_well_formed(mcp_client: MCPClient) -> None:
    payload = mcp_client.call_tool_structured("meta.enums", {"response_mode": "raw"})
    transitions = payload["allowed_transitions"]

    for table in ("runs", "run_plans", "run_plan_steps", "approval_requests"):
        assert table in transitions
        for state, allowed in transitions[table].items():
            assert isinstance(state, str)
            assert isinstance(allowed, list)
