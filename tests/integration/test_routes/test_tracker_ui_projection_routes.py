from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlmodel import Session

from stackos.auth_providers import AuthRepository
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.tracker import TrackerRepository
from tests.integration.test_routes.test_auth_provider_routes import _firecrawl_account


def seed(api, project_id):
    with Session(api.app.state.engine) as session:
        repo = TrackerRepository(session)
        for key in ("visible", "hidden"):
            repo.create_task(
                project_id=project_id, key=key, title=key, context_json={"private": "prompt"}
            )
            repo.create_ticket(project_id=project_id, task_key=key, key=f"{key}-child", title=key)
        repo.update_ticket(
            project_id=project_id, ticket_key="visible-child", patch_json={"status": "in-progress"}
        )
        other = (
            ProjectRepository(session)
            .create(slug="foreign", name="Foreign", domain="foreign.test")
            .data
        )
        repo.create_task(project_id=other.id, key="foreign", title="Foreign")
        return other.id


@pytest.mark.parametrize("mode", ["raw", "compact"])
def test_registered_ui_projection_preserves_complete_scope_and_rejects_foreign(
    api, project_id, mode
):
    seed(api, project_id)
    arguments = {
        "project_id": project_id,
        "projection": "ui",
        "task_index_only": True,
        "include_graph": False,
        "response_mode": mode,
        "metric_demand": {"task_keys": ["visible"]},
    }
    response = api.post("/api/v1/operations/tracker.get/call", json={"arguments": arguments})
    assert response.status_code == 200, response.text
    body = response.json()
    data = body.get("data", body)
    assert data["projection"] == "ui" and data["scope_complete"]
    tasks = {task["key"]: task for task in data["tasks"]}
    assert set(tasks) == {"visible", "hidden"}
    assert tasks["visible"]["agent_stats"] is not None
    assert tasks["hidden"]["agent_stats"] is None
    assert all("context_json" not in task for task in tasks.values())
    denied = api.post(
        "/api/v1/operations/tracker.get/call",
        json={
            "arguments": {
                **arguments,
                "metric_demand": {"task_keys": ["foreign"]},
            }
        },
    )
    assert denied.status_code == 422, denied.text
    assert denied.json()["code"] == -32602
    assert denied.json()["retryable"] is False
    full = api.post(
        "/api/v1/operations/tracker.get/call",
        json={
            "arguments": {
                "project_id": project_id,
                "task_key": "visible",
                "include_graph": False,
                "response_mode": "raw",
            }
        },
    )
    assert full.json()["tasks"][0]["context_json"] == {"private": "prompt"}
    with Session(api.app.state.engine) as session:
        from stackos.db.models import Project

        project = session.get(Project, project_id)
        project.cleanup_json = {"state": "incomplete"}
        session.add(project)
        session.commit()
    fenced = api.post("/api/v1/operations/tracker.get/call", json={"arguments": arguments})
    assert fenced.status_code == 409, fenced.text


def parse_usage(text):
    return next(
        json.loads(line[6:])
        for frame in text.split("\n\n")
        if "event: tracker-usage\n" in frame
        for line in frame.splitlines()
        if line.startswith("data: ")
    )


def test_ui_feed_and_legacy_scope_contracts_through_real_route(api, project_id):
    foreign = seed(api, project_id)
    route = f"/api/v1/projects/{project_id}/context/timeline/stream"
    base = {"replay": "true", "max_events": 1, "poll_ms": 250}
    demand = {
        "projects": {str(project_id): {"task_keys": ["visible"], "ticket_keys": ["visible-child"]}}
    }
    response = api.get(route, params={**base, "ui_demand": json.dumps(demand)})
    assert response.status_code == 200, response.text
    updates = parse_usage(response.text)["updates"]
    assert updates["replace"] and updates["scope_complete"] and updates["projection"] == "ui"
    assert {task["key"] for task in updates["tasks"]} == {"visible", "hidden"}
    assert [ticket["key"] for ticket in updates["tickets"]] == ["visible-child"]
    hidden = next(task for task in updates["tasks"] if task["key"] == "hidden")
    assert hidden["agent_stats"] is None
    assert "context_json" not in response.text
    foreign_response = api.get(
        route, params={"ui_demand": json.dumps({"projects": {str(foreign): {}}})}
    )
    assert foreign_response.status_code == 422
    assert api.get(route, params={"task_key": "visible", "ui_demand": "{}"}).status_code == 422
    assert (
        api.get(
            route,
            params={
                "ui_demand": json.dumps(
                    {"projects": {str(project_id): {"ticket_keys": ["missing"]}}}
                )
            },
        ).status_code
        == 422
    )
    scoped = api.get(route, params={**base, "task_key": "visible"})
    assert [task["key"] for task in parse_usage(scoped.text)["updates"]["tasks"]] == ["visible"]
    legacy = api.get(route, params=base)
    assert {task["key"] for task in parse_usage(legacy.text)["updates"]["tasks"]} == {
        "visible",
        "hidden",
    }
    assert all(
        task["agent_stats"] is not None for task in parse_usage(legacy.text)["updates"]["tasks"]
    )
    # Global discovery retains composite identity separately from bounded metrics.
    from stackos.api.context import _ui_usage_scope
    from stackos.repositories.tracker.schema import TrackerUsageDemand

    with Session(api.app.state.engine) as session:
        assert set(
            _ui_usage_scope(session, None, None, TrackerUsageDemand.model_validate(demand))
        ) == {project_id, foreign}


@pytest.mark.asyncio
async def test_global_ui_feed_replaces_complete_composite_scope(api, project_id):
    from stackos.api.context import global_context_timeline_stream

    foreign = seed(api, project_id)
    with Session(api.app.state.engine) as session:
        TrackerRepository(session).create_task(
            project_id=foreign, key="visible", title="Same key, foreign owner"
        )
        TrackerRepository(session).create_ticket(
            project_id=foreign, task_key="visible", key="foreign-child", title="Foreign child"
        )

    async def connected():
        return False

    receipt_queries = []

    def observe(_connection, _cursor, sql, params, _context, _many):
        if "receipt_json" in sql:
            receipt_queries.append((sql, params))

    engine = api.app.state.engine
    event.listen(engine, "before_cursor_execute", observe)
    response = await global_context_timeline_stream(
        SimpleNamespace(is_disconnected=connected),
        after=None,
        ui_demand=json.dumps(
            {
                "projects": {
                    str(project_id): {"task_keys": ["visible"]},
                    str(foreign): {"task_keys": ["visible"], "ticket_keys": ["foreign-child"]},
                }
            }
        ),
        engine=api.app.state.engine,
    )
    try:
        frame = await anext(response.body_iterator)
        data = parse_usage(frame)["updates"]
        assert data["scope"] == "global" and data["scope_complete"] and data["replace"]
        assert {(task["project_id"], task["key"]) for task in data["tasks"]} == {
            (project_id, "visible"),
            (project_id, "hidden"),
            (foreign, "foreign"),
            (foreign, "visible"),
        }
        assert sum(task["agent_stats"] is not None for task in data["tasks"]) == 2
        assert len(receipt_queries) == 1
        assert [ticket["key"] for ticket in data["tickets"]] == ["foreign-child"]
    finally:
        await response.body_iterator.aclose()
        event.remove(engine, "before_cursor_execute", observe)


@pytest.mark.parametrize("mode", ["raw", "compact"])
def test_home_account_summary_retains_status_fields_and_attachment_owner(
    api, project_id, monkeypatch, mode
):
    first = _firecrawl_account(api, project_id=project_id)
    _firecrawl_account(api, display_name="Unattached")
    full = api.get(f"/api/v1/projects/{project_id}/connections/accounts").json()

    def forbidden_inventory(*args, **kwargs):
        raise AssertionError("Home must not build provider setup inventories")

    monkeypatch.setattr(AuthRepository, "list_providers", forbidden_inventory)
    response = api.post(
        "/api/v1/operations/connection.list/call",
        json={
            "arguments": {
                "project_id": project_id,
                "summary_only": True,
                "response_mode": mode,
            }
        },
    )
    assert response.status_code == 200, response.text
    summary = response.json().get("data", response.json())
    assert summary["providers"] == []
    assert summary["accounts"] == full["accounts"]
    assert summary["accounts"][0]["credential_ref"] == first["credential_ref"]
    assert "fc-secret" not in response.text
    global_summary = api.get("/api/v1/auth/accounts", params={"summary_only": True}).json()
    assert len(global_summary["accounts"]) == 2
