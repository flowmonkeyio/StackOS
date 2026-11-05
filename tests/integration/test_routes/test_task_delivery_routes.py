"""Exercise delivery through real daemon grants, adapters and memory scheduler."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Event

import pytest
from apscheduler.events import EVENT_JOB_EXECUTED
from fastapi.testclient import TestClient

from stackos.host_mcp.result import AgentRunResult
from stackos.plugins.builtin_engineering import task_delivery
from tests.integration.test_mcp.conftest import MCPClient


def test_real_operation_returns_while_scheduler_delivers(
    api: TestClient, project_id: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def operation(name, **arguments):
        response = api.post(
            f"/api/v1/operations/{name}/call",
            json={"arguments": {"project_id": project_id, **arguments}},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        return body.get("data", body)

    operation("tracker.createTask", key="automatic", title="Automatic")
    for key, dependencies in (("first", []), ("second", ["first"])):
        operation(
            "tracker.createTicket",
            task_key="automatic",
            key=key,
            title=key,
            dependency_keys=dependencies,
        )

    entered, release, finished = Event(), Event(), Event()
    calls = []

    def run_agent(_host, _directory, _instructions, prompt, _schema, *, sandbox):
        calls.append((json.loads(prompt)["brief"]["ticket"]["key"], sandbox))
        entered.set()
        assert release.wait(5), "Test did not release the background agent"
        result = (
            {"status": "complete", "summary": "Delivered"}
            if sandbox == "workspace-write"
            else {"decision": "approve", "summary": "Reviewed"}
        )
        return AgentRunResult(0, result=result)

    monkeypatch.setattr(task_delivery(), "run_agent", run_agent)
    api.app.state.scheduler.add_listener(
        lambda event: finished.set() if event.job_id.startswith("engineering-delivery:") else None,
        EVENT_JOB_EXECUTED,
    )
    arguments = {
        "task_key": "automatic",
        "project_dir": str(tmp_path),
        "delivery_agent": {"name": "builder", "instructions": "Deliver the project ticket."},
        "review_agent": {"name": "reviewer", "instructions": "Review the project ticket."},
    }
    try:
        receipt = operation("tracker.startDelivery", **arguments)
        assert receipt["status"] == "queued"
        assert entered.wait(5) and not finished.is_set()
        # This is an ordinary system-authorized MCP caller, not the test grant sentinel.
        mcp = MCPClient(api, api.headers["Authorization"].removeprefix("Bearer "))
        assert "tracker.startDelivery" in {tool["name"] for tool in mcp.list_tools()}
        duplicate = mcp.call_tool_structured(
            "tracker.startDelivery", {"project_id": project_id, **arguments}
        )
        assert duplicate["data"]["job_ref"] == receipt["job_ref"]
    finally:
        release.set()
    assert finished.wait(5), "Scheduled delivery did not finish"
    snapshot = operation("tracker.get", task_key="automatic", response_mode="raw")
    assert snapshot["tasks"][0]["status"] == "complete"
    assert [ticket["status"] for ticket in snapshot["tickets"]] == ["complete", "complete"]
    assert calls == [
        ("first", "workspace-write"),
        ("first", "read-only"),
        ("second", "workspace-write"),
        ("second", "read-only"),
    ]
