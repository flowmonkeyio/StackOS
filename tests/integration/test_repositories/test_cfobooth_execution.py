"""P07 caller, grant, schema and server-denial boundaries through native CFO."""

import json

import httpx
import pytest
from sqlmodel import select

from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository
from stackos.db.models import ActionCall
from stackos.mcp.context import build_context
from stackos.mcp.permissions import check_call_grant
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.actions.discovery import action_list
from stackos.operations.actions.execution import action_execute, action_run
from stackos.operations.actions.schemas import ActionExecuteInput, ActionListInput, ActionRunInput
from stackos.repositories.base import ConflictError, RepositoryError, ValidationError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.run_plans import RunPlanRepository
from tests.integration.test_repositories.cfobooth_test_support import (
    BASE,
    CANARY,
    ORG,
    catalog,
    descriptor,
    native_cfo,
    sync,
    verified_account,
)

pytestmark = [native_cfo, pytest.mark.fresh_catalog, pytest.mark.asyncio]


async def caller(session, project_id, ref, action_ref, payload, *, granted=False, idem=None):
    args = dict(
        project_id=project_id,
        action_ref=action_ref,
        input_json=payload,
        credential_ref=ref,
        response_mode="raw",
        idempotency_key=idem,
    )
    if granted:
        plans = RunPlanRepository(session)
        plan = plans.create(
            project_id=project_id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "cfobooth.fixture",
                "title": "Isolated CFO caller",
                "grants": {
                    "mcp_tool_grants": [
                        {"step_id": "invoke", "tool": "action.execute", "action_refs": [action_ref]}
                    ]
                },
                "steps": [{"id": "invoke", "title": "Invoke", "action_refs": [action_ref]}],
            },
        ).data
        start = plans.start(plan.id, project_id=project_id).data
        plans.claim_step(run_plan_id=plan.id, run_id=start.run_id, step_id="invoke")
        inputs = ActionExecuteInput(**args, run_token=start.run_token)
        context = build_context(inputs.model_dump(), session)
        check_call_grant("action.execute", context, inputs)
        return await action_execute(inputs, context, ProgressEmitter(None, None))
    inputs = ActionRunInput(**args, confirm_direct=True, intent_summary="Isolated protocol proof")
    return await action_run(
        inputs, build_context(inputs.model_dump(), session), ProgressEmitter(None, None)
    )


@pytest.mark.parametrize("granted", [False, True])
async def test_direct_and_granted_owner_then_foreign_call(session, project_id, httpx_mock, granted):
    ref = await verified_account(session, project_id, httpx_mock)
    operation = descriptor()
    synced = await sync(session, project_id, ref, httpx_mock, catalog(operation))
    action_ref = synced["action_refs"][0]
    repo = ActionRepository(session)
    listing = await action_list(
        ActionListInput(project_id=project_id, provider_key="cfobooth"),
        build_context({"project_id": project_id}, session),
        ProgressEmitter(None, None),
    )
    assert action_ref in {item.action_ref for item in listing.items}
    assert repo.describe(project_id=project_id, action_ref=action_ref).execution_available
    assert repo.validate(
        project_id=project_id, action_ref=action_ref, input_json={}, credential_ref=ref
    ).valid
    httpx_mock.add_response(
        method="GET",
        url=BASE + f"/api/organizations/{ORG}/fixture/read",
        json={"requestId": "operation", "data": {"ok": True}},
    )
    result = await caller(session, project_id, ref, action_ref, {}, granted=granted)
    assert result.data.output_json["data"] == {"ok": True}
    before = len(httpx_mock.get_requests())
    foreign = (
        ProjectRepository(session)
        .create(slug="foreign-cfo", name="Foreign CFO", domain="example.test", locale="en-US")
        .data.id
    )
    foreign_listing = await action_list(
        ActionListInput(project_id=foreign, provider_key="cfobooth"),
        build_context({"project_id": foreign}, session),
        ProgressEmitter(None, None),
    )
    assert action_ref not in {item.action_ref for item in foreign_listing.items}
    with pytest.raises(RepositoryError):
        await caller(session, foreign, ref, action_ref, {}, granted=granted)
    assert len(httpx_mock.get_requests()) == before == 3
    calls = session.exec(select(ActionCall)).all()
    assert len(calls) == 2  # sync + legitimate invocation; foreign has no audit mutation
    assert CANARY not in json.dumps([c.model_dump(mode="json") for c in calls])
    assert all(r.headers["X-API-Key"] == CANARY for r in httpx_mock.get_requests())


async def test_strict_schema_survives_import_and_actual_dispatch(session, project_id, httpx_mock):
    ref = await verified_account(session, project_id, httpx_mock)
    body = {
        "type": "object",
        "additionalProperties": False,
        "required": ["mode", "nested", "label", "tags"],
        "properties": {
            "mode": {"enum": ["bounded", "all"]},
            "nested": {"$ref": "#/$defs/nested"},
            "label": {"type": "string", "minLength": 1, "maxLength": 8},
            "tags": {
                "type": "array",
                "minItems": 1,
                "maxItems": 2,
                "items": {"type": "string", "maxLength": 4},
            },
        },
        "$defs": {
            "nested": {
                "type": "object",
                "additionalProperties": False,
                "required": ["value"],
                "properties": {"value": {"type": "integer", "minimum": 1, "maximum": 3}},
            }
        },
        "oneOf": [
            {"properties": {"mode": {"const": "bounded"}}},
            {"properties": {"mode": {"const": "all"}}},
        ],
        "x-semantic-note": "Opaque provider meaning",
    }
    op = descriptor("fixture.query", method="POST", body=body)
    out = await sync(session, project_id, ref, httpx_mock, catalog(op))
    action_ref = out["action_refs"][0]
    repo = ActionRepository(session)
    schema = repo.describe(project_id=project_id, action_ref=action_ref).manifest.input_schema_json
    assert schema["properties"]["body"]["x-semantic-note"] == body["x-semantic-note"]
    valid = {"body": {"mode": "bounded", "nested": {"value": 2}, "label": "ok", "tags": ["one"]}}
    for payload in (
        {},
        {**valid, "extra": True},
        {"body": {**valid["body"], "mode": "wrong"}},
        {"body": {**valid["body"], "nested": {"value": 0}}},
        {"body": {**valid["body"], "nested": {"value": 1, "extra": True}}},
        {"body": {**valid["body"], "label": ""}},
        {"body": {**valid["body"], "label": "too-long-label"}},
        {"body": {**valid["body"], "tags": []}},
        {"body": {**valid["body"], "tags": ["one", "two", "more"]}},
        {"body": {**valid["body"], "tags": ["too-long"]}},
    ):
        assert not repo.validate(
            project_id=project_id, action_ref=action_ref, credential_ref=ref, input_json=payload
        ).valid
        with pytest.raises(ValidationError):
            await repo.execute(
                project_id=project_id, action_ref=action_ref, credential_ref=ref, input_json=payload
            )
    assert len(httpx_mock.get_requests()) == 2
    assert repo.validate(
        project_id=project_id,
        action_ref=action_ref,
        credential_ref=ref,
        input_json={"body": {**valid["body"], "mode": "all"}},
    ).valid
    httpx_mock.add_response(
        method="POST",
        url=BASE + f"/api/organizations/{ORG}/fixture/query",
        json={"requestId": "valid", "data": {}},
    )
    await caller(session, project_id, ref, action_ref, valid)
    assert json.loads(httpx_mock.get_requests()[-1].content) == valid["body"]
    assert len(httpx_mock.get_requests()) == 3


@pytest.mark.parametrize(
    "scope_field",
    ["subject", "organization_id", "key_id", "api_base_url", "credential_ref", "project_id"],
)
async def test_each_scope_dimension_denies_stale_inventory(
    session, project_id, httpx_mock, scope_field
):
    from stackos.db.models import Action

    ref = await verified_account(session, project_id, httpx_mock)
    out = await sync(session, project_id, ref, httpx_mock, catalog(descriptor()))
    row = session.exec(select(Action).where(Action.key.startswith("api."))).first()
    row.config_json = {**row.config_json, f"inventory_{scope_field}": "foreign"}
    session.add(row)
    session.commit()
    with pytest.raises(RepositoryError):
        await caller(session, project_id, ref, out["action_refs"][0], {})
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.parametrize("failure", ["revoked", "timeout"])
async def test_server_authority_and_unknown_mutation_are_not_replayed(
    session, project_id, httpx_mock, failure
):
    ref = await verified_account(session, project_id, httpx_mock)
    op = descriptor("fixture.write", method="POST", effect="mutation", body={"type": "object"})
    out = await sync(session, project_id, ref, httpx_mock, catalog(op))
    url = BASE + f"/api/organizations/{ORG}/fixture/write"
    if failure == "revoked":
        httpx_mock.add_response(
            method="POST",
            url=url,
            status_code=403,
            json={"requestId": "denied", "error": {"code": "forbidden", "message": "Not allowed"}},
        )
    else:
        httpx_mock.add_exception(
            httpx.ReadTimeout("synthetic uncertain outcome"), method="POST", url=url
        )
    with pytest.raises(ConflictError) as failed:
        await caller(
            session,
            project_id,
            ref,
            out["action_refs"][0],
            {"body": {}},
            idem="one-explicit-intent",
        )
    call = session.get(ActionCall, failed.value.data["action_call_id"])
    assert call.status == "failed"
    assert call.metadata_json["retry_safe"] is False
    if failure == "timeout":
        assert call.metadata_json["outcome_unknown"] is True
    assert len(httpx_mock.get_requests()) == 3
    assert CANARY not in json.dumps(call.model_dump(mode="json"))


async def test_real_loopback_probe_sync_and_stored_action(session, project_id):
    """Real socket boundary; synthetic server, actual native HTTP handlers."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    from tests.integration.test_repositories.cfobooth_test_support import identity

    seen = []
    operation = descriptor()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("X-API-Key")))
            if self.path.endswith("/api-keys/current"):
                data = identity()
            elif self.path.endswith("/operation-catalog"):
                data = catalog(operation)
            else:
                data = {"requestId": "loopback", "data": {"source": "owned-fixture"}}
            output = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(output)))
            self.end_headers()
            self.wfile.write(output)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/owned;prefix"
    try:
        repo = AuthRepository(session)
        ref = repo.store_credential(
            provider_key="cfobooth",
            display_name="Loopback",
            fields={"api_key": CANARY, "organization_id": ORG, "api_base_url": base},
            attach_project_id=project_id,
        ).data.credential_ref
        assert (await repo.test(project_id=project_id, credential_ref=ref)).data.ok
        actions = ActionRepository(session)
        out = (
            await actions.execute(
                project_id=project_id,
                action_ref="cfobooth.catalog.sync",
                input_json={},
                credential_ref=ref,
            )
        ).data.output_json
        result = await caller(session, project_id, ref, out["action_refs"][0], {})
        assert result.data.output_json["data"] == {"source": "owned-fixture"}
        assert [path for path, _ in seen] == [
            f"/owned;prefix/api/organizations/{ORG}/api-keys/current",
            f"/owned;prefix/api/organizations/{ORG}/operation-catalog",
            f"/owned;prefix/api/organizations/{ORG}/fixture/read",
        ]
        assert all(key == CANARY for _, key in seen)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
