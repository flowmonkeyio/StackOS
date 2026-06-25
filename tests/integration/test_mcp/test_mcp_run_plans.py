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


def _resource_grant_plan_json(key: str = "ops.resource-write.run") -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": key,
        "title": "Resource Write",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "write-resource",
                    "tool": "resource.upsert",
                    "plugin_slug": "core",
                    "resource_key": "learning",
                }
            ]
        },
        "steps": [
            {
                "id": "write-resource",
                "title": "Write resource",
                "resource_refs": ["core.learning"],
            }
        ],
    }


def _context_query_grant_plan_json(key: str = "ops.context-read.run") -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": key,
        "title": "Context Read",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "read-context",
                    "tool": "context.query",
                    "sources": ["learnings"],
                    "fields": ["statement", "evidence_json"],
                }
            ]
        },
        "steps": [{"id": "read-context", "title": "Read context"}],
    }


def _action_grant_plan_json(key: str = "ops.action-guidance.run") -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": key,
        "title": "Action Guidance",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "call-action",
                    "tool": "action.execute",
                    "action_refs": ["core.catalog.describe"],
                }
            ]
        },
        "steps": [
            {
                "id": "call-action",
                "title": "Call action",
                "action_refs": ["core.catalog.describe"],
            }
        ],
    }


def _create_learning(mcp: MCPClient, project_id: int) -> dict:
    response = mcp.test_client.post(
        f"/api/v1/projects/{project_id}/learnings",
        json={
            "statement": "Creative angle survived with api_key=secret",
            "review_state": "accepted",
            "evidence_json": {"refresh_token": "rt"},
        },
        headers=mcp._headers(),
    )
    response.raise_for_status()
    return response.json()["data"]


def test_run_plan_create_start_and_step_with_run_token(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    validation = mcp_client.call_tool_structured(
        "runPlan.validate",
        {"run_plan_json": _plan_json(), "response_mode": "raw"},
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
    tracker = mcp_client.call_tool_structured(
        "tracker.get",
        {
            "project_id": project_id,
            "task_key": f"workflow-{run_plan_id}",
            "include_graph": False,
            "response_mode": "raw",
        },
    )

    assert validation["valid"] is True
    assert started["data"]["run_id"] > 0
    assert denied["code"] == -32007
    assert update_denied["code"] == -32007
    assert claimed["data"]["status"] == "running"
    assert completed["data"]["status"] == "completed"
    assert tracker["tasks"][0]["status"] == "complete"
    assert tracker["tickets"][0]["status"] == "complete"
    assert tracker["tickets"][0]["lane_key"] == "done"


def test_run_plan_failed_step_marks_tracker_mirror_failed(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
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
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {"run_plan_id": run_plan_id, "step_id": "review", "run_token": run_token},
    )

    failed = mcp_client.call_tool_structured(
        "runPlan.recordStep",
        {
            "run_plan_id": run_plan_id,
            "step_id": "review",
            "status": "failed",
            "error": "contract check failed",
            "run_token": run_token,
        },
    )
    tracker = mcp_client.call_tool_structured(
        "tracker.get",
        {
            "project_id": project_id,
            "task_key": f"workflow-{run_plan_id}",
            "include_graph": False,
            "response_mode": "raw",
        },
    )

    assert failed["data"]["status"] == "failed"
    assert tracker["tasks"][0]["status"] == "failed"
    assert tracker["tasks"][0]["lane_key"] == "done"
    assert tracker["tickets"][0]["status"] == "failed"
    assert tracker["tickets"][0]["lane_key"] == "done"
    assert tracker["tickets"][0]["blocker_reason"] == "contract check failed"


def test_run_plan_recover_restores_failed_blocker_through_mcp(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
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
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {"run_plan_id": run_plan_id, "step_id": "review", "run_token": run_token},
    )
    mcp_client.call_tool_structured(
        "runPlan.recordStep",
        {
            "run_plan_id": run_plan_id,
            "step_id": "review",
            "status": "failed",
            "result_json": {"blocking_issue": "tracker graph has warnings"},
            "error": "Blocked by tracker graph warnings; daemon rejects blocked status.",
            "run_token": run_token,
        },
    )

    recovered = mcp_client.call_tool_structured(
        "runPlan.recover",
        {
            "project_id": project_id,
            "run_plan_id": run_plan_id,
            "step_id": "review",
            "step_status": "blocked",
            "reason": "Recover old daemon blocked-status bug.",
            "error": "tracker graph warnings",
            "actor": "mcp-test",
        },
    )
    tracker = mcp_client.call_tool_structured(
        "tracker.get",
        {
            "project_id": project_id,
            "task_key": f"workflow-{run_plan_id}",
            "include_graph": False,
            "response_mode": "raw",
        },
    )
    reclaimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {"run_plan_id": run_plan_id, "step_id": "review", "run_token": run_token},
    )

    assert recovered["data"]["status"] == "started"
    assert recovered["data"]["steps"][0]["status"] == "blocked"
    assert tracker["tasks"][0]["status"] == "in-progress"
    assert tracker["tickets"][0]["status"] == "in-progress"
    assert tracker["tickets"][0]["blocker_reason"] == "tracker graph warnings"
    assert reclaimed["data"]["status"] == "running"


def test_run_plan_grant_allows_only_active_claimed_step_tool(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _resource_grant_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]

    before_claim = mcp_client.call_tool_error(
        "resource.upsert",
        {
            "project_id": project_id,
            "plugin_slug": "core",
            "resource_key": "learning",
            "data_json": {"body": "too early"},
            "run_token": run_token,
        },
    )
    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "write-resource",
            "run_token": run_token,
        },
    )
    denied_artifact = mcp_client.call_tool_error(
        "artifact.create",
        {
            "project_id": project_id,
            "plugin_slug": "utils",
            "kind": "image",
            "uri": "/generated-assets/no-grant.png",
            "run_token": run_token,
        },
    )
    written = mcp_client.call_tool_structured(
        "resource.upsert",
        {
            "project_id": project_id,
            "plugin_slug": "core",
            "resource_key": "learning",
            "external_id": "run-plan-learning",
            "data_json": {"body": "active step can write"},
            "run_token": run_token,
            "response_mode": "raw",
        },
    )
    completed = mcp_client.call_tool_structured(
        "runPlan.recordStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "write-resource",
            "status": "success",
            "run_token": run_token,
        },
    )
    after_completion = mcp_client.call_tool_error(
        "resource.upsert",
        {
            "project_id": project_id,
            "plugin_slug": "core",
            "resource_key": "learning",
            "data_json": {"body": "too late"},
            "run_token": run_token,
        },
    )

    assert before_claim["code"] == -32007
    assert claimed["data"]["allowed_tools"] == ["resource.upsert"]
    assert denied_artifact["code"] == -32007
    assert written["data"]["data_json"] == {"body": "active step can write"}
    assert completed["data"]["status"] == "completed"
    assert after_completion["code"] == -32007


def test_run_plan_claim_step_guides_provider_action_context_flow(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _action_grant_plan_json()},
    )
    run_plan_id = created["data"]["id"]
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": run_plan_id},
    )

    claimed = mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "project_id": project_id,
            "run_plan_id": run_plan_id,
            "step_id": "call-action",
            "run_token": started["data"]["run_token"],
        },
    )

    guidance = claimed["data"]["action_execution_guidance"]
    assert claimed["data"]["allowed_tools"] == ["action.execute"]
    assert guidance["preferred_path"] == [
        "action.describe",
        "executionContext.discover_or_resolve",
        "action.validate",
        "action.execute",
    ]
    assert guidance["next_calls"][:2] == [
        {
            "operation": "action.describe",
            "arguments": {
                "project_id": project_id,
                "action_ref": "core.catalog.describe",
            },
        },
        {
            "operation": "executionContext.discover",
            "arguments": {
                "project_id": project_id,
                "action_ref": "core.catalog.describe",
                "run_plan_id": run_plan_id,
                "run_id": started["data"]["run_id"],
            },
        },
    ]
    assert guidance["payload_boundary"]["context_ref"].startswith("preferred carrier")


def test_run_plan_grant_argument_restrictions_are_enforced(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {
            "project_id": project_id,
            "run_plan_json": _resource_grant_plan_json("ops.resource-restricted.run"),
        },
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "write-resource",
            "run_token": run_token,
        },
    )

    denied = mcp_client.call_tool_error(
        "resource.upsert",
        {
            "project_id": project_id,
            "plugin_slug": "seo",
            "resource_key": "learning",
            "data_json": {"body": "wrong plugin"},
            "run_token": run_token,
        },
    )

    assert denied["code"] == -32007
    assert "arguments" in denied["data"]["detail"]


def test_run_plan_context_query_grant_enforces_sources_and_fields(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    learning = _create_learning(mcp_client, project_id)
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _context_query_grant_plan_json()},
    )
    started = mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": created["data"]["id"]},
    )
    run_token = started["data"]["run_token"]

    before_claim = mcp_client.call_tool_error(
        "context.query",
        {
            "project_id": project_id,
            "sources": ["learnings"],
            "fields": ["statement", "evidence_json"],
            "run_token": run_token,
            "response_mode": "raw",
        },
    )
    mcp_client.call_tool_structured(
        "runPlan.claimStep",
        {
            "run_plan_id": created["data"]["id"],
            "step_id": "read-context",
            "run_token": run_token,
        },
    )
    wrong_field = mcp_client.call_tool_error(
        "context.query",
        {
            "project_id": project_id,
            "sources": ["learnings"],
            "fields": ["statement", "metadata_json"],
            "run_token": run_token,
        },
    )
    wrong_source = mcp_client.call_tool_error(
        "context.query",
        {
            "project_id": project_id,
            "sources": ["decisions"],
            "fields": ["statement"],
            "run_token": run_token,
        },
    )
    missing_filters = mcp_client.call_tool_error(
        "context.query",
        {
            "project_id": project_id,
            "run_token": run_token,
        },
    )
    context = mcp_client.call_tool_structured(
        "context.query",
        {
            "project_id": project_id,
            "sources": ["learnings"],
            "fields": ["statement", "evidence_json"],
            "run_token": run_token,
            "response_mode": "raw",
        },
    )

    assert before_claim["code"] == -32007
    assert wrong_field["code"] == -32007
    assert wrong_source["code"] == -32007
    assert missing_filters["code"] == -32007
    assert context["items"][0]["id"] == learning["id"]
    assert context["items"][0]["fields"]["evidence_json"] == {"refresh_token": "[redacted]"}


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


def test_run_plan_abort_retires_started_plan_and_tracker_mirror(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _plan_json()},
    )
    run_plan_id = created["data"]["id"]
    mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": run_plan_id},
    )

    aborted = mcp_client.call_tool_structured(
        "runPlan.abort",
        {
            "project_id": project_id,
            "run_plan_id": run_plan_id,
            "reason": "superseded by a newer support workflow run",
            "actor": "codex",
        },
    )
    fetched = mcp_client.call_tool_structured(
        "runPlan.get",
        {"run_plan_id": run_plan_id, "response_mode": "raw"},
    )
    tracker = mcp_client.call_tool_structured(
        "tracker.get",
        {
            "project_id": project_id,
            "task_key": f"workflow-{run_plan_id}",
            "include_graph": False,
            "response_mode": "raw",
        },
    )

    assert aborted["data"]["status"] == "aborted"
    assert fetched["steps"][0]["status"] == "skipped"
    assert fetched["approval_requests"] == []
    assert tracker["tasks"][0]["status"] == "aborted"
    assert tracker["tickets"][0]["status"] == "aborted"
    assert tracker["tickets"][0]["blocker_reason"] is None


def test_run_plan_abort_does_not_abort_independent_tracker_task(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _plan_json()},
    )
    run_plan_id = created["data"]["id"]
    mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": run_plan_id},
    )
    mcp_client.call_tool_structured(
        "tracker.createTask",
        {
            "project_id": project_id,
            "key": "plain-independent-work",
            "title": "Plain independent work",
            "goal": "A tracker-only task with no run-plan ownership.",
        },
    )
    mcp_client.call_tool_structured(
        "tracker.createTicket",
        {
            "project_id": project_id,
            "task_key": "plain-independent-work",
            "key": "plain-ticket",
            "title": "Plain ticket",
        },
    )

    mcp_client.call_tool_structured(
        "runPlan.abort",
        {
            "project_id": project_id,
            "run_plan_id": run_plan_id,
            "reason": "operator stopped the workflow",
            "actor": "codex",
        },
    )
    plain = mcp_client.call_tool_structured(
        "tracker.get",
        {
            "project_id": project_id,
            "task_key": "plain-independent-work",
            "include_graph": False,
            "response_mode": "raw",
        },
    )
    workflow = mcp_client.call_tool_structured(
        "tracker.get",
        {
            "project_id": project_id,
            "task_key": f"workflow-{run_plan_id}",
            "include_graph": False,
            "response_mode": "raw",
        },
    )

    assert plain["tasks"][0]["status"] == "not-started"
    assert {ticket["status"] for ticket in plain["tickets"]} == {"not-started"}
    assert workflow["tasks"][0]["status"] == "aborted"
    assert {ticket["status"] for ticket in workflow["tickets"]} == {"aborted"}


def test_tracker_reject_task_aborts_started_run_plan_and_cascades_mirror(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _plan_json()},
    )
    run_plan_id = created["data"]["id"]
    mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": run_plan_id},
    )

    rejected = mcp_client.call_tool_structured(
        "tracker.rejectTask",
        {
            "project_id": project_id,
            "run_plan_id": run_plan_id,
            "reason": "Operator rejected this workflow run.",
            "actor": "codex",
            "response_mode": "raw",
        },
    )
    fetched = mcp_client.call_tool_structured(
        "runPlan.get",
        {"run_plan_id": run_plan_id, "response_mode": "raw"},
    )
    tracker = mcp_client.call_tool_structured(
        "tracker.get",
        {
            "project_id": project_id,
            "task_key": f"workflow-{run_plan_id}",
            "include_graph": False,
            "response_mode": "raw",
        },
    )

    assert fetched["status"] == "aborted"
    assert rejected["data"]["task"]["status"] == "aborted"
    assert rejected["data"]["task"]["completion_evidence_json"]["decision"] == "rejected"
    assert {ticket["status"] for ticket in tracker["tickets"]} == {"aborted"}
    assert [result["action"] for result in rejected["data"]["results"]] == ["rejected"]


def test_tracker_reject_task_aborts_draft_run_plan_and_cascades_mirror(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _plan_json()},
    )
    run_plan_id = created["data"]["id"]

    rejected = mcp_client.call_tool_structured(
        "tracker.rejectTask",
        {
            "project_id": project_id,
            "run_plan_id": run_plan_id,
            "reason": "Operator rejected this draft workflow run.",
            "actor": "codex",
        },
    )
    fetched = mcp_client.call_tool_structured(
        "runPlan.get",
        {"run_plan_id": run_plan_id, "response_mode": "raw"},
    )
    tracker = mcp_client.call_tool_structured(
        "tracker.get",
        {
            "project_id": project_id,
            "task_key": f"workflow-{run_plan_id}",
            "include_graph": False,
            "response_mode": "raw",
        },
    )

    assert fetched["status"] == "aborted"
    assert rejected["data"]["task"]["status"] == "aborted"
    assert {ticket["status"] for ticket in tracker["tickets"]} == {"aborted"}


def test_tracker_reject_task_validates_before_aborting_run_plan(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {"project_id": project_id, "run_plan_json": _plan_json()},
    )
    run_plan_id = created["data"]["id"]
    mcp_client.call_tool_structured(
        "runPlan.start",
        {"project_id": project_id, "run_plan_id": run_plan_id},
    )

    rejected = mcp_client.call_tool_error(
        "tracker.rejectTask",
        {
            "project_id": project_id,
            "run_plan_id": run_plan_id,
            "reason": "   ",
            "actor": "codex",
        },
    )
    fetched = mcp_client.call_tool_structured(
        "runPlan.get",
        {"run_plan_id": run_plan_id, "response_mode": "raw"},
    )
    tracker = mcp_client.call_tool_structured(
        "tracker.get",
        {
            "project_id": project_id,
            "task_key": f"workflow-{run_plan_id}",
            "include_graph": False,
            "response_mode": "raw",
        },
    )

    assert rejected["code"] == -32602
    assert fetched["status"] == "started"
    assert tracker["tasks"][0]["status"] == "in-progress"
    assert tracker["tickets"][0]["status"] == "not-started"


def test_run_plan_create_from_template_and_list(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]
    created = mcp_client.call_tool_structured(
        "runPlan.create",
        {
            "project_id": project_id,
            "workflow_key": "core.project-memory-review",
            "inputs_json": {"goal": "Review recent project memory."},
        },
    )
    page = mcp_client.call_tool_structured(
        "runPlan.list",
        {"project_id": project_id, "workflow_key": "core.project-memory-review"},
    )
    fetched = mcp_client.call_tool_structured(
        "runPlan.get",
        {"run_plan_id": created["data"]["id"], "response_mode": "raw"},
    )

    assert created["data"]["template_key"] == "core.project-memory-review"
    assert any(item["id"] == created["data"]["id"] for item in page["items"])
    assert fetched["template_snapshot_json"]["key"] == "core.project-memory-review"


def test_run_plan_validate_can_enforce_template_required_inputs(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    structural = mcp_client.call_tool_structured(
        "runPlan.validate",
        {
            "project_id": project_id,
            "workflow_key": "core.project-memory-review",
            "response_mode": "raw",
        },
    )
    missing = mcp_client.call_tool_structured(
        "runPlan.validate",
        {
            "project_id": project_id,
            "workflow_key": "core.project-memory-review",
            "enforce_required_inputs": True,
            "response_mode": "raw",
        },
    )
    with_inputs = mcp_client.call_tool_structured(
        "runPlan.validate",
        {
            "project_id": project_id,
            "workflow_key": "core.project-memory-review",
            "inputs_json": {"goal": "Review recent project memory."},
            "enforce_required_inputs": True,
            "response_mode": "raw",
        },
    )

    assert structural["valid"] is True
    assert missing["valid"] is False
    assert "goal" in missing["errors"][0]["message"]
    assert with_inputs["valid"] is True
    assert with_inputs["plan"]["key"] == "core.project-memory-review.run"


def test_run_plan_validate_marketing_template_wires_optional_provider_video_flow(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    validation = mcp_client.call_tool_structured(
        "runPlan.validate",
        {
            "project_id": project_id,
            "workflow_key": "marketing.campaign-production",
            "response_mode": "raw",
        },
    )

    assert validation["valid"] is True
    assert validation["warnings"] == []
    plan = validation["plan"]
    produce_media = next(step for step in plan["steps"] if step["id"] == "produce-media")
    expected_action_refs = [
        "utils.image.generate",
        "utils.image.edit",
        "utils.google.video.generate",
        "utils.byteplus.video.generate",
        "utils.alibaba.video.generate",
        "utils.kling.video.generate",
        "utils.xai.video.generate",
    ]
    assert produce_media["action_refs"] == expected_action_refs
    grants = plan["grant_snapshot_json"]["mcp_tool_grants"]
    produce_action_grant = next(
        grant
        for grant in grants
        if grant["step_id"] == "produce-media" and grant["tool"] == "action.execute"
    )
    assert produce_action_grant["action_refs"] == expected_action_refs
    assert "utils.video.generate" not in produce_media["action_refs"]
    assert "utils.video.generate" not in {
        ref for grant in grants for ref in grant.get("action_refs", [])
    }


def test_run_plan_template_and_workflow_key_must_match(
    mcp_client: MCPClient,
    seeded_project: dict,
) -> None:
    project_id = seeded_project["data"]["id"]

    rejected = mcp_client.call_tool_error(
        "runPlan.validate",
        {
            "project_id": project_id,
            "template_key": "core.project-memory-review",
            "workflow_key": "engineering.tracked-delivery",
        },
    )

    assert rejected["code"] == -32602
    assert rejected["message"] == "ValidationError"
    assert "must match" in rejected["data"]["detail"]
    assert rejected["data"]["template_key"] == "core.project-memory-review"
    assert rejected["data"]["workflow_key"] == "engineering.tracked-delivery"


def test_run_plan_validate_rejects_secrets(mcp_client: MCPClient) -> None:
    plan = _plan_json()
    plan["metadata"] = {"api_key": "real-secret"}

    validation = mcp_client.call_tool_structured(
        "runPlan.validate",
        {"run_plan_json": plan, "response_mode": "raw"},
    )

    assert validation["valid"] is False
    assert "must not contain secrets" in validation["errors"][0]["message"]
