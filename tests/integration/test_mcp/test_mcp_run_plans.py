"""MCP tests for StackOS runPlan.* tools."""

from __future__ import annotations

from .conftest import MCPClient


def _plan_json() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "ops.memory-review.run",
        "title": "Memory Review",
        "steps": [{"id": "review", "title": "Review memory"}],
        "metadata": {"credential_ref": "cred_abc"},
    }


def test_run_plan_create_start_and_step_with_run_token(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    validation = mcp_client.call_tool_structured(
        "runPlan.validate",
        {"run_plan_json": _plan_json()},
    )
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _plan_json()},
    )
    run_plan_id = created["data"]["id"]
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": run_plan_id},
    )
    run_token = started["data"]["run_token"]

    denied = mcp_client.call_tool_error(
        "runPlan.claimStep",
        {"run_plan_id": run_plan_id, "step_id": "review"},
    )
    update_denied = mcp_client.call_tool_error(
        "runPlan.update",
        {
            "run_plan_id": run_plan_id,
            "metadata_json": {"attempt": "self-approval should be denied"},
            "run_token": run_token,
        },
    )
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {"run_plan_id": run_plan_id, "step_id": "review", "run_token": run_token},
    )
    completed = mcp_client.call_tool_structured(
        "runPlan.recordStep",
        {
            "run_plan_id": run_plan_id,
            "step_id": "review",
            "status": "success",
            "result_json": {"summary": "ok"},
            "run_token": run_token,
        },
    )

    assert validation["valid"] is True
    assert started["data"]["run_id"] > 0
    assert denied["code"] == -32007
    assert update_denied["code"] == -32007
    assert claimed["data"]["status"] == "running"
    assert completed["data"]["status"] == "completed"


def test_run_plan_token_cannot_mutate_another_plan(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    first = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _plan_json()},
    )
    second_plan = _plan_json()
    second_plan["key"] = "ops.memory-review.second"
    second = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": second_plan},
    )
    first_started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": first["data"]["id"]},
    )
    second_started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": second["data"]["id"]},
    )

    cross_claim = mcp_client.call_tool_error(
        "runPlan.claimStep",
        {
            "run_plan_id": second["data"]["id"],
            "step_id": "review",
            "run_token": first_started["data"]["run_token"],
        },
    )
    valid_claim = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": second["data"]["id"],
            "step_id": "review",
            "run_token": second_started["data"]["run_token"],
        },
    )
    cross_record = mcp_client.call_tool_error(
        "runPlan.recordStep",
        {
            "run_plan_id": second["data"]["id"],
            "step_id": "review",
            "status": "success",
            "run_token": first_started["data"]["run_token"],
        },
    )

    assert cross_claim["code"] == -32008
    assert valid_claim["data"]["status"] == "running"
    assert cross_record["code"] == -32008


def test_run_plan_start_does_not_replay_run_token(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _plan_json()},
    )
    run_plan_id = created["data"]["id"]
    first = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": run_plan_id},
    )
    second = mcp_client.call_tool_error(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": run_plan_id},
    )

    assert first["data"]["run_token"]
    assert second["code"] == -32008
    assert "run_token" not in str(second)


def test_run_plan_create_from_template_and_list(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "template_key": "core.project-memory-review"},
    )
    page = mcp_client.call_tool_structured("runPlan.list", {"project_id": project_id})
    fetched = mcp_client.call_tool_structured(
        "runPlan.get",
        {"run_plan_id": created["data"]["id"]},
    )

    assert created["data"]["template_key"] == "core.project-memory-review"
    assert any(item["id"] == created["data"]["id"] for item in page["items"])
    assert fetched["template_snapshot_json"]["key"] == "core.project-memory-review"


def test_run_plan_validate_rejects_secrets(mcp_client: MCPClient) -> None:
    plan = _plan_json()
    plan["metadata"] = {"api_key": "real-secret"}

    validation = mcp_client.call_tool_structured(
        "runPlan.validate",
        {"run_plan_json": plan},
    )

    assert validation["valid"] is False
    assert "must not contain secrets" in validation["errors"][0]["message"]
