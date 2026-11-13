from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
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


def test_deferred_summary_preserves_the_existing_board_subtitle_and_safe_feed(api, project_id):

    condition = "After operator review"
    metadata = {
        "delivery_scope": {"resume_condition": condition, "private": "omit-scope-data"},
        "private": {"prompt": "omit-full-metadata"},
    }
    with Session(api.app.state.engine) as session:
        repo = TrackerRepository(session)
        repo.create_task(
            project_id=project_id,
            key="deferred",
            title="Deferred",
            status="deferred",
            metadata_json=metadata,
        )
        repo.create_task(project_id=project_id, key="trigger", title="Trigger")
        repo.update_task(
            project_id=project_id, task_key="trigger", patch_json={"status": "in-progress"}
        )
    arguments = {
        "project_id": project_id,
        "task_index_only": True,
        "include_graph": False,
        "response_mode": "raw",
    }
    full = api.post("/api/v1/operations/tracker.get/call", json={"arguments": arguments}).json()
    assert (
        next(task for task in full["tasks"] if task["key"] == "deferred")["metadata_json"]
        == metadata
    )
    queries = []

    def observe(_connection, _cursor, sql, _params, _context, _many):
        queries.append(sql)

    event.listen(api.app.state.engine, "before_cursor_execute", observe)
    try:
        summary_response = api.post(
            "/api/v1/operations/tracker.get/call",
            json={
                "arguments": {
                    **arguments,
                    "projection": "ui",
                }
            },
        )
        assert summary_response.status_code == 200, summary_response.text
        feed_response = api.get(
            f"/api/v1/projects/{project_id}/context/timeline/stream",
            params={
                "ui_demand": json.dumps({"projects": {str(project_id): {}}}),
                "replay": "true",
                "max_events": 1,
            },
        )
        assert feed_response.status_code == 200, feed_response.text
    finally:
        event.remove(api.app.state.engine, "before_cursor_execute", observe)
    for data in (summary_response.json(), parse_usage(feed_response.text)["updates"]):
        deferred = next(task for task in data["tasks"] if task["key"] == "deferred")
        assert deferred["board_column"] == "backlog"
        # This exact metadata path is read by TrackerBoardPanel.subtitle.
        assert deferred["metadata_json"] == {"delivery_scope": {"resume_condition": condition}}
        assert deferred["agent_stats"] is None
        assert "omit-" not in json.dumps(deferred)
    assert not any("receipt_json" in sql for sql in queries)
    with Session(api.app.state.engine) as session:
        TrackerRepository(session).update_task(
            project_id=project_id,
            task_key="deferred",
            patch_json={
                "metadata_json": {"delivery_scope": {"resume_condition": {"private": "not-text"}}},
            },
        )
    invalid = api.post(
        "/api/v1/operations/tracker.get/call",
        json={
            "arguments": {
                **arguments,
                "projection": "ui",
            }
        },
    )
    assert invalid.status_code == 200, invalid.text
    assert (
        next(task for task in invalid.json()["tasks"] if task["key"] == "deferred")["metadata_json"]
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["archive", "cleanup", "delete"])
async def test_admitted_global_ui_reconciles_removed_project_without_losing_status(
    api, project_id, transition, monkeypatch
):
    from stackos.api import context
    from stackos.context.repository import ContextRepository
    from stackos.db.models import Project
    from stackos.repositories.tracker import agent_stats
    from tests.integration.test_repositories.test_agent_statistics import _receipt

    with Session(api.app.state.engine) as session:
        removed = session.get(Project, project_id)
        removed_slug = removed.slug
        survivor = (
            ProjectRepository(session)
            .create(slug="scope-survivor", name="Survivor", domain="fixture.test")
            .data.id
        )
        for pid, child in ((project_id, "removed-child"), (survivor, "kept-child")):
            repo = TrackerRepository(session)
            repo.create_task(project_id=pid, key="visible", title="Visible")
            repo.create_ticket(project_id=pid, task_key="visible", key=child, title=child)
            receipt = _receipt(total=41)
            receipt["launch"].update(started_at="2026-11-01T00:00:00Z", ticket_key=child)
            receipt.update(finished_at="2026-11-01T00:01:00Z", exit_code=0)
            repo.update_ticket(
                project_id=pid,
                ticket_key=child,
                patch_json={
                    "completion_evidence_json": {
                        "automatic_delivery": {"attempt": 1, "delivery": receipt}
                    },
                },
            )
        baseline = ContextRepository(session).latest_tracker_status_event_id(project_id=None)
    request_demand = {
        "projects": {
            str(project_id): {"task_keys": ["visible"], "ticket_keys": ["removed-child"]},
            str(survivor): {"task_keys": ["visible"], "ticket_keys": ["kept-child"]},
        }
    }
    # No native agent is launched. Only the transient telemetry list is substituted;
    # admission, project transitions, queries, metrics, status events and SSE are real.
    monkeypatch.setattr(
        context,
        "usage_executions",
        lambda _pid: [
            {"project_id": project_id, "task_key": "visible", "ticket_key": "removed-child"},
            {"project_id": survivor, "task_key": "visible", "ticket_key": "kept-child"},
        ],
    )
    original = agent_stats.decode_receipt
    decoded = []

    def decode(source):
        value = original(source)
        decoded.append(value["launch"]["ticket_key"])
        return value

    monkeypatch.setattr(agent_stats, "decode_receipt", decode)

    async def connected():
        return False

    response = await context.global_context_timeline_stream(
        SimpleNamespace(is_disconnected=connected),
        after=baseline,
        ui_demand=json.dumps(request_demand),
        engine=api.app.state.engine,
    )
    try:
        initial = parse_usage(await anext(response.body_iterator))
        assert {task["project_id"] for task in initial["updates"]["tasks"]} == {
            project_id,
            survivor,
        }
        decoded.clear()
        if transition == "delete":
            deleted = api.post(
                "/api/v1/operations/project.delete/call",
                json={
                    "arguments": {
                        "project_id": project_id,
                        "confirmation": {
                            "permanent": True,
                            "project_id": project_id,
                            "project_slug": removed_slug,
                        },
                        "response_mode": "raw",
                    }
                },
            )
            assert deleted.status_code == 200, deleted.text
            assert deleted.json()["data"]["status"] == "deleted"
        else:
            with Session(api.app.state.engine) as session:
                if transition == "archive":
                    ProjectRepository(session).update(project_id, is_active=False)
                else:
                    removed = session.get(Project, project_id)
                    removed.cleanup_json = {
                        "operation_id": "fixture-incomplete",
                        "status": "incomplete",
                    }
                    session.add(removed)
                    session.commit()
        # Scope replacement is observable even without a tracker status event.
        replacement_frame = await asyncio.wait_for(anext(response.body_iterator), 3)
        replacement = parse_usage(replacement_frame)
        assert "id:" not in replacement_frame
        updates = replacement["updates"]
        assert updates["replace"] and updates["scope_complete"]
        assert {task["project_id"] for task in updates["tasks"]} == {survivor}
        assert {ticket["project_id"] for ticket in updates["tickets"]} == {survivor}
        assert {call["project_id"] for call in replacement["executions"]} == {survivor}
        assert set(decoded) == {"kept-child"}
        # Fresh admission still rejects that unavailable project; existing authority
        # was reconciled rather than relaxed for new foreign/missing demand.
        with pytest.raises(HTTPException) as rejected:
            await context.global_context_timeline_stream(
                SimpleNamespace(is_disconnected=connected),
                after=None,
                ui_demand=json.dumps(request_demand),
                engine=api.app.state.engine,
            )
        assert rejected.value.status_code == 422
        with Session(api.app.state.engine) as session:
            TrackerRepository(session).update_task(
                project_id=survivor, task_key="visible", patch_json={"status": "in-progress"}
            )
            expected = ContextRepository(session).latest_tracker_status_event_id(project_id=None)
        current = parse_usage(await asyncio.wait_for(anext(response.body_iterator), 3))["updates"]
        assert {task["project_id"] for task in current["tasks"]} == {survivor}
        status_frame = await anext(response.body_iterator)
        assert f"id: {expected}\n" in status_frame and "event: tracker-status\n" in status_frame
        assert f'"project_id":{survivor}' in status_frame
    finally:
        await response.body_iterator.aclose()


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
