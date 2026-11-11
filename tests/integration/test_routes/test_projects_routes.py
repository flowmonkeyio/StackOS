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

    deleted = api.delete(f"/api/v1/projects/{project_id}")
    assert deleted.status_code == 200
    assert deleted.json()["data"]["is_active"] is False


def test_project_hard_delete_route_removes_row(api: TestClient) -> None:
    created = api.post(
        "/api/v1/projects",
        json={
            "slug": "hard-delete-route",
            "name": "Hard Delete Route",
            "domain": "hard-delete-route.example",
            "locale": "en-US",
        },
    )
    assert created.status_code == 201
    project_id = created.json()["data"]["id"]

    deleted = api.delete(f"/api/v1/projects/{project_id}", params={"hard": "true"})
    assert deleted.status_code == 200
    assert api.get(f"/api/v1/projects/{project_id}").status_code == 404


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
        ("project.delete", {"project_id": other["id"]}),
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


@pytest.mark.parametrize("surface", ["legacy", "operation"])
def test_schedule_role_null_survives_http_storage(api, project_id, tmp_path, surface):
    from sqlmodel import Session

    from stackos.db.models import ScheduledJob

    execution = {"project_dir": str(tmp_path), "delivery_agent": {"model": None}}
    config = {"kind": "role-clear", "cron_expr": "0 9 * * *", "execution_json": execution}
    if surface == "legacy":
        response = api.post(f"/api/v1/projects/{project_id}/schedules", json=config)
    else:
        response = api.post(
            "/api/v1/operations/schedule.set/call",
            json={"arguments": {"project_id": project_id, **config}},
        )
    assert response.status_code == 200, response.text
    with Session(api.app.state.engine) as session:
        assert session.get(ScheduledJob, response.json()["data"]["id"]).execution_json == execution


@pytest.mark.parametrize("surface", ["legacy", "operation"])
def test_schedule_source_references_round_trip_and_revalidate_on_http_save(
    api, project_id, tmp_path, surface
):
    from sqlmodel import Session, select

    from stackos.db.models import ScheduledJob
    from tests.integration.test_repositories.test_workflow_schedule_agents import (
        assert_no_admission,
        configuration,
    )

    with Session(api.app.state.engine) as session:
        config = configuration(session, project_id, tmp_path)
    execution = config["execution_json"]
    execution["step_agent_assignments"] = {"step0": "role0"}
    execution["agents"]["role0"]["model"] = None

    def save():
        if surface == "legacy":
            return api.post(f"/api/v1/projects/{project_id}/schedules", json=config)
        return api.post(
            "/api/v1/operations/schedule.set/call",
            json={"arguments": {"project_id": project_id, **config}},
        )

    response = save()
    assert response.status_code == 200, response.text
    ref = execution["agents"]["role0"]["source_ref"]
    (tmp_path / ref["source_path"]).unlink()
    rejected = save()
    assert rejected.status_code == 422, rejected.text
    with Session(api.app.state.engine) as session:
        row = session.exec(select(ScheduledJob)).one()
        assert row.execution_json == execution
        assert not row.enabled
        assert_no_admission(session)


@pytest.mark.parametrize("surface", ["legacy", "operation"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("selection", ["missing_source", "copied_codex"])
def test_schedule_http_save_validates_unassigned_selections(
    api, project_id, tmp_path, surface, enabled, selection
):
    from sqlmodel import Session, select

    from stackos.db.models import ScheduledJob
    from tests.integration.test_repositories.test_workflow_schedule_agents import (
        assert_no_admission,
        configuration,
    )

    with Session(api.app.state.engine) as session:
        config = configuration(session, project_id, tmp_path, count=2, ambiguous=True)
    config["enabled"] = enabled
    execution = config["execution_json"]
    execution["step_agent_assignments"] = {"step0": "role0"}
    ref = execution["agents"]["role1"]["source_ref"]
    (tmp_path / ref["source_path"]).unlink()
    if selection == "copied_codex":
        execution["agents"]["role1"] = {"name": "copied", "instructions": "Duplicate Codex prompt"}
    if surface == "legacy":
        response = api.post(f"/api/v1/projects/{project_id}/schedules", json=config)
    else:
        response = api.post(
            "/api/v1/operations/schedule.set/call",
            json={"arguments": {"project_id": project_id, **config}},
        )
    assert response.status_code == 422, response.text
    with Session(api.app.state.engine) as session:
        assert session.exec(select(ScheduledJob)).all() == []
        assert_no_admission(session)


@pytest.mark.parametrize("surface", ["legacy", "operation"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("role", ["delivery_agent", "review_agent"])
@pytest.mark.parametrize("invalid", ["copied_codex", "missing_source"])
def test_schedule_http_save_validates_shadowed_map_selections(
    api, project_id, tmp_path, surface, enabled, role, invalid
):
    from sqlmodel import Session, select

    from stackos.db.models import ScheduledJob
    from tests.integration.test_repositories.test_workflow_schedule_agents import (
        assert_no_admission,
        configuration,
    )

    with Session(api.app.state.engine) as session:
        config = configuration(session, project_id, tmp_path)
    config["enabled"] = enabled
    execution = config["execution_json"]
    ref = execution["agents"]["role0"]["source_ref"]
    execution[role] = {"source_ref": ref}
    execution["agents"][role] = (
        {"name": "copied", "instructions": "Duplicate Codex prompt"}
        if invalid == "copied_codex"
        else {
            "source_ref": {
                **ref,
                "native_name": "missing",
                "source_path": ".codex/agents/missing.toml",
            }
        }
    )
    if surface == "legacy":
        response = api.post(f"/api/v1/projects/{project_id}/schedules", json=config)
    else:
        response = api.post(
            "/api/v1/operations/schedule.set/call",
            json={"arguments": {"project_id": project_id, **config}},
        )
    assert response.status_code == 422, response.text
    with Session(api.app.state.engine) as session:
        assert session.exec(select(ScheduledJob)).all() == []
        assert_no_admission(session)


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
