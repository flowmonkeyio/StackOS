"""Actual host dispatch and account probes cross the resolved native boundary."""

import json
from dataclasses import fields
from datetime import UTC, datetime

import pytest
from sqlmodel import select
from stackos_connectors.connectors.serper.actions import SerperActionConnector

from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository
from stackos.db.models import ActionCall, Credential
from stackos.mcp.context import build_context
from stackos.mcp.permissions import check_call_grant
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.actions.execution import action_execute
from stackos.operations.actions.schemas import ActionExecuteInput
from stackos.repositories.base import ValidationError
from stackos.repositories.run_plans import RunPlanRepository
from tests.integration.account_test_support import seed_test_account


@pytest.mark.asyncio
@pytest.mark.parametrize("granted", [False, True], ids=["direct", "granted"])
async def test_missing_credential_has_one_host_issue_and_never_dispatches(
    session, project_id, monkeypatch, httpx_mock, granted
):
    calls = []

    async def forbidden_send(self, request):
        calls.append(request)
        raise AssertionError("missing credentials must reject before native execution")

    monkeypatch.setattr(SerperActionConnector, "execute", forbidden_send)
    arguments = {
        "project_id": project_id,
        "action_ref": "seo.serper.search",
        "input_json": {"query": "credential boundary"},
    }
    repo = ActionRepository(session)
    validation = repo.validate(**arguments)
    assert [(issue.path, issue.code) for issue in validation.issues] == [
        ("$.credential_ref", "credential_required")
    ]
    if granted:
        plans = RunPlanRepository(session)
        plan = plans.create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "native-credential-boundary.run",
                "title": "Native credential boundary",
                "grants": {
                    "mcp_tool_grants": [
                        {
                            "step_id": "lookup",
                            "tool": "action.execute",
                            "action_refs": ["seo.serper.search"],
                        }
                    ]
                },
                "steps": [
                    {
                        "id": "lookup",
                        "title": "Lookup",
                        "action_refs": ["seo.serper.search"],
                    }
                ],
            },
        ).data
        started = plans.start(plan.id, project_id=project_id).data
        plans.claim_step(run_plan_id=plan.id, run_id=started.run_id, step_id="lookup")
        context = build_context({**arguments, "run_token": started.run_token}, session)
        inputs = ActionExecuteInput(**arguments, run_token=started.run_token)
        check_call_grant("action.execute", context, inputs)
        execute = action_execute(inputs, context, ProgressEmitter(None, None))
    else:
        execute = repo.execute(**arguments)
    with pytest.raises(ValidationError) as error:
        await execute
    assert [(issue["path"], issue["code"]) for issue in error.value.data["issues"]] == [
        ("$.credential_ref", "credential_required")
    ]
    assert calls == []
    assert httpx_mock.get_requests() == []
    assert session.exec(select(ActionCall)).all() == []


@pytest.mark.asyncio
async def test_direct_native_dispatch_redacts_and_replays_without_second_send(
    session, project_id, monkeypatch, httpx_mock
):
    seed_test_account(
        session,
        project_id=project_id,
        provider_key="serper",
        secret_payload=b"native-secret-canary",
        config_json={"refs": {"private": "host-ref"}, "client_secret": "acquisition-only"},
    )
    credential = session.exec(select(Credential).where(Credential.provider_key == "serper")).one()
    calls = []
    execute = SerperActionConnector.execute

    async def spy(self, request):
        calls.append(request)
        return await execute(self, request)

    monkeypatch.setattr(SerperActionConnector, "execute", spy)
    httpx_mock.add_response(
        method="POST",
        url="https://google.serper.dev/search",
        json={"organic": [{"title": "Found"}], "echo": "native-secret-canary"},
    )
    arguments = dict(
        project_id=project_id,
        action_ref="seo.serper.search",
        input_json={"query": "native boundary"},
        credential_ref=credential.credential_ref,
        idempotency_key="native-direct-replay",
    )
    repo = ActionRepository(session)
    first = (await repo.execute(**arguments)).data
    replay = (await repo.execute(**arguments)).data
    assert replay.action_call.id == first.action_call.id
    assert len(calls) == len(httpx_mock.get_requests()) == 1
    request = calls[0]
    assert {field.name for field in fields(request)} == {
        "connector",
        "action_key",
        "operation",
        "input_json",
        "config_json",
        "auth",
        "options",
    }
    assert request.auth.fields == {"api_key": "native-secret-canary"}
    assert request.auth.config == {}
    assert request.options.rate_limiter is not None
    assert "native-secret-canary" not in first.model_dump_json()
    assert first.action_call.connector_key == "serper"


@pytest.mark.asyncio
async def test_account_probe_passes_only_resolved_execution_fields(
    session, project_id, monkeypatch
):
    seed_test_account(
        session,
        project_id=project_id,
        provider_key="google-analytics",
        secret_payload=json.dumps(
            {
                "access_token": "resolved-access",
                "refresh_token": "acquisition-refresh",
                "client_secret": "acquisition-secret",
                "client_id": "acquisition-client",
                "private_key": "acquisition-private",
                "token_type": "Bearer",
            }
        ).encode(),
        config_json={
            "auth_method_key": "oauth2_access_token",
            "properties": {"home": "123"},
            "default_property_ref": "home",
            "permission_verification": {"enforcement": "host"},
        },
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    credential = session.exec(
        select(Credential).where(Credential.provider_key == "google-analytics")
    ).one()
    calls = []

    class Probe:
        default_qps = 2.0

        def __init__(self, **kwargs):
            calls.append(kwargs)

        async def test_credentials(self):
            return {"ok": True, "metadata": {"verification": "native-fixture"}}

    monkeypatch.setattr(
        "stackos.auth_providers.repository.testing.integration_class_for", lambda key: Probe
    )
    result = (
        await AuthRepository(session).test(
            project_id=project_id, credential_ref=credential.credential_ref
        )
    ).data
    assert result.ok
    assert len(calls) == 1
    assert json.loads(calls[0]["payload"]) == {"access_token": "resolved-access"}
    assert set(calls[0]) == {"payload", "http", "probe_context", "rate_limiter"}
    assert calls[0]["probe_context"].auth_method_key == "oauth2_access_token"
    assert calls[0]["probe_context"].permission_verification is None
    assert "acquisition" not in result.model_dump_json()
