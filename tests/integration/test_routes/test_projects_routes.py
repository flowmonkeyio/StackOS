"""Project setup route tests for the clean StackOS core."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import stackos.cli.operation_commands as operation_cli
from stackos.cli import app as cli_app


def test_create_project_returns_envelope(api: TestClient) -> None:
    resp = api.post(
        "/api/v1/projects",
        json={
            "slug": "site-a",
            "name": "Site A",
            "domain": "a.com",
            "locale": "en-US",
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["project_id"] == body["data"]["id"]
    assert body["data"]["slug"] == "site-a"


def test_project_crud_routes(api: TestClient, project_id: int) -> None:
    patch = api.patch(f"/api/v1/projects/{project_id}", json={"name": "New Name"})
    assert patch.status_code == 200
    assert patch.json()["data"]["name"] == "New Name"

    # Unconfirmed archive/hard-delete routes are retired.
    assert api.delete(f"/api/v1/projects/{project_id}").status_code == 405
    assert api.get(f"/api/v1/projects/{project_id}").json()["name"] == "New Name"


def test_list_projects_pagination_and_missing(api: TestClient) -> None:
    listing = api.get("/api/v1/projects")
    assert listing.status_code == 200
    assert listing.json()["items"] == []

    missing = api.get("/api/v1/projects/999")
    assert missing.status_code == 404


@pytest.mark.parametrize("name", ["", " \t\n", "x" * 201, None, 7])
@pytest.mark.parametrize("surface", ["legacy", "operation", "console"])
def test_project_name_validation_is_shared(api, project_id, surface, name):
    original = api.get(f"/api/v1/projects/{project_id}").json()
    headers = {}
    if surface == "console":
        headers = {"Authorization": "Bearer " + api.get("/api/v1/auth/ui-token").json()["token"]}
    if surface == "legacy":
        response = api.patch(f"/api/v1/projects/{project_id}", json={"name": name})
    else:
        response = api.post(
            "/api/v1/operations/project.update/call",
            headers=headers,
            json={
                "arguments": {"project_id": project_id, "scope": "catalog", "patch": {"name": name}}
            },
        )
    assert response.status_code == 422, response.text
    assert api.get(f"/api/v1/projects/{project_id}").json() == original


def test_project_catalog_console_and_cli_use_shared_operations(api, project_id, monkeypatch):
    other = api.post(
        "/api/v1/projects", json={"slug": "other", "name": "Other", "domain": "test"}
    ).json()["data"]
    browser = {"Authorization": "Bearer " + api.get("/api/v1/auth/ui-token").json()["token"]}

    def call(name, arguments, headers=browser):
        return api.post(
            f"/api/v1/operations/{name}/call", headers=headers, json={"arguments": arguments}
        )

    renamed = call(
        "project.update",
        {"project_id": other["id"], "scope": "catalog", "patch": {"name": "  Selected  "}},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["data"]["name"] == "Selected"
    moved = call(
        "project.move",
        {
            "project_id": other["id"],
            "scope": "catalog",
            "placement": "before",
            "anchor_id": project_id,
        },
    )
    assert moved.status_code == 200, moved.text
    assert [p["id"] for p in api.get("/api/v1/projects").json()["items"]] == [
        other["id"],
        project_id,
    ]
    for name, arguments in (
        (
            "project.update",
            {"project_id": other["id"], "scope": "catalog", "patch": {"is_active": False}},
        ),
        ("project.move", {"project_id": other["id"], "placement": "top"}),
    ):
        assert call(name, arguments).status_code == 403

    def request(method, path, *, body=None, **kwargs):
        response = api.request(method, path, json=body)
        response.raise_for_status()
        return response.json()

    monkeypatch.setattr(operation_cli, "_api_request", request)
    for name, arguments in (
        ("project.update", {"project_id": project_id, "patch": {"name": "  CLI renamed  "}}),
        ("project.move", {"project_id": project_id, "placement": "top"}),
    ):
        result = CliRunner().invoke(
            cli_app,
            ["ops", "call", name, "--input", "-"],
            input=json.dumps(arguments),
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.stdout
        assert json.loads(result.stdout)["data"]["id"] == project_id
    listed = call("project.list", {"limit": 1}).json()
    assert listed["items"][0]["id"] == project_id
    assert listed["items"][0]["name"] == "CLI renamed"
    assert listed["next_cursor"] == project_id
    assert call("project.list", {"after_id": project_id}).json()["items"][0]["id"] == other["id"]


def test_schedule_create_then_remove(api: TestClient, project_id: int) -> None:
    created = api.post(
        f"/api/v1/projects/{project_id}/schedules",
        json={"kind": "weekly-review", "cron_expr": "0 3 * * *", "enabled": False},
    )
    assert created.status_code == 200
    job_id = created.json()["data"]["id"]

    disabled = api.delete(f"/api/v1/projects/{project_id}/schedules/{job_id}")
    assert disabled.status_code == 200
    assert disabled.json()["data"] == {"project_id": project_id, "job_id": job_id, "removed": True}
    assert api.get(f"/api/v1/projects/{project_id}/schedules").json() == []


def test_schedule_mutations_reject_wrong_project(api: TestClient, project_id: int) -> None:
    created = api.post(
        f"/api/v1/projects/{project_id}/schedules",
        json={"kind": "private", "cron_expr": "0 3 * * *", "enabled": False},
    )
    job_id = created.json()["data"]["id"]
    assert (
        api.patch(
            f"/api/v1/projects/{project_id + 1}/schedules/{job_id}",
            params={"enabled": "true"},
        ).status_code
        == 404
    )
    assert api.delete(f"/api/v1/projects/{project_id + 1}/schedules/{job_id}").status_code == 404
    assert api.get(f"/api/v1/projects/{project_id}/schedules").json()[0]["enabled"] is False


@pytest.mark.parametrize("surface", ["legacy", "operation"])
@pytest.mark.parametrize("workflow", [None, "core.project-memory-review"])
def test_schedule_goal_context_round_trip(api, project_id, surface, workflow):
    config = {
        "kind": "main",
        "name": "Main goal",
        "goal": "Inspect benign project context",
        "cron_expr": "0 9 * * *",
        "timezone": "UTC",
        "template_key": workflow,
        "inputs_json": {"resource_ref": "fixture"},
        "execution_json": {"host_key": "codex"},
    }
    if surface == "legacy":
        response = api.post(f"/api/v1/projects/{project_id}/schedules", json=config)
    else:
        response = api.post(
            "/api/v1/operations/schedule.set/call",
            json={"arguments": {"project_id": project_id, **config, "response_mode": "raw"}},
        )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["goal"] == config["goal"]
    assert data["inputs_json"] == config["inputs_json"]
    assert data["template_key"] == workflow and not data["enabled"]


@pytest.mark.parametrize("surface", ["legacy", "operation"])
@pytest.mark.parametrize(
    "field", ["agents", "delivery_agent", "review_agent", "step_agent_assignments"]
)
def test_schedule_http_rejects_replaced_role_configuration(api, project_id, surface, field):
    config = {"kind": "old", "cron_expr": "0 9 * * *", "execution_json": {field: {}}}
    if surface == "legacy":
        response = api.post(f"/api/v1/projects/{project_id}/schedules", json=config)
    else:
        response = api.post(
            "/api/v1/operations/schedule.set/call",
            json={"arguments": {"project_id": project_id, **config}},
        )
    assert response.status_code == 422, response.text
    assert api.get(f"/api/v1/projects/{project_id}/schedules").json() == []


def test_incomplete_schedule_cannot_enable(api: TestClient, project_id: int) -> None:
    response = api.post(
        f"/api/v1/projects/{project_id}/schedules",
        json={"kind": "incomplete", "cron_expr": "0 3 * * *", "enabled": True},
    )
    assert response.status_code == 422
    assert api.get(f"/api/v1/projects/{project_id}/schedules").json() == []


def test_schedule_cli_uses_registered_http_contract(
    api: TestClient, project_id: int, monkeypatch
) -> None:
    def request(method, path, *, body=None, **kwargs):
        response = api.request(method, path, json=body)
        response.raise_for_status()
        return response.json()

    monkeypatch.setattr(operation_cli, "_api_request", request)
    config = {
        "project_id": project_id,
        "kind": "cli-review",
        "cron_expr": "0 9 * * 1",
        "enabled": False,
    }
    result = CliRunner().invoke(
        cli_app,
        ["ops", "call", "schedule.set", "--input", "-"],
        input=json.dumps(config),
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.stdout
    job_id = json.loads(result.stdout)["data"]["id"]
    listed = api.post(
        "/api/v1/operations/schedule.list/call",
        json={"arguments": {"project_id": project_id, "response_mode": "raw"}},
    )
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == job_id
    runs = api.post(
        "/api/v1/operations/schedule.runs/call",
        json={"arguments": {"project_id": project_id, "limit": 1}},
    )
    assert runs.status_code == 200
    assert runs.json()["items"] == []


def test_schedule_browser_setup_is_bounded_to_explicit_operations(
    api: TestClient, project_id: int
) -> None:
    browser = {"Authorization": "Bearer " + api.get("/api/v1/auth/ui-token").json()["token"]}
    created = api.post(
        "/api/v1/operations/schedule.set/call",
        headers=browser,
        json={
            "arguments": {
                "project_id": project_id,
                "kind": "browser-review",
                "cron_expr": "0 9 * * mon",
            }
        },
    )
    assert created.status_code == 200, created.text
    job_id = created.json()["data"]["id"]
    toggled = api.post(
        "/api/v1/operations/schedule.toggle/call",
        headers=browser,
        json={
            "arguments": {
                "project_id": project_id,
                "job_id": job_id,
                "enabled": False,
            }
        },
    )
    assert toggled.status_code == 200
    denied = api.post(
        "/api/v1/operations/schedule.remove/call",
        headers=browser,
        json={
            "arguments": {
                "project_id": project_id + 1,
                "job_id": job_id,
            }
        },
    )
    assert denied.status_code == 404
    assert (
        api.post(
            "/api/v1/operations/runPlan.create/call",
            headers=browser,
            json={
                "arguments": {
                    "project_id": project_id,
                    "template_key": "core.project-memory-review",
                    "inputs_json": {"goal": "Must not create a run"},
                }
            },
        ).status_code
        == 403
    )
    removed = api.post(
        "/api/v1/operations/schedule.remove/call",
        headers=browser,
        json={
            "arguments": {
                "project_id": project_id,
                "job_id": job_id,
            }
        },
    )
    assert removed.status_code == 200 and removed.json()["data"]["removed"] is True


def test_budget_set_then_get(api: TestClient, project_id: int) -> None:
    created = api.post(
        f"/api/v1/projects/{project_id}/budgets",
        json={"kind": "dataforseo", "monthly_budget_usd": 25.0},
    )
    assert created.status_code == 200

    fetched = api.get(f"/api/v1/projects/{project_id}/budgets/dataforseo")
    assert fetched.status_code == 200
    assert fetched.json()["monthly_budget_usd"] == 25.0

    listing = api.get(f"/api/v1/projects/{project_id}/budgets")
    assert listing.status_code == 200
    assert [row["kind"] for row in listing.json()] == ["dataforseo"]
