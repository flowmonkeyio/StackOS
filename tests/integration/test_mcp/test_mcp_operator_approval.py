"""Record existing operator decisions through canonical MCP approval state."""

import pytest

from .conftest import MCPClient


def _approval_run(mcp: MCPClient, project_id: int) -> tuple[int, str]:
    created = mcp.call_tool_structured(
        "runPlan.create",
        {
            "project_id": project_id,
            "run_plan_json": {
                "schema_version": "stackos.run-plan.v1",
                "key": "operator-decision.run",
                "title": "Record operator decision",
                "approvals": [{"key": "owner-release", "title": "Owner release"}],
                "steps": [
                    {"id": "release", "title": "Release", "approval_refs": ["owner-release"]}
                ],
            },
        },
    )
    plan_id = created["data"]["id"]
    started = mcp.call_tool_structured(
        "runPlan.start",
        {
            "project_id": project_id,
            "run_plan_id": plan_id,
        },
    )
    return plan_id, started["data"]["run_token"]


def _get(mcp: MCPClient, plan_id: int, project_id: int) -> dict:
    return mcp.call_tool_structured(
        "runPlan.get",
        {
            "run_plan_id": plan_id,
            "project_id": project_id,
            "response_mode": "raw",
        },
    )


@pytest.mark.parametrize("controller", [True, False])
def test_existing_operator_approval_can_be_recorded_by_mcp_before_claim(
    mcp_client: MCPClient, seeded_project: dict, controller: bool
) -> None:
    project_id = seeded_project["data"]["id"]
    plan_id, token = _approval_run(mcp_client, project_id)
    denied = mcp_client.call_tool_error(
        "runPlan.claimStep",
        {
            "run_plan_id": plan_id,
            "step_id": "release",
            "run_token": token,
        },
    )
    assert denied["data"]["approval_keys"] == ["owner-release"]
    decision = {
        "source": "operator-conversation",
        "evidence_ref": "operator-decision:fixture",
        "operator_decided_at": "2026-09-23T09:00:00Z",
    }
    scope = {"run_token": token} if controller else {"project_id": project_id}
    recorded = mcp_client.call_tool_structured(
        "runPlan.update",
        {
            **scope,
            "run_plan_id": plan_id,
            "approval_key": "owner-release",
            "approval_status": "approved",
            "decided_by": "operator:fixture",
            "decision_json": decision,
            "response_mode": "raw",
        },
    )
    approval = recorded["data"]["approval_requests"][0]
    assert approval["status"] == "approved"
    assert approval["decided_by"] == "operator:fixture"
    assert approval["decision_json"] == decision
    assert approval["decided_at"]
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": plan_id,
            "step_id": "release",
            "run_token": token,
        },
    )
    assert claimed["data"]["status"] == "running"
    assert _get(mcp_client, plan_id, project_id)["approval_requests"][0] == approval


def test_controller_omitted_project_cannot_commit_foreign_approval_or_metadata(
    mcp_client: MCPClient, seeded_project: dict
) -> None:
    project_id = seeded_project["data"]["id"]
    _, token = _approval_run(mcp_client, project_id)
    foreign = mcp_client.call_tool_structured(
        "project.create",
        {
            "name": "Other owner",
            "slug": "other-owner",
            "domain": "other.example",
            "locale": "en-US",
        },
    )["data"]["id"]
    foreign_plan, _ = _approval_run(mcp_client, foreign)
    before = _get(mcp_client, foreign_plan, foreign)
    denied = mcp_client.call_tool_error(
        "runPlan.update",
        {
            "run_plan_id": foreign_plan,
            "run_token": token,
            "approval_key": "owner-release",
            "approval_status": "approved",
            "decided_by": "operator:fixture",
            "metadata_json": {"must_not_commit": True},
        },
    )
    assert denied["code"] == -32004
    assert _get(mcp_client, foreign_plan, foreign) == before


def test_unscoped_daemon_mcp_cannot_infer_approval_project_from_target(
    mcp_client: MCPClient, seeded_project: dict
) -> None:
    project_id = seeded_project["data"]["id"]
    plan_id, _ = _approval_run(mcp_client, project_id)
    before = _get(mcp_client, plan_id, project_id)
    denied = mcp_client.call_tool_error(
        "runPlan.update",
        {
            "run_plan_id": plan_id,
            "approval_key": "owner-release",
            "approval_status": "approved",
        },
    )
    assert denied["code"] == -32602
    assert "requires project scope" in str(denied)
    assert _get(mcp_client, plan_id, project_id) == before


def test_rejected_gate_and_status_transitions_are_preserved(
    mcp_client: MCPClient, seeded_project: dict
) -> None:
    project_id = seeded_project["data"]["id"]
    plan_id, token = _approval_run(mcp_client, project_id)
    mcp_client.call_tool_structured(
        "runPlan.update",
        {
            "run_plan_id": plan_id,
            "run_token": token,
            "approval_key": "owner-release",
            "approval_status": "rejected",
            "decided_by": "operator:fixture",
        },
    )
    denied = mcp_client.call_tool_error(
        "runPlan.claimStep",
        {
            "run_plan_id": plan_id,
            "step_id": "release",
            "run_token": token,
        },
    )
    assert denied["data"]["approval_keys"] == ["owner-release"]
    transition = mcp_client.call_tool_error(
        "runPlan.update",
        {
            "run_plan_id": plan_id,
            "run_token": token,
            "approval_key": "owner-release",
            "approval_status": "approved",
        },
    )
    assert transition["code"] == -32008
    assert _get(mcp_client, plan_id, project_id)["approval_requests"][0]["status"] == "rejected"


def test_recording_operator_decision_does_not_unlock_unrelated_admin_tools(
    mcp_client: MCPClient, seeded_project: dict
) -> None:
    project_id = seeded_project["data"]["id"]
    _, token = _approval_run(mcp_client, project_id)
    for tool, arguments in (
        ("plugin.disable", {"project_id": project_id, "plugin_slug": "finance"}),
        ("account.revoke", {"credential_ref": "cred_fixture"}),
        ("connection.detach", {"project_id": project_id, "credential_ref": "cred_fixture"}),
    ):
        denied = mcp_client.call_tool_error(
            tool,
            {
                **arguments,
                "run_token": token,
            },
        )
        assert denied["code"] == -32007
