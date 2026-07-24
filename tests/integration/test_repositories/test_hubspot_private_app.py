"""HubSpot private-app connection persistence tests."""

from __future__ import annotations

import asyncio
import json

from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.db.models import Credential, CredentialAccount, CredentialScope, CredentialUsageEvent


def test_hubspot_private_app_auth_test_persists_only_provider_probe_evidence(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    token = "hubspot-private-token-canary"
    repo = AuthRepository(session)
    stored = repo.store_credential(
        project_id=project_id,
        provider_key="hubspot",
        auth_method_key="private_app_token",
        profile_key="private-app",
        fields={"access_token": token},
    ).data
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/oauth/v2/private-apps/get/access-token-info",
        json={
            "userId": 123,
            "hubId": 456,
            "appId": 789,
            "scopes": ["crm.objects.contacts.read"],
        },
    )

    tested = asyncio.run(
        repo.test(project_id=project_id, credential_ref=stored.credential_ref)
    ).data
    resolved = asyncio.run(
        repo.resolve_for_execution(
            project_id=project_id,
            provider_key="hubspot",
            credential_ref=stored.credential_ref,
            operation="test.hubspot.private-app",
            required_scopes=["crm.objects.contacts.read"],
        )
    )
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == stored.credential_ref)
    ).one()
    assert credential.id is not None
    scopes = session.exec(
        select(CredentialScope).where(CredentialScope.credential_id == credential.id)
    ).all()
    account = session.exec(
        select(CredentialAccount).where(CredentialAccount.credential_id == credential.id)
    ).one()
    events = session.exec(
        select(CredentialUsageEvent).where(CredentialUsageEvent.credential_id == credential.id)
    ).all()
    rendered = json.dumps(
        {
            "test": tested.model_dump(mode="json"),
            "status": repo.status(project_id=project_id, provider_key="hubspot").model_dump(
                mode="json"
            ),
            "events": [event.metadata_json for event in events],
        }
    )

    assert tested.ok is True
    assert tested.metadata["evidence"]["grants"] == ["crm.objects.contacts.read"]
    assert credential.config_json is not None
    assert credential.config_json["scope_status"] == "known"
    assert [scope.scope for scope in scopes] == ["crm.objects.contacts.read"]
    assert account.provider_account_id == "456"
    assert account.metadata_json == {"hub_id": 456, "app_id": 789, "user_id": 123}
    assert resolved.credential.credential_ref == stored.credential_ref
    assert token not in rendered
