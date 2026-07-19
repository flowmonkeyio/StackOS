"""Trackbooth generated action execution and failure-contract tests."""

from __future__ import annotations

import asyncio
import json

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.actions import (
    ActionRepository,
)
from stackos.actions import (
    TrackboothActionConnector as RegisteredTrackboothActionConnector,
)
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
            "details": {
                "type": "object",
                "fields": [
                    {"name": "host", "type": "string", "required": True},
                    {"name": "password", "type": "string", "required": True},
                ],
            },
        },
        "response_schema": {
            "component_name": "ApiOkResponse<ConfigureTenantSmtpResult>",
            "details": {"type": "object", "fields": []},
        },
    }
    credential_ref = _trackbooth_credential_ref(session, project_id)
    _add_trackbooth_sync_responses(httpx_mock, detail)
    sync_output = _sync_trackbooth_catalog(session, project_id, credential_ref)
    action_ref = _trackbooth_generated_action_ref(
        sync_output,
        "AccountCommunicationController.configureSmtp",
    )
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
