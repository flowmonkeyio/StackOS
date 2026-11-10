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
from tests.integration.test_repositories.test_task_delivery import (
    commit,
    generic_result,
    git,
    native_source_fixtures,  # noqa: F401 -- applies real native admission fixtures to route scenarios
)


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


@pytest.mark.parametrize("cleared", [False, True])
def test_source_adoption_preview_and_write_use_registered_local_setup(
    api, project_id, tmp_path, cleared
):
    from tests.integration.test_routes.test_project_agent_operations import bind, call

    scope = bind(api, project_id, tmp_path / "adoption")
    created = call(
        api,
        "create",
        {
            **scope,
            "fields": {
                "name": "builder",
                "description": "Fixture",
                "developer_instructions": "Native source",
                "model_reasoning_effort": "high",
            },
        },
    )
    assert created.status_code == 200, created.text
    source = created.json()["data"]
    endpoint = "/api/v1/operations/tracker.configureDelivery/call"
    original = {
        "delivery_agent": None
        if cleared
        else {
            "name": "builder",
            "instructions": "Legacy copy",
            "provider": None,
        },
        "review_agent": None,
    }
    saved = api.post(endpoint, json={"arguments": {"project_id": project_id, "delivery": original}})
    assert saved.status_code == 200, saved.text
    arguments = {
        "project_id": project_id,
        "project_dir": scope["project_dir"],
        "adopt_sources": {
            "delivery_agent": {
                "source_ref": source["source_ref"],
                "expected_digest": source["digest"],
            },
        },
    }
    preview = api.post(endpoint, json={"arguments": {**arguments, "preview": True}})
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["adoption"]["differences"]["delivery_agent"]["instructions"] == {
        "saved": None if cleared else "Legacy copy",
        "source": "Native source",
    }

    def defaults():
        response = api.post(
            "/api/v1/operations/tracker.get/call",
            json={
                "arguments": {
                    "project_id": project_id,
                    "response_mode": "raw",
                }
            },
        )
        assert response.status_code == 200, response.text
        return response.json()["tracker"]

    assert defaults() == saved.json()["data"]
    adopted = api.post(endpoint, json={"arguments": arguments})
    assert adopted.status_code == 200, adopted.text
    readback = defaults()
    assert readback["config_json"]["delivery"] == adopted.json()["data"]["config_json"]["delivery"]
    selected = readback["config_json"]["delivery"]["agents"]["delivery_agent"]
    if cleared:
        assert "provider" not in selected
    else:
        assert selected["provider"] is None
    assert "instructions" not in selected
    assert selected["source_ref"] == source["source_ref"]
    assert readback["config_json"]["delivery"]["review_agent"] is None


@pytest.mark.parametrize("argument_form", ["legacy", "map"])
@pytest.mark.parametrize("selected_role", ["delivery_agent", "review_agent"])
@pytest.mark.parametrize("layout", ["legacy", "mapped", "partial"])
@pytest.mark.parametrize("unrelated_missing", [False, True])
def test_registered_partial_handoff_resolves_only_required_roles(
    api, project_id, tmp_path, monkeypatch, argument_form, selected_role, layout, unrelated_missing
):
    from tests.integration.test_routes.test_project_agent_operations import bind, call

    scope = bind(api, project_id, tmp_path / "admission")
    native_name = "builder" if selected_role == "delivery_agent" else "reviewer"
    created = call(
        api,
        "create",
        {
            **scope,
            "fields": {
                "name": native_name,
                "description": "Fixture",
                "developer_instructions": "Selected source",
            },
        },
    )
    assert created.status_code == 200, created.text
    ref = created.json()["data"]["source_ref"]
    other_role = "review_agent" if selected_role == "delivery_agent" else "delivery_agent"
    other = {"name": "saved-other", "host_key": "claude-code", "instructions": "Saved role"}
    fallback = {"model": "gpt-6-astra", "model_reasoning_effort": "high", "provider": None}
    if layout == "mapped":
        saved = {"agents": {selected_role: fallback, other_role: other}}
    else:
        saved = {selected_role: fallback, other_role: other}
        if layout == "partial":
            saved["agents"] = {selected_role: {"model_reasoning_effort": "medium"}}
    if unrelated_missing:
        saved.setdefault("agents", {})["unrelated"] = {
            "source_ref": {
                "host_key": "codex",
                "native_name": "missing",
                "source_path": ".codex/agents/missing.toml",
            }
        }

    def operation(name, **arguments):
        response = api.post(
            f"/api/v1/operations/{name}/call",
            json={
                "arguments": {
                    "project_id": project_id,
                    **arguments,
                }
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        return payload.get("data", payload)

    operation("tracker.configureDelivery", delivery=saved)
    operation("tracker.createTask", key="partial", title="Partial")
    operation("tracker.createTicket", task_key="partial", key="work", title="Work")
    finished = Event()
    api.app.state.scheduler.add_listener(
        lambda event: finished.set() if event.job_id.startswith("task-delivery:") else None,
        EVENT_JOB_EXECUTED,
    )
    monkeypatch.setattr(
        task_delivery,
        "run_agent",
        lambda *a, **k: AgentRunResult(
            0,
            thread_id="fixture-delivery",
            result=generic_result("blocked"),
        ),
    )
    choice = {"source_ref": ref}
    selection = (
        {selected_role: choice}
        if argument_form == "legacy"
        else {"agents": {selected_role: choice}}
    )
    admitted = operation(
        "tracker.startDelivery", task_key="partial", project_dir=scope["project_dir"], **selection
    )
    assert admitted["status"] == "queued"
    assert finished.wait(5), "Disposable delivery did not retire"
    snapshot = operation("tracker.get", task_key="partial", response_mode="raw")
    agents = snapshot["tasks"][0]["metadata_json"]["delivery"]["agents"]
    assert set(agents) == {"delivery_agent", "review_agent"}
    assert agents[other_role] == other
    assert agents[selected_role]["source_ref"] == ref
    assert agents[selected_role]["model"] == "gpt-6-astra"
    assert agents[selected_role]["native_settings"] == {
        "model_reasoning_effort": "medium" if layout == "partial" else "high",
    }
    assert agents[selected_role]["setting_sources"] == {
        "model": "project",
        "model_reasoning_effort": "project",
    }
    assert snapshot["tracker"]["config_json"]["delivery"] == saved


@pytest.mark.parametrize("problem", ["missing_delivery", "missing_review", "same_source"])
def test_registered_handoff_keeps_selected_source_and_independence_failures(
    api, project_id, tmp_path, monkeypatch, problem
):
    from tests.integration.test_routes.test_project_agent_operations import bind, call

    scope = bind(api, project_id, tmp_path / "required")
    refs = {}
    for role in ("delivery", "review"):
        response = call(
            api,
            "create",
            {
                **scope,
                "fields": {
                    "name": role,
                    "description": "Fixture",
                    "developer_instructions": role,
                },
            },
        )
        assert response.status_code == 200, response.text
        refs[role] = response.json()["data"]["source_ref"]
    if problem == "same_source":
        refs["review"] = refs["delivery"]
    else:
        (
            Path(scope["project_dir"]) / refs[problem.removeprefix("missing_")]["source_path"]
        ).unlink()
    for name, arguments in [
        ("tracker.createTask", {"key": "required", "title": "Required"}),
        ("tracker.createTicket", {"task_key": "required", "key": "work", "title": "Work"}),
    ]:
        response = api.post(
            f"/api/v1/operations/{name}/call",
            json={
                "arguments": {
                    "project_id": project_id,
                    **arguments,
                }
            },
        )
        assert response.status_code == 200, response.text
    calls = []
    monkeypatch.setattr(task_delivery, "run_agent", lambda *a, **k: calls.append(a))
    response = api.post(
        "/api/v1/operations/tracker.startDelivery/call",
        json={
            "arguments": {
                "project_id": project_id,
                "task_key": "required",
                "project_dir": scope["project_dir"],
                "agents": {f"{role}_agent": {"source_ref": ref} for role, ref in refs.items()},
            }
        },
    )
    assert response.status_code == 422, response.text
    assert (
        "independent" in response.text
        if problem == "same_source"
        else "unavailable" in response.text
    )
    snapshot = api.post(
        "/api/v1/operations/tracker.get/call",
        json={
            "arguments": {
                "project_id": project_id,
                "task_key": "required",
                "response_mode": "raw",
            }
        },
    ).json()
    assert not calls
    assert "delivery" not in (snapshot["tasks"][0]["metadata_json"] or {})


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

    def run_agent(
        _host,
        _directory,
        _instructions,
        prompt,
        _schema,
        *,
        sandbox,
        on_session=None,
        process_control=None,
        native_settings=None,
    ):
        assert process_control is not None, "Managed delivery must retain invocation control"
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
    reviewer = snapshot["tasks"][0]["metadata_json"]["delivery"]["agents"]["review_agent"]
    assert {key: reviewer[key] for key in ("name", "instructions", "host_key")} == {
        "name": "reviewer",
        "instructions": "Review the project ticket.",
        "host_key": "codex",
    }
    assert reviewer["source_ref"]["native_name"] == "reviewer"
    assert len(reviewer["source_digest"]) == 64 and reviewer["native_settings"] == {}
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
