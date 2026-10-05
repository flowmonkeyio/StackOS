"""Trackbooth generated action execution and failure-contract tests."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.actions import (
    ActionRepository,
)
from stackos.actions import (
    TrackboothActionConnector as RegisteredTrackboothActionConnector,
)
from stackos.actions.connectors import ActionConnectorError, ActionConnectorRequest
from stackos.actions.trackbooth import (
    TrackboothActionConnector,
    TrackboothAssets,
    retire_removed_trackbooth_actions,
    retire_superseded_trackbooth_inventory_scopes,
)
from stackos.db.models import (
    Action,
    ActionCall,
)
from stackos.repositories.base import (
    ConflictError,
    ValidationError,
)
from stackos.repositories.secrets import PayloadSecretRepository
from tests.integration.test_repositories.trackbooth_test_support import (
    _add_trackbooth_sync_responses,
    _sync_trackbooth_catalog,
    _trackbooth_account_listaccounts_detail,
    _trackbooth_api_key_generate_detail,
    _trackbooth_api_key_reveal_detail,
    _trackbooth_credential_ref,
    _trackbooth_generated_action_ref,
    _trackbooth_links_create_detail,
    _trackbooth_offers_findbyid_detail,
)


def test_trackbooth_public_module_preserves_connector_and_lifecycle_imports() -> None:
    assert TrackboothActionConnector is RegisteredTrackboothActionConnector
    assert TrackboothAssets.__module__ == "stackos.actions.trackbooth_assets"
    assert callable(retire_removed_trackbooth_actions)
    assert callable(retire_superseded_trackbooth_inventory_scopes)


@pytest.mark.parametrize("action", ["catalog.search", "operation.describe"])
def test_trackbooth_blocked_endpoints_remain_marked_in_discovery(
    session: Session, project_id: int, httpx_mock: HTTPXMock, action: str
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    detail = _trackbooth_api_key_reveal_detail()
    httpx_mock.add_response(json={"data": [detail] if action == "catalog.search" else detail})
    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="trackbooth." + action,
            input_json={}
            if action == "catalog.search"
            else {"operation_id": detail["operation_id"]},
            credential_ref=credential_ref,
        )
    ).data
    data = out.output_json["data"]
    row = data[0] if isinstance(data, list) else data
    assert row["operation_id"] == "AccountApiKeyController.revealApiKey"
    assert row["execution_blocked"] is True
    assert len(httpx_mock.get_requests()) == 1


def test_trackbooth_catalog_store_failure_retains_provider_receipt(
    session: Session, project_id: int, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stackos.actions.trackbooth as host_trackbooth

    credential_ref = _trackbooth_credential_ref(session, project_id)
    detail = _trackbooth_links_create_detail()
    _add_trackbooth_sync_responses(httpx_mock, detail, catalog_hash="catalog-fixture")

    def fail(*args, **kwargs):
        raise RuntimeError("private-store-error")

    monkeypatch.setattr(host_trackbooth, "_upsert_runtime_actions", fail)
    with pytest.raises(ConflictError) as failed:
        _sync_trackbooth_catalog(session, project_id, credential_ref)
    call = session.get(ActionCall, failed.value.data["action_call_id"])
    assert call.response_json["catalog_hash"] == "catalog-fixture"
    assert call.response_json["source_endpoint"] == "/api/agent-api/catalog/export"
    assert call.response_json["inventory_sync_confirmed"] is False
    assert call.metadata_json["provider_executed"] is True
    assert call.metadata_json["retry_safe"] is False
    assert "private-store-error" not in json.dumps(call.response_json)
    assert len(httpx_mock.get_requests()) == 1


def _diagnostic_request(operation: str, operation_id: str) -> ActionConnectorRequest:
    return ActionConnectorRequest(
        project_id=1,
        plugin_slug="trackbooth",
        action_key="diagnostic",
        action_ref="trackbooth.diagnostic",
        provider_key="trackbooth",
        operation=operation,
        input_json={"operation_id": operation_id, "path_params": {"id": "one"}},
        config_json={"connector": "trackbooth"},
        credential=SimpleNamespace(
            credential=SimpleNamespace(auth_method_key="api-key"),
            credential_ref="fixture-account",
            config_json={"api_base_url": "https://diagnostic.example.test"},
            secret_payload=b'{"api_key":"synthetic-only"}',
        ),
    )


@pytest.mark.parametrize("method,operation", [("GET", "rest.read"), ("POST", "rest.write")])
def test_trackbooth_generic_rest_keeps_live_detail_preflight(
    httpx_mock: HTTPXMock, method: str, operation: str
) -> None:
    descriptor = {
        "operation_id": "Fixture.native",
        "method": method,
        "path": "/api/native/{id}",
        "path_params": [{"name": "id"}],
    }
    httpx_mock.add_response(
        method="GET",
        url="https://diagnostic.example.test/api/agent-api/catalog/Fixture.native",
        json={"data": descriptor},
    )
    httpx_mock.add_response(
        method=method,
        url="https://diagnostic.example.test/api/native/one",
        json={"id": "remote-one"},
    )
    out = asyncio.run(
        TrackboothActionConnector().execute(_diagnostic_request(operation, "Fixture.native"))
    )
    assert out.output_json["data"] == {"id": "remote-one"}
    assert out.output_json["method"] == method
    assert [r.method for r in httpx_mock.get_requests()] == ["GET", method]
    assert len(httpx_mock.get_requests()) == 2


def test_trackbooth_generic_rest_blocks_live_secret_path_after_one_preflight(
    httpx_mock: HTTPXMock,
) -> None:
    descriptor = {
        "operation_id": "Fixture.native",
        "method": "POST",
        "path": "/api/accounts/api-key/reveal",
    }
    httpx_mock.add_response(json={"data": descriptor})
    with pytest.raises(ActionConnectorError) as failed:
        asyncio.run(
            TrackboothActionConnector().execute(_diagnostic_request("rest.write", "Fixture.native"))
        )
    assert len(httpx_mock.get_requests()) == 1
    assert failed.value.metadata_json["provider_executed"] is True
    assert failed.value.metadata_json["primary_request_executed"] is False


def test_trackbooth_generic_rest_blocks_known_secret_operation_without_request(
    httpx_mock: HTTPXMock,
) -> None:
    with pytest.raises(ValidationError, match="not executable"):
        asyncio.run(
            TrackboothActionConnector().execute(
                _diagnostic_request("rest.write", "AccountApiKeyController.generateApiKey")
            )
        )
    assert httpx_mock.get_requests() == []


def test_trackbooth_generated_read_action_calls_endpoint_without_catalog_preflight(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    _add_trackbooth_sync_responses(httpx_mock, _trackbooth_offers_findbyid_detail())
    sync_output = _sync_trackbooth_catalog(session, project_id, credential_ref)
    action_ref = _trackbooth_generated_action_ref(sync_output, "OffersController.findById")
    httpx_mock.add_response(
        method="GET",
        url="https://trackbooth.local.test/api/offers/offer-123",
        json={"data": {"id": "offer-123"}},
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref=action_ref,
            input_json={"path_params": {"id": "offer-123"}},
            credential_ref=credential_ref,
        )
    ).data

    assert out.output_json["operation_id"] == "OffersController.findById"
    assert out.output_json["data"] == {"data": {"id": "offer-123"}}
    requests = httpx_mock.get_requests()
    assert requests[-1].url.path == "/api/offers/offer-123"
    assert [
        request.url.path
        for request in requests
        if request.url.path == "/api/agent-api/catalog/OffersController.findById"
    ] == []
    assert [
        request.url.path
        for request in requests
        if request.url.path == "/api/agent-api/catalog/export"
    ] == ["/api/agent-api/catalog/export"]


def test_trackbooth_generated_write_action_sends_body_without_catalog_preflight(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    _add_trackbooth_sync_responses(httpx_mock, _trackbooth_links_create_detail())
    sync_output = _sync_trackbooth_catalog(
        session,
        project_id,
        credential_ref,
        acting_as_account="acct-managed",
    )
    action_ref = _trackbooth_generated_action_ref(sync_output, "LinksController.create")
    httpx_mock.add_response(
        method="POST",
        url="https://trackbooth.local.test/api/links",
        json={"data": {"id": "link-1"}},
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref=action_ref,
            input_json={
                "body": {
                    "campaign_id": "campaign-1",
                    "name": "Generated action link",
                    "routing_mode": "direct",
                    "offer_id": "offer-1",
                },
            },
            provider_context_json={"acting_as_account": "acct-managed"},
            credential_ref=credential_ref,
        )
    ).data

    assert out.output_json["operation_id"] == "LinksController.create"
    requests = httpx_mock.get_requests()
    actual = requests[-1]
    assert actual.method == "POST"
    assert actual.url.path == "/api/links"
    assert actual.headers["X-Acting-As-Account"] == "acct-managed"
    assert json.loads(actual.content)["routing_mode"] == "direct"


def test_trackbooth_generated_action_materializes_tenant_secret_without_replacing_auth(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    tenant_password = "trackbooth-tenant-smtp-canary"
    detail = {
        "operation_id": "AccountCommunicationController.configureSmtp",
        "name": "account_communication_configure_smtp",
        "method": "POST",
        "path": "/api/accounts/acct-tenant/smtp",
        "context": {"title": "Configure tenant SMTP", "category": "accounts"},
        "body_schema": {
            "component_name": "ConfigureTenantSmtpBody",
            "json_schema": {
                "type": "object",
                "required": ["host", "password"],
                "properties": {
                    "host": {"type": "string"},
                    "password": {"type": "string"},
                    "secure": {
                        "type": "boolean",
                        "description": (
                            "Use true for implicit TLS from connect, or false for required "
                            "STARTTLS."
                        ),
                    },
                },
            },
        },
        "response_schema": {
            "component_name": "ApiOkResponse<ConfigureTenantSmtpResult>",
            "json_schema": {"type": "object", "properties": {}},
        },
    }
    credential_ref = _trackbooth_credential_ref(session, project_id)
    _add_trackbooth_sync_responses(httpx_mock, detail)
    sync_output = _sync_trackbooth_catalog(session, project_id, credential_ref)
    action_ref = _trackbooth_generated_action_ref(
        sync_output,
        "AccountCommunicationController.configureSmtp",
    )
    secure_schema = (
        ActionRepository(session)
        .describe(project_id=project_id, action_ref=action_ref)
        .manifest.input_schema_json["properties"]["body"]["properties"]["secure"]
    )
    assert secure_schema == {
        "type": "boolean",
        "description": "Use true for implicit TLS from connect, or false for required STARTTLS.",
    }
    secret_ref = (
        PayloadSecretRepository(session)
        .set(
            project_id=project_id,
            value=tenant_password,
        )
        .secret_ref
    )
    httpx_mock.add_response(
        method="POST",
        url="https://trackbooth.local.test/api/accounts/acct-tenant/smtp",
        json={"data": {"status": "configured", "provider_echo": tenant_password}},
    )

    out = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref=action_ref,
            input_json={
                "body": {
                    "host": "smtp.tenant.test",
                    "password": {"$secret_ref": secret_ref},
                }
            },
            credential_ref=credential_ref,
        )
    ).data

    actual = httpx_mock.get_requests()[-1]
    assert json.loads(actual.content)["password"] == tenant_password
    assert actual.headers["X-API-Key"] != tenant_password
    assert out.output_json["data"]["data"]["provider_echo"] == "[redacted]"
    call = session.exec(
        select(ActionCall).where(ActionCall.action_key == action_ref.split(".", 1)[1])
    ).one()
    assert call.request_json["body"]["password"] == {"$secret_ref": secret_ref}
    assert tenant_password not in json.dumps(call.model_dump(mode="json"))


def test_trackbooth_generated_action_uses_provider_context_for_acting_account(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    _add_trackbooth_sync_responses(httpx_mock, _trackbooth_links_create_detail())
    sync_output = _sync_trackbooth_catalog(
        session,
        project_id,
        credential_ref,
        acting_as_account="acct-managed",
    )
    action_ref = _trackbooth_generated_action_ref(sync_output, "LinksController.create")
    httpx_mock.add_response(method="POST", json={"data": {"id": "link-acting"}})

    asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref=action_ref,
            input_json={
                "body": {
                    "campaign_id": "campaign-1",
                    "name": "Synced acting account",
                    "routing_mode": "direct",
                },
            },
            provider_context_json={"acting_as_account": "acct-managed"},
            credential_ref=credential_ref,
        )
    )
    assert httpx_mock.get_requests()[-1].headers["X-Acting-As-Account"] == "acct-managed"
    call = session.exec(
        select(ActionCall).where(ActionCall.action_key == action_ref.split(".", 1)[1])
    ).first()
    assert call is not None
    assert call.provider_context_json == {"acting_as_account": "acct-managed"}

    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref=action_ref,
        input_json={
            "acting_as_account": "acct-other",
            "body": {
                "campaign_id": "campaign-1",
                "name": "Wrong acting account",
                "routing_mode": "direct",
            },
        },
        credential_ref=credential_ref,
    )
    assert any(issue.code == "additional_property" for issue in validation.issues)


def test_trackbooth_validation_rejects_invalid_enum_and_missing_required_body_field(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    repo = ActionRepository(session)
    _add_trackbooth_sync_responses(httpx_mock, _trackbooth_links_create_detail())
    sync_output = _sync_trackbooth_catalog(session, project_id, credential_ref)
    action_ref = _trackbooth_generated_action_ref(sync_output, "LinksController.create")

    invalid_enum = repo.validate(
        project_id=project_id,
        action_ref=action_ref,
        input_json={
            "body": {
                "campaign_id": "campaign-1",
                "name": "Bad link",
                "routing_mode": "sideways",
            },
        },
        credential_ref=credential_ref,
    )
    assert any(
        issue.path == "$.body.routing_mode" and issue.code == "enum_mismatch"
        for issue in invalid_enum.issues
    )

    missing_body = repo.validate(
        project_id=project_id,
        action_ref=action_ref,
        input_json={},
        credential_ref=credential_ref,
    )
    assert {
        issue.path
        for issue in missing_body.issues
        if issue.code == "required" and issue.path.startswith("$.body.")
    } >= {"$.body.campaign_id", "$.body.name"}

    generated_invalid_enum = repo.validate(
        project_id=project_id,
        action_ref=action_ref,
        input_json={
            "body": {
                "campaign_id": "campaign-1",
                "name": "Bad generated link",
                "routing_mode": "sideways",
            },
        },
        credential_ref=credential_ref,
    )
    assert any(
        issue.path == "$.body.routing_mode" and issue.code == "enum_mismatch"
        for issue in generated_invalid_enum.issues
    )


def test_trackbooth_rejects_unsafe_base_url_without_outbound_request(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(
        session,
        project_id,
        api_base_url="http://10.0.0.1",
    )

    with pytest.raises(ConflictError) as excinfo:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="trackbooth.catalog.search",
                input_json={},
                credential_ref=credential_ref,
            )
        )

    assert "private" in excinfo.value.data["error"]
    assert httpx_mock.get_requests() == []


def test_trackbooth_blocks_api_key_reveal_and_generate_operations(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)

    _add_trackbooth_sync_responses(
        httpx_mock,
        _trackbooth_api_key_reveal_detail(),
        _trackbooth_api_key_generate_detail(),
    )
    sync_output = _sync_trackbooth_catalog(session, project_id, credential_ref)

    assert sync_output["synced"] == 0
    assert sync_output["blocked_operation_ids"] == [
        "AccountApiKeyController.revealApiKey",
        "AccountApiKeyController.generateApiKey",
    ]
    assert (
        session.exec(select(Action).where(Action.key.like("%accountapikey_revealapikey%"))).first()
        is None
    )
    assert (
        session.exec(
            select(Action).where(Action.key.like("%accountapikey_generateapikey%"))
        ).first()
        is None
    )


def test_trackbooth_permission_error_is_preserved_for_agent_repair(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    httpx_mock.add_response(
        method="GET",
        url="https://trackbooth.local.test/api/agent-api/catalog/LinksController.create",
        status_code=403,
        text='{"message":"agent_api.access is required"}',
    )

    with pytest.raises(ConflictError) as excinfo:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="trackbooth.operation.describe",
                input_json={"operation_id": "LinksController.create"},
                credential_ref=credential_ref,
            )
        )

    assert excinfo.value.data["status"] == "failed"
    assert excinfo.value.data["provider_status_code"] == 403
    assert excinfo.value.data["provider_error"]["message"] == "agent_api.access is required"
    call = session.exec(
        select(ActionCall).where(ActionCall.id == excinfo.value.data["action_call_id"])
    ).one()
    assert call.status.value == "failed"
    assert call.response_json == {
        "status": "failed",
        "provider_status_code": 403,
        "provider_error": {"message": "agent_api.access is required"},
    }


def test_trackbooth_rate_limit_error_preserves_provider_body_for_agent_repair(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _trackbooth_credential_ref(session, project_id)
    _add_trackbooth_sync_responses(httpx_mock, _trackbooth_account_listaccounts_detail())
    _sync_trackbooth_catalog(session, project_id, credential_ref)
    httpx_mock.add_response(
        method="GET",
        url="https://trackbooth.local.test/api/accounts",
        status_code=429,
        json={
            "code": "agent_api_concurrency_limit_exceeded",
            "message": "Agent API concurrency limit exceeded",
            "retry_after_ms": 29988,
            "channel": "agent_api_key",
            "operation_id": "AccountController.listAccounts",
        },
    )

    with pytest.raises(ConflictError) as excinfo:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="trackbooth.api.account_listaccounts",
                input_json={},
                credential_ref=credential_ref,
            )
        )

    data = excinfo.value.data
    assert data["status"] == "failed"
    assert data["action_ref"] == "trackbooth.api.account_listaccounts"
    assert data["provider_status_code"] == 429
    assert data["provider_error"] == {
        "code": "agent_api_concurrency_limit_exceeded",
        "message": "Agent API concurrency limit exceeded",
        "retry_after_ms": 29988,
        "channel": "agent_api_key",
        "operation_id": "AccountController.listAccounts",
    }
    call = session.exec(select(ActionCall).where(ActionCall.id == data["action_call_id"])).one()
    assert call.response_json == {
        "status": "failed",
        "provider_status_code": 429,
        "provider_error": data["provider_error"],
    }
