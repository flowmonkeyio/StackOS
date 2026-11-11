"""Verified owners prepare work and execute private actions through public callers.

Supabase transport and the provider connector are substituted in these fast
cases. A separate disposable Auth/Postgres/HTTP-provider proof covers the real
network boundary; no repository helper substitutes for operation admission.
"""

from __future__ import annotations

import json

import pytest
from sqlmodel import Session, select

from stackos.db.models import ContextSnapshot, Credential, Project, Provider, TrackerTicket
from stackos.repositories.execution_contexts import ExecutionContextRepository
from stackos.repositories.projects import ProjectRepository
from tests.integration.test_repositories.test_action_secret_refs import (
    CANARY,
    _CapturingConnector,
    _payload,
    _seed_tenant_action,
)
from tests.integration.test_routes.test_project_privacy import (
    create_project,
)
from tests.integration.test_routes.test_project_privacy import (
    private_api as private_api,
)


def mcp(api, name, **arguments):
    response = api.post(
        "/mcp",
        headers={"Accept": "application/json, text/event-stream"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": {"response_mode": "raw", **arguments}},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


def ok(api, name, **arguments):
    result = mcp(api, name, **arguments)
    assert result.get("isError") is False, result
    return result["structuredContent"]


def rest(api, name, **arguments):
    return api.post(f"/api/v1/operations/{name}/call", json={"arguments": arguments})


def plan(key="owned-work", **extra):
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": key,
        "title": key,
        "steps": [
            {"id": "work", "title": "Work", "action_refs": ["tenant-actions.account.create"]}
        ],
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "work",
                    "tool": "action.execute",
                    "action_refs": ["tenant-actions.account.create"],
                }
            ]
        },
        **extra,
    }


@pytest.fixture
def owned_action(private_api):
    api, selected, users, login = private_api
    login("u1")
    project_id = create_project(api, "owned-action")
    with Session(api.app.state.engine) as session:
        _seed_tenant_action(session)
        provider = session.exec(select(Provider).where(Provider.key == "tenant-provider")).one()
        provider.config_json = {
            "auth_methods": [
                {
                    "key": "api-key",
                    "label": "API key",
                    "fields": [
                        {"key": "api_key", "label": "API key", "secret": True, "required": True}
                    ],
                }
            ]
        }
        session.add(provider)
        session.commit()
    connector = _CapturingConnector()
    api.app.state.operation_services["action_connectors"].register(connector)
    account = api.post(
        "/api/v1/auth/accounts/tenant-provider",
        json={
            "display_name": "Private provider",
            "fields": {"api_key": "synthetic-provider-key"},
            "attach_project_id": project_id,
        },
    )
    assert account.status_code == 201, account.text
    account_ref = account.json()["data"]["credential_ref"]
    secret = ok(api, "secret.set", project_id=project_id, value=CANARY)["data"]["secret_ref"]
    return api, selected, users, login, project_id, account_ref, secret, connector


def test_owned_setup_action_grants_and_private_result(owned_action):
    api, _, _, login, pid, account, secret, connector = owned_action
    for name, arguments in (
        ("action.list", {}),
        ("action.describe", {"action_ref": "tenant-actions.account.create"}),
        ("toolProfile.resolve", {"provider_key": "tenant-provider"}),
        ("workflowTemplate.list", {}),
        ("agentPreset.list", {}),
        ("skillPreset.list", {}),
    ):
        ok(api, name, project_id=pid, **arguments)
    task = rest(api, "tracker.createTask", project_id=pid, key="prep", title="Prepare")
    assert task.status_code == 200, task.text
    ticket = ok(
        api, "tracker.createTicket", project_id=pid, task_key="prep", key="prep-work", title="Work"
    )
    assert ticket["data"]["ticket"]["project_id"] == pid
    valid = ok(api, "runPlan.validate", project_id=pid, run_plan_json=plan())
    assert valid["valid"], valid
    created = rest(api, "runPlan.create", project_id=pid, run_plan_json=plan())
    assert created.status_code == 200, created.text
    plan_id = created.json()["data"]["id"]
    arguments = {
        "project_id": pid,
        "action_ref": "tenant-actions.account.create",
        "credential_ref": account,
        "input_json": _payload(secret),
    }
    assert ok(api, "action.validate", **arguments)["valid"]
    denied = mcp(api, "action.run", **arguments)
    assert denied["isError"] and connector.calls == 0
    direct = rest(
        api, "action.run", **arguments, confirm_direct=True, intent_summary="Synthetic owner proof"
    )
    assert direct.status_code == 200, direct.text
    assert connector.calls == 1 and connector.executed_password == CANARY
    assert b"synthetic-provider-key" in connector.provider_secret
    assert CANARY not in direct.text and "synthetic-provider-key" not in direct.text
    call_id = direct.json()["data"]["action_call_id"]
    download = api.get(f"/api/v1/projects/{pid}/files/action-output/{call_id}")
    assert download.status_code == 200, download.text
    assert CANARY not in download.text and "synthetic-provider-key" not in download.text
    ok(api, "actionCall.get", project_id=pid, action_call_id=call_id)
    calls = api.get(f"/api/v1/projects/{pid}/action-calls")
    assert calls.status_code == 200 and calls.json()["total_estimate"] == 1, calls.text
    templates = api.get(f"/api/v1/projects/{pid}/workflow-templates")
    assert templates.status_code == 200, templates.text
    template_key = templates.json()["templates"][0]["key"]
    assert api.get(f"/api/v1/projects/{pid}/workflow-templates/{template_key}").status_code == 200
    started = ok(api, "runPlan.start", project_id=pid, run_plan_id=plan_id)["data"]
    token = started["run_token"]
    # Owner admission does not grant workflow work or permit direct actions in a run.
    assert mcp(api, "action.execute", **arguments)["isError"]
    assert mcp(api, "action.execute", **arguments, run_token=token)["isError"]
    assert mcp(
        api,
        "action.run",
        **arguments,
        run_token=token,
        confirm_direct=True,
        intent_summary="No direct bypass",
    )["isError"]
    ok(
        api,
        "runPlan.claimStep",
        project_id=pid,
        run_plan_id=plan_id,
        step_id="work",
        run_token=token,
    )
    assert mcp(
        api, "action.execute", **{**arguments, "action_ref": "utils.mock.echo"}, run_token=token
    )["isError"]
    assert connector.calls == 1
    executed = ok(api, "action.execute", **arguments, run_token=token)
    assert executed["project_id"] == pid
    assert connector.calls == 2
    ok(
        api,
        "runPlan.recordStep",
        project_id=pid,
        run_plan_id=plan_id,
        step_id="work",
        status="success",
        run_token=token,
    )
    login("u2")
    other = create_project(api, "other-owner")
    assert api.get(f"/api/v1/projects/{pid}/files/action-output/{call_id}").status_code == 404
    assert mcp(api, "actionCall.get", project_id=other, action_call_id=call_id)["isError"]
    assert mcp(api, "runPlan.get", run_plan_id=plan_id)["isError"]
    issued = rest(api, "localRuntime.authorize", project_id=other)
    api.headers["Authorization"] = "Bearer " + issued.json()["credential"]
    for name, args in (
        ("secret.set", {"value": "computer"}),
        ("tracker.createTask", {"key": "computer", "title": "Computer"}),
        ("action.list", {}),
    ):
        denied = api.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": {"project_id": other, **args}},
            },
        )
        assert denied.status_code == 403, denied.text
    assert rest(api, "action.run", **arguments).status_code == 403


def test_setup_refs_are_preflighted_before_bulk_mutation(owned_action):
    api, selected, users, login, pid, _, _, _ = owned_action
    login("u2")
    foreign = create_project(api, "foreign-control")
    foreign_plan = ok(api, "runPlan.create", project_id=foreign, run_plan_json=plan("foreign"))[
        "data"
    ]
    ok(api, "tracker.createTask", project_id=foreign, key="foreign-task", title="Foreign")
    foreign_ticket = ok(
        api,
        "tracker.createTicket",
        project_id=foreign,
        task_key="foreign-task",
        key="foreign-ticket",
        title="Foreign",
    )["data"]["ticket"]["id"]
    with Session(api.app.state.engine) as session:
        snapshot = ContextSnapshot(project_id=foreign, name="private")
        session.add(snapshot)
        session.commit()
        session.refresh(snapshot)
        snapshot_id = snapshot.id
        ownerless = (
            ProjectRepository(session)
            .create(slug="legacy-control", name="Legacy", domain="example.test")
            .data.id
        )
        wrong = Project(
            slug="wrong-environment",
            name="Wrong",
            domain="example.test",
            locale="en-US",
            owner_user_id=users["u1"],
            environment_id=selected.environment_id + "-other",
        )
        session.add(wrong)
        session.commit()
        session.refresh(wrong)
        wrong_id = wrong.id
    login("u1")
    ok(api, "tracker.createTask", project_id=pid, key="bulk", title="Bulk")
    assert mcp(
        api,
        "tracker.createTask",
        project_id=pid,
        key=f"workflow-{foreign_plan['id']}",
        title="Forged workflow",
        source_kind="workflow",
    )["isError"]
    own_ticket = ok(
        api,
        "tracker.createTicket",
        project_id=pid,
        task_key="bulk",
        key="owned-ticket",
        title="Before",
    )["data"]["ticket"]["id"]
    ok(
        api,
        "tracker.updateTicket",
        project_id=pid,
        updates_json=[{"ticket_id": own_ticket, "patch_json": {"title": "After"}}],
    )
    rejected = mcp(
        api,
        "tracker.updateTicket",
        project_id=pid,
        updates_json=[
            {"ticket_id": own_ticket, "patch_json": {"title": "Partial write"}},
            {"ticket_id": foreign_ticket, "patch_json": {"title": "Foreign write"}},
        ],
    )
    assert rejected["isError"]
    with Session(api.app.state.engine) as session:
        assert session.get(TrackerTicket, own_ticket).title == "After"
    for foreign_id in (foreign, ownerless, wrong_id, 999999):
        for name, args in (
            ("secret.set", {"value": "no"}),
            ("tracker.createTask", {"key": "bad", "title": "Bad"}),
            ("action.list", {}),
            ("runPlan.create", {"run_plan_json": plan("bad")}),
        ):
            assert mcp(api, name, project_id=foreign_id, **args)["isError"]
    assert mcp(api, "action.list")["isError"]
    assert mcp(api, "workflowTemplate.list", project_id=pid, repo_root="/private/tmp")["isError"]
    for nested in (False, True):
        arguments = (
            {"run_plan_json": plan("bad-snapshot", context_snapshot_id=snapshot_id)}
            if nested
            else {"run_plan_json": plan("bad-snapshot"), "context_snapshot_id": snapshot_id}
        )
        assert mcp(api, "runPlan.create", project_id=pid, **arguments)["isError"]
    for name, args in (
        (
            "tracker.createTask",
            {
                "key": "bad-source",
                "title": "Bad",
                "source_json": {"run_plan_id": foreign_plan["id"]},
            },
        ),
        (
            "tracker.createTicket",
            {
                "task_key": "bulk",
                "tickets_json": [
                    {"key": "first", "title": "First"},
                    {
                        "key": "foreign",
                        "title": "Foreign",
                        "run_plan_id": foreign_plan["id"],
                        "run_plan_step_id": foreign_plan["steps"][0]["id"],
                    },
                ],
            },
        ),
    ):
        result = mcp(api, name, project_id=pid, **args)
        assert result["isError"], result
        assert "foreign-control" not in json.dumps(result)
    with Session(api.app.state.engine) as session:
        assert (
            session.exec(select(TrackerTicket).where(TrackerTicket.key == "first")).first() is None
        )


def test_private_action_rejects_foreign_secret_account_and_context(owned_action):
    api, _, _, login, pid, account, secret, connector = owned_action
    login("u2")
    other = create_project(api, "foreign-action")
    foreign_secret = ok(api, "secret.set", project_id=other, value="foreign-payload")["data"][
        "secret_ref"
    ]
    account_response = api.post(
        "/api/v1/auth/accounts/tenant-provider",
        json={
            "display_name": "Other provider",
            "fields": {"api_key": "foreign-provider"},
            "attach_project_id": other,
        },
    )
    assert account_response.status_code == 201, account_response.text
    foreign_account = account_response.json()["data"]["credential_ref"]
    with Session(api.app.state.engine) as session:
        foreign_context = (
            ExecutionContextRepository(session)
            .create(
                project_id=other,
                name="Foreign context",
                context_ref="ctx_foreign",
                credential_ref=foreign_account,
            )
            .data.context_ref
        )
    login("u1")
    arguments = {
        "project_id": pid,
        "action_ref": "tenant-actions.account.create",
        "credential_ref": account,
        "input_json": _payload(secret),
        "confirm_direct": True,
        "intent_summary": "Synthetic rejection proof",
    }
    for changes in (
        {"credential_ref": foreign_account},
        {"credential_ref": "credential_missing"},
        {"input_json": _payload(foreign_secret)},
        {"input_json": _payload("secret_missing")},
        {"context_ref": foreign_context},
        {"output_policy_json": {"mode": "always_file", "path": "/private/tmp"}},
    ):
        result = mcp(api, "action.run", **{**arguments, **changes})
        assert result["isError"], result
        assert connector.calls == 0
    # A persisted attachment cannot adopt a legacy or other-environment Account.
    with Session(api.app.state.engine) as session:
        row = session.exec(select(Credential).where(Credential.credential_ref == account)).one()
        owner, environment = row.owner_user_id, row.environment_id
        row.owner_user_id = None
        session.add(row)
        session.commit()
        assert mcp(api, "action.run", **arguments)["isError"]
        row.owner_user_id = owner
        row.environment_id = environment + "-other"
        session.add(row)
        session.commit()
        assert mcp(api, "action.run", **arguments)["isError"]
    assert connector.calls == 0
    assert rest(api, "secret.set", project_id=pid, value="not-rest").status_code != 200
