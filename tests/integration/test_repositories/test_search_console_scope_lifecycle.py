"""Regression for shipped write-mode Accounts after canonical auth extraction."""

import base64
import json
from datetime import timedelta
from urllib.parse import parse_qs

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlmodel import select

from stackos.auth_providers import AuthRepository
from stackos.auth_providers.repository.utils import utcnow
from stackos.db.models import CredentialScope
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository

READ = "https://www.googleapis.com/auth/webmasters.readonly"
WRITE = "https://www.googleapis.com/auth/webmasters"
TOKEN_URL = "https://oauth2.googleapis.com/token"


@pytest.fixture
def service_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return json.dumps(
        {
            "type": "service_account",
            "client_email": "test@project.iam.gserviceaccount.com",
            "private_key": key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode(),
            "token_uri": TOKEN_URL,
        }
    )


def create(session, project_id, service_key, **fields):
    repo = AuthRepository(session)
    account = repo.store_credential(
        provider_key="google-search-console",
        auth_method_key="service-account",
        display_name="Synthetic Search Console",
        attach_project_id=project_id,
        fields={"service_account_json": service_key, **fields},
    ).data
    assert "private_key" not in account.model_dump_json()
    return repo, account.credential_ref


async def resolve(repo, ref, project_id, scope):
    return await repo.resolve_for_execution(
        project_id=project_id,
        provider_key="google-search-console",
        credential_ref=ref,
        operation="scope-regression",
        required_scopes=[scope],
    )


def token_response(httpx_mock, token="synthetic-access"):
    httpx_mock.add_response(
        url=TOKEN_URL, json={"access_token": token, "expires_in": 3600, "token_type": "Bearer"}
    )


def assertion_scope(request):
    body = parse_qs(request.content.decode())["assertion"][0].split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))["scope"]


@pytest.mark.asyncio
async def test_saved_write_mode_survives_renewal_and_allows_reads(
    session, project_id, service_key, httpx_mock
):
    repo, ref = create(session, project_id, service_key, access_mode="sitemap_write")
    token_response(httpx_mock)
    first = await resolve(repo, ref, project_id, WRITE)
    await resolve(repo, ref, project_id, READ)
    first.credential.expires_at = utcnow() - timedelta(seconds=1)
    session.add(first.credential)
    session.commit()
    token_response(httpx_mock, "synthetic-renewed")
    renewed = await resolve(repo, ref, project_id, WRITE)
    await resolve(repo, ref, project_id, READ)
    assert renewed.credential.credential_ref == ref
    assert renewed.config_json["access_mode"] == "sitemap_write"
    assert [assertion_scope(r) for r in httpx_mock.get_requests()] == [WRITE, WRITE]
    assert {row.scope for row in session.exec(select(CredentialScope)).all()} == {WRITE}


@pytest.mark.asyncio
async def test_default_readonly_is_not_promoted_and_mode_edit_reacquires(
    session, project_id, service_key, httpx_mock
):
    repo, ref = create(session, project_id, service_key)
    token_response(httpx_mock)
    await resolve(repo, ref, project_id, READ)
    with pytest.raises(ConflictError, match="missing required scopes"):
        await resolve(repo, ref, project_id, WRITE)
    assert len(httpx_mock.get_requests()) == 1
    repo.update_credential(
        credential_ref=ref,
        fields={"access_mode": "sitemap_write", "service_account_json": ""},
        display_name=None,
    )
    credential, _ = repo._global_credential(ref)
    assert credential.expires_at is None
    assert credential.config_json["scope_status"] == "unknown"
    assert session.exec(select(CredentialScope)).all() == []
    token_response(httpx_mock, "synthetic-write")
    await resolve(repo, ref, project_id, WRITE)
    repo.update_credential(credential_ref=ref, fields={}, display_name="Rename only")
    await resolve(repo, ref, project_id, READ)
    assert [assertion_scope(r) for r in httpx_mock.get_requests()] == [READ, WRITE]
    repo.update_credential(
        credential_ref=ref, fields={"access_mode": "readonly"}, display_name=None
    )
    token_response(httpx_mock, "synthetic-read-again")
    await resolve(repo, ref, project_id, READ)
    with pytest.raises(ConflictError, match="missing required scopes"):
        await resolve(repo, ref, project_id, WRITE)
    assert assertion_scope(httpx_mock.get_requests()[-1]) == READ


def test_invalid_mode_rejected_without_credential_disclosure(session, project_id, service_key):
    with pytest.raises(ValidationError) as caught:
        create(session, project_id, service_key, access_mode="SECRET-invalid")
    assert "PRIVATE KEY" not in str(caught.value)
    assert "SECRET-invalid" not in str(caught.value)


def test_oauth_scope_edit_requires_consent_and_keeps_application(session, project_id):
    repo = AuthRepository(session)
    saved = repo.store_credential(
        provider_key="google-search-console",
        auth_method_key="oauth2_authorization_code",
        display_name="Synthetic consent",
        attach_project_id=project_id,
        fields={"client_id": "synthetic-app", "client_secret": "synthetic-secret"},
    ).data
    credential, backing = repo._global_credential(saved.credential_ref)
    credential.status = "connected"
    credential.config_json = {**credential.config_json, "scope_status": "known"}
    session.add(credential)
    session.add(CredentialScope(credential_id=credential.id, scope=READ))
    IntegrationCredentialRepository(session).set(
        credential_ref=saved.credential_ref,
        provider_key="google-search-console",
        integration_credential_id=backing.id,
        secret_payload=json.dumps(
            {
                "client_id": "synthetic-app",
                "client_secret": "synthetic-secret",
                "access_token": "synthetic-old",
                "refresh_token": "synthetic-refresh",
            }
        ).encode(),
    )
    repo.update_credential(
        credential_ref=saved.credential_ref,
        fields={"access_mode": "sitemap_write"},
        display_name=None,
    )
    credential, backing = repo._global_credential(saved.credential_ref)
    assert credential.status == "pending"
    assert credential.config_json["scope_status"] == "unknown"
    assert session.exec(select(CredentialScope)).all() == []
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(backing.id))
    assert payload == {"client_id": "synthetic-app", "client_secret": "synthetic-secret"}


def test_scope_compatibility_does_not_promote_other_providers(session, project_id, service_key):
    repo, ref = create(session, project_id, service_key)
    credential, _ = repo._global_credential(ref)
    credential.provider_key = "google-analytics"
    credential.config_json = {**credential.config_json, "scope_status": "known"}
    session.add(credential)
    session.add(CredentialScope(credential_id=credential.id, scope=WRITE))
    session.commit()
    with pytest.raises(ConflictError, match="missing required scopes"):
        repo._require_scopes(credential=credential, required_scopes=(READ,))


@pytest.mark.asyncio
async def test_upgrade_repairs_unexpired_readonly_token_for_saved_write_mode(
    session, project_id, service_key, httpx_mock
):
    repo, ref = create(session, project_id, service_key)
    token_response(httpx_mock)
    previous = await resolve(repo, ref, project_id, READ)
    # Recreate 2.1.33: saved write choice survived, but its still-valid cached
    # token was acquired under the read-only package contract without request metadata.
    config = dict(previous.credential.config_json)
    config["access_mode"] = "sitemap_write"
    config.pop("scope_request_scopes")
    previous.credential.config_json = config
    session.add(previous.credential)
    session.commit()
    assert previous.credential.expires_at > utcnow() + timedelta(minutes=30)
    token_response(httpx_mock, "synthetic-recovered")
    recovered = await resolve(repo, ref, project_id, WRITE)
    await resolve(repo, ref, project_id, READ)
    assert recovered.credential.credential_ref == ref
    assert recovered.config_json["scope_request_scopes"] == [WRITE]
    assert [assertion_scope(r) for r in httpx_mock.get_requests()] == [READ, WRITE]


@pytest.mark.asyncio
async def test_narrower_provider_response_remains_authoritative_without_retry_loop(
    session, project_id, service_key, httpx_mock
):
    repo, ref = create(session, project_id, service_key, access_mode="sitemap_write")
    httpx_mock.add_response(
        url=TOKEN_URL,
        json={
            "access_token": "synthetic-narrow",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": READ,
        },
    )
    for _ in range(2):
        with pytest.raises(ConflictError, match="missing required scopes"):
            await resolve(repo, ref, project_id, WRITE)
    assert len(httpx_mock.get_requests()) == 1
    assert {row.scope for row in session.exec(select(CredentialScope)).all()} == {READ}


@pytest.mark.asyncio
async def test_readonly_selection_rejects_broader_provider_grant(
    session, project_id, service_key, httpx_mock
):
    repo, ref = create(session, project_id, service_key)
    httpx_mock.add_response(
        url=TOKEN_URL,
        json={
            "access_token": "synthetic-broader",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": WRITE,
        },
    )
    await resolve(repo, ref, project_id, READ)
    with pytest.raises(ConflictError):
        await resolve(repo, ref, project_id, WRITE)
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.parametrize(
    "method,fields",
    [
        ("oauth2_authorization_code", {"client_id": "synthetic-app", "client_secret": "synthetic"}),
        ("oauth2_access_token", {"access_token": "synthetic-access"}),
        (
            "oauth2_refresh_token",
            {
                "client_id": "synthetic-app",
                "client_secret": "synthetic",
                "refresh_token": "synthetic-refresh",
            },
        ),
        ("service-account", {}),
    ],
)
@pytest.mark.asyncio
async def test_readonly_scope_ceiling_applies_before_renewal_for_all_methods(
    session, project_id, service_key, httpx_mock, method, fields
):
    repo = AuthRepository(session)
    if method == "service-account":
        fields = {"service_account_json": service_key}
    saved = repo.store_credential(
        provider_key="google-search-console",
        auth_method_key=method,
        display_name="Synthetic broad grant",
        fields={**fields, "access_mode": "readonly"},
        attach_project_id=project_id,
    ).data
    credential, backing = repo._global_credential(saved.credential_ref)
    credential.status = "connected"
    credential.config_json = {**credential.config_json, "scope_status": "known"}
    credential.expires_at = utcnow() + timedelta(hours=1)
    session.add(credential)
    session.add(CredentialScope(credential_id=credential.id, scope=WRITE))
    IntegrationCredentialRepository(session).set(
        credential_ref=saved.credential_ref,
        provider_key="google-search-console",
        integration_credential_id=backing.id,
        secret_payload=json.dumps({**fields, "access_token": "synthetic-access"}).encode(),
    )
    await resolve(repo, saved.credential_ref, project_id, READ)
    with pytest.raises(ConflictError, match="selected access mode"):
        await resolve(repo, saved.credential_ref, project_id, WRITE)
    credential.expires_at = utcnow() - timedelta(seconds=1)
    session.add(credential)
    session.commit()
    with pytest.raises(ConflictError, match="selected access mode"):
        await resolve(repo, saved.credential_ref, project_id, WRITE)
    assert httpx_mock.get_requests() == []
