"""Finance domain contracts are console-only, never financial MCP primitives."""

from __future__ import annotations

import pytest

from stackos.operations.registry import build_operation_registry
from stackos.repositories.base import NotFoundError

NAMES = (
    "financeWorkspace.select",
    "financeWorkspace.revoke",
    "financeWorkspace.status",
    "finance.query",
    "finance.record.get",
    "finance.record.preview",
    "finance.record.commit",
    "finance.evidence.read",
    "finance.report.render",
)


@pytest.mark.parametrize("name", NAMES)
def test_finance_registry_declares_console_authority_and_mcp_denial(name):
    registry = build_operation_registry()
    spec = registry.get(name)
    assert spec.surfaces.rest.browser_safe and spec.surfaces.cli.enabled
    assert not spec.surfaces.mcp.enabled
    with pytest.raises(NotFoundError):
        registry.get(name, surface="mcp")
    assert spec.surfaces.rest.path == f"/api/v1/operations/{name}/call"
    assert spec.grant_policy == (
        "local-admin-console-read" if spec.read_only else "local-admin-console-write"
    )
    assert spec.idempotency_policy == ("dispatcher" if spec.read_only else "handler")
    assert spec.describe_out().idempotency_policy == spec.idempotency_policy
    assert "ack" not in spec.effective_response_policy.allowed_modes


def test_finance_mcp_discovery_and_execution_are_unavailable(mcp_client, seeded_project):
    mcp = mcp_client
    project_id = seeded_project["data"]["id"]
    names = {row["name"] for row in mcp.list_tools()}
    assert names.isdisjoint(NAMES)
    for name in NAMES:
        error = mcp.call_tool_error(
            name, {"project_id": project_id, "run_token": "synthetic-invalid"}
        )
        assert error


def test_raw_only_finance_evidence_and_report_policies():
    registry = build_operation_registry()
    for name in ("finance.evidence.read", "finance.report.render"):
        spec = registry.get(name)
        assert spec.effective_response_policy.allowed_modes == ("raw",)
        assert spec.effective_response_policy.default_mode == "raw"
