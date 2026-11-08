"""Exercise delivery through real daemon grants, adapters and memory scheduler."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from apscheduler.events import EVENT_JOB_EXECUTED
from fastapi.testclient import TestClient
from sqlmodel import Session

from stackos import task_delivery
from stackos.context import ContextRepository
from stackos.host_mcp import service
from stackos.host_mcp.result import AgentRunResult
from tests.integration.test_mcp.conftest import MCPClient
from tests.integration.test_repositories.test_task_delivery import commit, generic_result, git


@pytest.mark.parametrize("response_mode", ["compact", "ack"])
def test_project_defaults_setup_rejects_lossy_responses(api, project_id, response_mode):
    arguments = {"project_id": project_id, "delivery": {"host_key": "codex"}}
    endpoint = "/api/v1/operations/tracker.configureDelivery/call"
    saved = api.post(endpoint, json={"arguments": arguments})
    assert saved.status_code == 200, saved.text
    rejected = api.post(
        endpoint,
        json={"arguments": {**arguments, "delivery": {}, "response_mode": response_mode}},
    )
    assert rejected.status_code == 422, rejected.text
    snapshot = api.post(
        "/api/v1/operations/tracker.get/call",
        json={"arguments": {"project_id": project_id, "response_mode": "raw"}},
    )
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["tracker"] == saved.json()["data"]


@pytest.mark.parametrize("workflow", [False, True])
@pytest.mark.parametrize("defaults", [False, True])
def test_real_operation_returns_while_scheduler_delivers(
    api: TestClient,
    project_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    workflow: bool,
    defaults: bool,
) -> None:
    tmp_path = tmp_path / "project"
    tmp_path.mkdir()
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Delivery Test")
    git(tmp_path, "config", "user.email", "delivery@example.test")
    commit(tmp_path, "baseline")

    def operation(name, **arguments):
        response = api.post(
            f"/api/v1/operations/{name}/call",
            json={"arguments": {"project_id": project_id, **arguments}},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        return body.get("data", body)

    scope = {}
    task_key = "automatic"
    if workflow:
        plan = operation(
            "runPlan.create",
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "automatic-workflow",
                "title": "Automatic workflow",
                "steps": [{"id": "deliver", "title": "Deliver"}],
            },
            response_mode="raw",
        )
        scope = {"run_plan_id": plan["id"], "step_id": "deliver"}
        started = operation("runPlan.start", run_plan_id=plan["id"], response_mode="raw")
        operation("runPlan.claimStep", **scope, run_token=started["run_token"])
        task_key = f"workflow-{plan['id']}"
    else:
        operation("tracker.createTask", key=task_key, title="Automatic")
    for key, dependencies in (("first", []), ("second", ["first"])):
        operation(
            "tracker.createTicket",
            task_key=task_key,
            key=key,
            title=key,
            dependency_keys=dependencies,
            **scope,
        )

    entered, release, finished = Event(), Event(), Event()
    calls = []

    def run_agent(_host, _directory, _instructions, prompt, _schema, *, sandbox, on_session=None):
        packet = json.loads(prompt)
        delivery = packet["phase"] == "delivery"
        calls.append(
            (json.loads(prompt)["brief"]["ticket"]["key"], "delivery" if delivery else "review")
        )
        entered.set()
        assert release.wait(5), "Test did not release the background agent"
        result = generic_result(
            outputs={}
            if packet["workflow_step"]
            else {
                "commit_sha": (
                    commit(tmp_path, calls[-1][0])
                    if delivery
                    else git(tmp_path, "rev-parse", "HEAD")
                )
            }
        )
        return AgentRunResult(0, thread_id=f"{calls[-1][0]}-{delivery}", result=result)

    definition = service._definition_by_key("codex")
    monkeypatch.setattr(
        service,
        "_definition_by_key",
        lambda host: replace(
            definition, run_agent=lambda *args, **kwargs: run_agent(host, *args, **kwargs)
        ),
    )
    monkeypatch.setattr(task_delivery, "run_agent", service.run_agent)
    api.app.state.scheduler.add_listener(
        lambda event: finished.set() if event.job_id.startswith("task-delivery:") else None,
        EVENT_JOB_EXECUTED,
    )
    arguments = {
        "task_key": task_key,
        **scope,
        "project_dir": str(tmp_path),
        "delivery_agent": {"name": "builder", "instructions": "Deliver the project ticket."},
        "review_agent": {"name": "reviewer", "instructions": "Review the project ticket."},
    }
    if defaults:
        enabled = api.post(
            "/api/v1/operations/tracker.configureDelivery/call",
            json={
                "arguments": {
                    "project_id": project_id,
                    "delivery": {
                        "delivery_agent": arguments.pop("delivery_agent"),
                        "review_agent": arguments.pop("review_agent"),
                    },
                }
            },
        )
        assert enabled.status_code == 200, enabled.text
        configured = enabled.json()["data"]
        readback = operation("tracker.get", task_key=task_key, response_mode="raw")
        assert readback["tracker"]["config_json"] == configured["config_json"]
        assert readback["tracker"]["rev"] == configured["rev"]
    try:
        receipt = operation("tracker.startDelivery", **arguments)
        assert receipt["status"] == "queued"
        assert entered.wait(5) and not finished.is_set()
        active = operation("tracker.get", task_key=task_key, response_mode="raw")
        assert active["tasks"][0]["active_execution"]["agent"] == "builder"
        first = next(ticket for ticket in active["tickets"] if ticket["key"] == "first")
        assert first["active_execution"] == active["tasks"][0]["active_execution"]
        # This is an ordinary system-authorized MCP caller, not the test grant sentinel.
        mcp = MCPClient(api, api.headers["Authorization"].removeprefix("Bearer "))
        assert "tracker.startDelivery" in {tool["name"] for tool in mcp.list_tools()}
        denied = mcp.call_tool_error(
            "tracker.configureDelivery", {"project_id": project_id, "delivery": {}}
        )
        assert denied["code"] == -32601
        duplicate = mcp.call_tool_structured(
            "tracker.startDelivery", {"project_id": project_id, **arguments}
        )
        assert duplicate["data"]["job_ref"] == receipt["job_ref"]
    finally:
        release.set()
    assert finished.wait(5), "Scheduled delivery did not finish"
    snapshot = operation("tracker.get", task_key=task_key, response_mode="raw")
    assert snapshot["tasks"][0]["active_execution"] is None
    assert all(ticket["active_execution"] is None for ticket in snapshot["tickets"])
    with Session(api.app.state.engine) as reader:
        events = (
            ContextRepository(reader)
            .tracker_status_timeline(project_id=project_id, task_key=task_key, limit=50)
            .items
        )
    lifetime = [event for event in events if event.event_type == "tracker.execution.changed"]
    assert [event.metadata_json["active"] for event in lifetime] == [True, False] * (
        5 if workflow else 4
    )
    assert snapshot["tasks"][0]["status"] == "complete"
    assert snapshot["tasks"][0]["metadata_json"]["delivery"]["status"] == "complete"
    assert snapshot["tasks"][0]["metadata_json"]["delivery"]["review_agent"] == {
        "name": "reviewer",
        "instructions": "Review the project ticket.",
        "host_key": "codex",
    }
    assert [
        ticket["status"] for ticket in snapshot["tickets"] if ticket["key"] in {"first", "second"}
    ] == ["complete", "complete"]
    if workflow:
        mirror = next(
            ticket for ticket in snapshot["tickets"] if ticket["parent_ticket_key"] is None
        )
        assert mirror["status"] == "complete"
    assert calls == [
        ("first", "delivery"),
        ("first", "review"),
        ("second", "delivery"),
        ("second", "review"),
    ] + ([(f"workflow-{plan['id']}-deliver", "delivery")] if workflow else [])
