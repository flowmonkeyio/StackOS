"""Real manifests and shared acquisition feed the existing Google action transport."""

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from stackos.actions import ActionConnectorRequest, ActionRepository
from stackos.actions.package_bridge import PackageActionConnector
from stackos.auth_providers import AuthRepository
from stackos.repositories.base import ValidationError


@pytest.fixture
def service_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return json.dumps(
        {
            "type": "service_account",
            "client_email": "reader@example.iam.gserviceaccount.com",
            "private_key": key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode(),
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )


async def workspace_request(
    session, project_id, service_key, httpx_mock, operation, payload, subject=""
):
    repo = AuthRepository(session)
    saved = repo.store_credential(
        provider_key="google-workspace",
        auth_method_key="service-account",
        display_name="Synthetic Workspace",
        fields={"service_account_json": service_key, "delegated_subject": subject},
        attach_project_id=project_id,
    ).data
    httpx_mock.add_response(
        url="https://oauth2.googleapis.com/token",
        json={"access_token": "synthetic-workspace", "token_type": "Bearer", "expires_in": 3600},
    )
    credential = await repo.resolve_for_execution(
        project_id=project_id,
        provider_key="google-workspace",
        credential_ref=saved.credential_ref,
        operation=operation,
        required_scopes=[],
    )
    credential.config_json["calendars"] = {"default-calendar": "primary"}
    return ActionConnectorRequest(
        project_id=project_id,
        plugin_slug="gtm",
        action_key=f"google-workspace.{operation}",
        action_ref=f"gtm.google-workspace.{operation}",
        provider_key="google-workspace",
        operation=operation,
        input_json=payload,
        config_json={},
        credential=credential,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,payload,reason",
    [
        (
            "gmail.message.send",
            {"user_ref": "person@example.com", "message": {"raw": "UmF3"}},
            "delegated",
        ),
        ("calendar.event.create", {"calendar_ref": "primary", "event": {}}, "shared calendar"),
        (
            "calendar.event.create",
            {"calendar_ref": "default-calendar", "event": {}},
            "shared calendar",
        ),
        (
            "calendar.event.create",
            {
                "calendar_ref": "shared@example.com",
                "event": {"attendees": [{"email": "person@example.com"}]},
            },
            "delegated",
        ),
    ],
)
async def test_direct_workspace_restrictions_precede_action_http(
    session,
    project_id,
    service_key,
    httpx_mock,
    operation,
    payload,
    reason,
):
    request = await workspace_request(
        session, project_id, service_key, httpx_mock, operation, payload
    )
    with pytest.raises(ValidationError, match=reason):
        await PackageActionConnector("google-workspace").execute(request)
    assert len(httpx_mock.get_requests()) == 1  # Token acquisition only.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "subject,operation,payload,url",
    [
        (
            "",
            "calendar.event.create",
            {
                "calendar_ref": "shared@example.com",
                "event": {"summary": "Synthetic", "attendees": []},
            },
            "https://www.googleapis.com/calendar/v3/calendars/shared%40example.com/events",
        ),
        (
            "person@example.com",
            "calendar.event.create",
            {"calendar_ref": "primary", "event": {"attendees": [{"email": "guest@example.com"}]}},
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        ),
        (
            "person@example.com",
            "gmail.message.send",
            {"message": {"raw": "UmF3"}},
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        ),
    ],
)
async def test_workspace_service_account_actions_use_existing_executor(
    session,
    project_id,
    service_key,
    httpx_mock,
    subject,
    operation,
    payload,
    url,
):
    if operation == "calendar.event.create":
        payload = {
            **payload,
            "event": {
                **payload["event"],
                "start": {"dateTime": "2026-09-25T12:00:00Z"},
                "end": {"dateTime": "2026-09-25T13:00:00Z"},
            },
        }
    request = await workspace_request(
        session, project_id, service_key, httpx_mock, operation, payload, subject
    )
    httpx_mock.add_response(url=url, method="POST", json={"id": "synthetic-result"})
    out = (
        await ActionRepository(session).execute(
            project_id=project_id,
            action_ref=request.action_ref,
            input_json=payload,
            credential_ref=request.credential.credential.credential_ref,
        )
    ).data
    assert out.action_call.provider_key == "google-workspace"
    assert httpx_mock.get_requests()[-1].headers["authorization"] == "Bearer synthetic-workspace"
    assert "synthetic-workspace" not in out.model_dump_json()
    assert "private_key" not in out.model_dump_json()


@pytest.mark.asyncio
async def test_ads_service_account_preserves_developer_and_manager_headers(
    session,
    project_id,
    service_key,
    httpx_mock,
):
    saved = (
        AuthRepository(session)
        .store_credential(
            provider_key="google-ads",
            auth_method_key="service-account",
            display_name="Synthetic Ads",
            attach_project_id=project_id,
            fields={
                "service_account_json": service_key,
                "developer_token": "synthetic-developer",
                "manager_account_ref": "123-456-7890",
            },
        )
        .data
    )
    httpx_mock.add_response(
        url="https://oauth2.googleapis.com/token",
        json={"access_token": "synthetic-ads", "token_type": "Bearer", "expires_in": 3600},
    )
    httpx_mock.add_response(
        url="https://googleads.googleapis.com/v24/customers:listAccessibleCustomers",
        json={"resourceNames": ["customers/1234567890"]},
    )
    out = (
        await ActionRepository(session).execute(
            project_id=project_id,
            action_ref="media-buying.google.customer.list",
            input_json={},
            credential_ref=saved.credential_ref,
        )
    ).data
    request = httpx_mock.get_requests()[-1]
    assert request.headers["authorization"] == "Bearer synthetic-ads"
    assert request.headers["developer-token"] == "synthetic-developer"
    assert request.headers["login-customer-id"] == "1234567890"
    assert "synthetic-ads" not in out.model_dump_json()
    assert "synthetic-developer" not in out.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,url,body",
    [
        (
            "google-search-console",
            "https://www.googleapis.com/webmasters/v3/sites",
            {"siteEntry": []},
        ),
        (
            "google-analytics",
            "https://analyticsadmin.googleapis.com/v1beta/accountSummaries?pageSize=1",
            {"accountSummaries": []},
        ),
        (
            "google-tag-manager",
            "https://tagmanager.googleapis.com/tagmanager/v2/accounts",
            {"account": []},
        ),
    ],
)
async def test_real_google_manifests_feed_existing_empty_inventory_probes(
    session,
    service_key,
    httpx_mock,
    provider,
    url,
    body,
):
    repo = AuthRepository(session)
    saved = repo.store_credential(
        provider_key=provider,
        auth_method_key="service-account",
        display_name="Synthetic inventory",
        fields={"service_account_json": service_key},
    ).data
    httpx_mock.add_response(
        url="https://oauth2.googleapis.com/token",
        json={"access_token": "synthetic-inventory", "token_type": "Bearer", "expires_in": 3600},
    )
    httpx_mock.add_response(url=url, json=body)
    tested = (await repo.test(project_id=None, credential_ref=saved.credential_ref)).data
    assert tested.ok
    assert httpx_mock.get_requests()[-1].headers["authorization"] == "Bearer synthetic-inventory"
    assert "synthetic-inventory" not in tested.model_dump_json()
