"""Ownership regressions through real REST/operation callers.

These fast cases substitute Supabase HTTP only. Real local Auth/Postgres proof
uses the same routes and is retained separately with the candidate receipt.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlmodel import Session

from stackos.api.deps import get_settings
from stackos.auth import UserPrincipal
from stackos.db.models import Project, ProjectEvent, Run, RunKind, RunStatus, UserSession
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.resources import ResourceRepository
from stackos.repositories.run_plans import RunPlanRepository

ORIGIN = "http://127.0.0.1:18510"


@pytest.fixture
def private_api(api, settings, monkeypatch):
    selected = settings.model_copy(
        update={
            "process_role": "server",
            "environment_id": "privacy-test",
            "server_url": ORIGIN,
            "supabase_url": "http://127.0.0.1:18521",
            "supabase_anon_key": SecretStr("synthetic-anon"),
        }
    )
    api.app.state.settings = selected
    api.app.dependency_overrides[get_settings] = lambda: selected
    api.base_url = httpx.URL(ORIGIN)
    api.headers.clear()
    api.headers["Origin"] = ORIGIN
    users = {name: str(uuid4()) for name in ("u1", "u2")}

    def respond(request):
        if request.url.path.endswith("/user"):
            return httpx.Response(
                200,
                json={
                    "id": request.headers["authorization"].split()[-1],
                    "email_confirmed_at": "2026-11-10T00:00:00Z",
                },
            )
        user = users[json.loads(request.content)["email"].split("@")[0]]
        return httpx.Response(200, json={"access_token": user, "refresh_token": "synthetic"})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        "stackos.auth.httpx.AsyncClient",
        lambda **kw: original(
            **kw,
            transport=httpx.MockTransport(respond),
        ),
    )

    def login(name):
        api.cookies.clear()
        result = api.post(
            "/api/v1/auth/login",
            json={
                "email": name + "@example.test",
                "password": "password-123",
            },
        )
        assert result.status_code == 200, result.text
        api.headers["X-CSRF-Token"] = result.json()["csrf_token"]

    return api, selected, users, login


def create_project(api, slug):
    response = api.post(
        "/api/v1/projects",
        json={
            "slug": slug,
            "name": slug,
            "domain": "example.test",
            "owner_user_id": "caller-must-not-select-owner",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def test_same_environment_private_projects_and_no_adoption(private_api):
    api, selected, users, login = private_api
    with Session(api.app.state.engine) as session:
        old = ProjectRepository(session).create(slug="old", name="Old", domain="example.test").data
    login("u1")
    p1 = create_project(api, "private-one")
    assert api.get(f"/api/v1/projects/{p1}").status_code == 200
    login("u2")
    p2 = create_project(api, "private-two")
    listing = api.get("/api/v1/projects").json()
    assert [p["id"] for p in listing["items"]] == [p2]
    assert listing["total_estimate"] == 1
    for foreign in (p1, old.id, 999999):
        response = api.get(f"/api/v1/projects/{foreign}")
        assert response.status_code == 404, response.text
    with Session(api.app.state.engine) as session:
        row = session.get(Project, p1)
        assert row.owner_user_id == users["u1"]
        assert row.environment_id == selected.environment_id
    login("u1")
    assert [p["id"] for p in api.get("/api/v1/projects").json()["items"]] == [p1]


def test_child_ids_lists_streams_and_unadapted_routes(private_api):
    api, _selected, _users, login = private_api
    login("u1")
    p1 = create_project(api, "private-one")
    with Session(api.app.state.engine) as session:
        run = Run(project_id=p1, kind=RunKind.RUN_PLAN, status=RunStatus.RUNNING)
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id
    assert api.get(f"/api/v1/runs/{run_id}").status_code == 200
    login("u2")
    p2 = create_project(api, "private-two")
    for path in (
        f"/api/v1/runs/{run_id}",
        f"/api/v1/runs/{run_id}/children",
        f"/api/v1/projects/{p1}/runs",
        f"/api/v1/projects/{p1}/context/timeline/stream",
        f"/api/v1/projects/{p2}/runs?parent_run_id={run_id}",
    ):
        response = api.get(path)
        assert response.status_code == 404, (path, response.text)
    for path in (
        "/api/v1/auth/accounts",
        "/api/v1/context/timeline/stream",
        "/mcp",
        "/generated-assets/private.txt",
        "/api/v1/desktop/native-availability",
    ):
        assert api.get(path).status_code == 403, path


def test_operations_resolve_persisted_children_before_grants(private_api):
    api, _selected, _users, login = private_api
    login("u1")
    p1 = create_project(api, "private-one")
    with Session(api.app.state.engine) as session:
        run = Run(project_id=p1, kind=RunKind.RUN_PLAN, status=RunStatus.RUNNING)
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id
    login("u2")
    p2 = create_project(api, "private-two")
    for args in ({"run_id": run_id}, {"run_id": run_id, "project_id": p2}):
        response = api.post("/api/v1/operations/run.get/call", json={"arguments": args})
        assert response.status_code == 404, response.text
    assert api.post("/api/v1/operations/run.list/call", json={"arguments": {}}).status_code == 403
    response = api.post("/api/v1/operations/project.list/call", json={"arguments": {}})
    assert response.status_code == 200, response.text
    assert [p["id"] for p in response.json()["items"]] == [p2]


def test_repository_principal_filters_names_and_environment(private_api):
    api, selected, users, login = private_api
    login("u1")
    p1 = create_project(api, "private-one")
    with Session(api.app.state.engine) as session:
        session.info["user_principal"] = UserPrincipal(users["u2"], selected.environment_id)
        assert ProjectRepository(session).list().items == []
        from stackos.repositories.base import NotFoundError

        with pytest.raises(NotFoundError):
            ProjectRepository(session).resolve_identifier(project_name="private-one")
        session.info["user_principal"] = UserPrincipal(users["u1"], "different-environment")
        with pytest.raises(NotFoundError):
            ProjectRepository(session).get(p1)


def test_resource_and_plan_ids_and_portfolio_counts(private_api):
    api, _selected, _users, login = private_api
    login("u1")
    p1 = create_project(api, "one")
    with Session(api.app.state.engine) as session:
        record = (
            ResourceRepository(session)
            .upsert_record(
                project_id=p1,
                plugin_slug="core",
                resource_key="learning",
                data_json={"body": "private"},
            )
            .data
        )
        plan = (
            RunPlanRepository(session)
            .create(
                project_id=p1,
                run_plan_json={
                    "schema_version": "stackos.run-plan.v1",
                    "key": "privacy.run",
                    "title": "Privacy",
                    "steps": [{"id": "work", "title": "Work"}],
                },
            )
            .data
        )
    assert api.get(f"/api/v1/resource-records/{record.id}").status_code == 200
    assert api.get(f"/api/v1/run-plans/{plan.id}").status_code == 200
    login("u2")
    p2 = create_project(api, "two")
    for path in (f"/api/v1/resource-records/{record.id}", f"/api/v1/run-plans/{plan.id}"):
        assert api.get(path).status_code == 404
    for name, args in (
        ("resource.get", {"project_id": p2, "record_id": record.id}),
        ("runPlan.get", {"run_plan_id": plan.id}),
        ("tracker.get", {"project_id": p2, "run_plan_id": plan.id}),
    ):
        response = api.post(f"/api/v1/operations/{name}/call", json={"arguments": args})
        assert response.status_code == 404, response.text
    result = api.post("/api/v1/operations/project.portfolio/call", json={"arguments": {}})
    assert result.status_code == 200, result.text
    assert result.json()["total_estimate"] == 1
    assert [row["id"] for row in result.json()["items"]] == [p2]


@pytest.mark.asyncio
async def test_owner_checks_precede_grants_and_idempotent_replay(private_api):
    from stackos.mcp.errors import ToolNotGrantedError
    from stackos.operations.dispatcher import OperationDispatcher
    from stackos.operations.registry import build_operation_registry
    from stackos.repositories.base import NotFoundError

    api, selected, users, login = private_api
    login("u1")
    p1 = create_project(api, "one")
    dispatcher = OperationDispatcher(build_operation_registry())
    args = {
        "project_id": p1,
        "plugin_slug": "core",
        "resource_key": "learning",
        "external_id": "private",
        "data_json": {"body": "private", "project_id": 987654},
        "idempotency_key": "one-write",
    }
    with Session(api.app.state.engine) as session:
        session.info["user_principal"] = UserPrincipal(users["u1"], selected.environment_id)
        with pytest.raises(ToolNotGrantedError):
            await dispatcher.dispatch(
                "resource.upsert", args, session=session, surface="mcp", settings=selected
            )
        with pytest.raises(ToolNotGrantedError):
            await dispatcher.dispatch(
                "resource.upsert", args, session=session, surface="rest", trusted_local_admin=True
            )
        repo = RunPlanRepository(session)
        plan = repo.create(
            project_id=p1,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "grant.run",
                "title": "Grant",
                "grants": {"step_tools": {"work": ["resource.upsert"]}},
                "steps": [{"id": "work", "title": "Work"}],
            },
        ).data
        started = repo.start(plan.id, project_id=p1).data
        repo.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="work")
        args["run_token"] = started.run_token
        with pytest.raises(ToolNotGrantedError):
            await dispatcher.dispatch(
                "project.create",
                {
                    "slug": "must-not-create",
                    "name": "No",
                    "domain": "example.test",
                    "run_token": started.run_token,
                },
                session=session,
                surface="mcp",
                settings=selected,
            )
        assert [p.id for p in ProjectRepository(session).list().items] == [p1]
        result = await dispatcher.dispatch(
            "resource.upsert", args, session=session, surface="mcp", settings=selected
        )
        assert result.payload["data"]["project_id"] == p1
        session.info["user_principal"] = UserPrincipal(users["u2"], selected.environment_id)
        with pytest.raises(NotFoundError):
            await dispatcher.dispatch(
                "resource.upsert", args, session=session, surface="mcp", settings=selected
            )
        # Current persisted owner is checked even for a previously cached success.
        session.info["user_principal"] = UserPrincipal(users["u1"], selected.environment_id)
        project = session.get(Project, p1)
        project.owner_user_id = users["u2"]
        session.add(project)
        session.commit()
        with pytest.raises(NotFoundError):
            await dispatcher.dispatch(
                "resource.upsert", args, session=session, surface="mcp", settings=selected
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke", ["owner", "logout", "expiry", "provider"])
async def test_stream_stops_before_releasing_buffered_private_event(
    private_api, monkeypatch, revoke
):
    from stackos.api.context import _timeline_stream

    api, _selected, users, login = private_api
    login("u1")
    p1 = create_project(api, "one")
    with Session(api.app.state.engine) as session:
        from sqlmodel import select

        row = session.exec(select(UserSession)).one()
        digest = row.token_digest
        session.add(
            ProjectEvent(
                project_id=p1,
                source_type="tracker_task",
                source_id=1,
                event_type="tracker.task.status_changed",
                title="Private event",
            )
        )
        session.commit()

    async def connected():
        return False

    request = SimpleNamespace(
        app=api.app,
        is_disconnected=connected,
        state=SimpleNamespace(
            principal=UserPrincipal(users["u1"], "privacy-test"),
            user_session_digest=digest,
        ),
    )
    stream = _timeline_stream(p1, request, None, 0, 25, 250, 1000, 1, True, api.app.state.engine)
    iterator = stream.body_iterator
    first = await anext(iterator)
    assert "tracker-usage" in first
    assert "Private event" not in first
    with Session(api.app.state.engine) as session:
        if revoke == "owner":
            project = session.get(Project, p1)
            project.owner_user_id = users["u2"]
            session.add(project)
        elif revoke == "provider":

            async def denied(*_):
                from fastapi import HTTPException

                raise HTTPException(401, "revoked")

            monkeypatch.setattr("stackos.auth.ServerAuth.principal", denied)
        else:
            row = session.get(UserSession, digest)
            if revoke == "logout":
                session.delete(row)
            else:
                row.expires_at = 0
                session.add(row)
        session.commit()
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)


def test_own_stream_contains_only_selected_project(private_api):
    api, _selected, _users, login = private_api
    login("u1")
    p1 = create_project(api, "one")
    login("u2")
    p2 = create_project(api, "two")
    with Session(api.app.state.engine) as session:
        for project_id, title in ((p1, "Foreign event"), (p2, "Own event")):
            session.add(
                ProjectEvent(
                    project_id=project_id,
                    source_type="tracker_task",
                    source_id=1,
                    event_type="tracker.task.status_changed",
                    title=title,
                )
            )
        session.commit()
    response = api.get(f"/api/v1/projects/{p2}/context/timeline/stream?replay=true&max_events=1")
    assert response.status_code == 200
    assert "Own event" in response.text and "Foreign event" not in response.text


@pytest.mark.asyncio
async def test_unadapted_mcp_sessions_cannot_bypass_server_boundary(private_api):
    api, _selected, _users, login = private_api
    login("u1")
    result = await api.app.state.mcp_dispatcher.dispatch(
        "workspace.startSession", {"global_session": True}
    )
    assert result.is_error
    assert result.payload["error"]["code"] == -32007
