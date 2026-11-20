"""Recorded diagnostics use normal operation and active-step grant boundaries."""

from tests.helpers.internal_diagnostics import STAGES, inputs, receipt, workflow

from .conftest import MCPClient


def test_runtime_template_save_and_incident_index_grants(
    mcp_client: MCPClient, seeded_project: dict
) -> None:
    project_id = seeded_project["data"]["id"]
    saved = mcp_client.call_tool_structured(
        "workflowTemplate.save",
        {"project_id": project_id, "template_json": workflow(), "source": "project"},
    )
    assert saved["data"]["summary"]["source"] == "project"
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {
            "project_id": project_id,
            "workflow_key": "support.internal-diagnostics",
            "inputs_json": inputs(project_id),
        },
    )
    plan_id = created["data"]["id"]
    started = mcp_client.call_tool_structured(
        "runPlan.start", {"project_id": project_id, "run_plan_id": plan_id}
    )
    controller = started["data"]["run_token"]
    index_args = {
        "project_id": project_id,
        "plugin_slug": "support",
        "resource_key": "support-investigation",
        "external_id": "launch-handshake-2026-10-23",
        "data_json": {"title": "Recorded incident", "summary": "Unresolved", "status": "blocked"},
        "run_token": controller,
        "response_mode": "raw",
    }
    assert mcp_client.call_tool_error("resource.upsert", index_args)["code"] == -32007
    record_id = None
    for stage, key in STAGES:
        claimed = mcp_client.call_tool_structured(
            "runPlan.claimStep",
            {"run_plan_id": plan_id, "step_id": stage, "run_token": controller},
        )
        if stage in {"collect-classify", "verify-escalate"}:
            assert "resource.upsert" in claimed["data"]["allowed_tools"]
            written = mcp_client.call_tool_structured("resource.upsert", index_args)
            if record_id is not None:
                assert written["data"]["id"] == record_id
            record_id = written["data"]["id"]
        else:
            assert mcp_client.call_tool_error("resource.upsert", index_args)["code"] == -32007
        mcp_client.call_tool_structured(
            "runPlan.recordStep",
            {
                "run_plan_id": plan_id,
                "step_id": stage,
                "status": "success",
                "result_json": {key: receipt(stage, project_id, plan_id)},
                "run_token": controller,
            },
        )
    assert record_id is not None
    assert mcp_client.call_tool_error("resource.upsert", index_args)["code"] == -32007
