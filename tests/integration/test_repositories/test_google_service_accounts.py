"""Service-account lifecycle proof using synthetic method metadata, never real keys."""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.auth_providers.repository.utils import utcnow
from stackos.db.models import (
    CredentialScope,
    CredentialUsageEvent,
    IntegrationCredential,
)
from stackos.repositories.base import ConflictError, NotFoundError, ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository

TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
TOKEN = {"access_token": "synthetic-access", "token_type": "Bearer", "expires_in": 3600}


@pytest.fixture
def service_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return json.dumps(
        {
            "type": "service_account",
            "client_email": "reader@example-project.iam.gserviceaccount.com",
            "private_key": key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode(),
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )


@pytest.fixture
def repo(session: Session, monkeypatch: pytest.MonkeyPatch) -> AuthRepository:
    repo = AuthRepository(session)
    repo.sync_providers()
    for key in (
        "google-search-console",
        "google-analytics",
        "google-tag-manager",
        "google-ads",
        "google-workspace",
    ):
        provider = repo._get_provider(key, sync=False)
        config = dict(provider.config_json or {})
        methods = [m for m in config["auth_methods"] if m["key"] != "service-account"]
        methods.append(
            {
                "key": "service-account",
                "label": "Service account",
                "auth_type": "oauth",
                "permission_verification": {
                    "evidence_source": "oauth_response",
                    "enforcement": "local_required",
                },
                "interactive": False,
                "payload_format": "json",
                "fields": [
                    {
                        "key": "service_account_json",
                        "label": "JSON key",
                        "secret": True,
                        "required": True,
                    },
                    *(
                        [{"key": "delegated_subject", "label": "Delegated user"}]
                        if key == "google-workspace"
                        else []
                    ),
                ],
            }
        )
        config["auth_methods"] = methods
        provider.config_json = config
        session.add(provider)
    session.commit()
    monkeypatch.setattr(AuthRepository, "sync_providers", lambda self: None)
    return repo


@pytest.mark.asyncio
async def test_first_acquisition_uses_shared_resolver(
    repo: AuthRepository,
    project_id: int,
    service_key: str,
    httpx_mock: HTTPXMock,
) -> None:
    stored = repo.store_credential(
        provider_key="google-search-console",
        auth_method_key="service-account",
        display_name="Synthetic reader",
        fields={"service_account_json": service_key},
        attach_project_id=project_id,
    ).data
    httpx_mock.add_response(
        url="https://oauth2.googleapis.com/token",
        json={
            "access_token": "synthetic-access",
            "token_type": "Bearer",
            "expires_in": 3600,
        },
    )
    resolved = await repo.resolve_for_execution(
        project_id=project_id,
        provider_key="google-search-console",
        credential_ref=stored.credential_ref,
        operation="test",
        required_scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
    )
    assert json.loads(resolved.secret_payload)["access_token"] == "synthetic-access"
    assert resolved.config_json["scope_evidence_source"] == "google_service_account_exchange"
    assert resolved.config_json["scope_evidence_basis"] == "accepted_signed_request"
    body = parse_qs(httpx_mock.get_request().content.decode())
    assert set(body) == {"grant_type", "assertion"}
    assert body["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]


def create(repo, service_key, project_id=None, provider="google-search-console", **fields):
    return repo.store_credential(
        provider_key=provider,
        auth_method_key="service-account",
        display_name="Synthetic",
        attach_project_id=project_id,
        fields={"service_account_json": service_key, **fields},
    ).data.credential_ref


async def resolve(repo, ref, project_id, scopes=(SCOPE,), provider="google-search-console"):
    return await repo.resolve_for_execution(
        project_id=project_id,
        provider_key=provider,
        credential_ref=ref,
        operation="test",
        required_scopes=scopes,
    )


@pytest.mark.asyncio
async def test_search_console_mode_change_reacquires_scope_without_fabricated_grants(
    session, project_id, service_key, httpx_mock
):
    repo = AuthRepository(session)
    full_scope = "https://www.googleapis.com/auth/webmasters"
    ref = create(repo, service_key, project_id)
    httpx_mock.add_response(url=TOKEN_URL, json=TOKEN)
    await resolve(repo, ref, project_id)
    with pytest.raises(ConflictError, match="access mode"):
        await resolve(repo, ref, project_id, scopes=(full_scope,))
    repo.update_credential(
        credential_ref=ref, fields={"access_mode": "sitemap_write"}, display_name=None
    )
    credential, row = repo._global_credential(ref)
    assert credential.expires_at is None
    assert credential.config_json["scope_status"] == "unknown"
    assert not session.exec(select(CredentialScope)).all()
    assert "access_token" not in json.loads(
        IntegrationCredentialRepository(session).get_decrypted(row.id)
    )
    httpx_mock.add_response(url=TOKEN_URL, json={**TOKEN, "access_token": "write-access"})
    await resolve(repo, ref, project_id, scopes=(full_scope,))
    await resolve(repo, ref, project_id)  # Full scope also permits existing reads.
    assert {s.scope for s in session.exec(select(CredentialScope)).all()} == {full_scope}
    assertion = parse_qs(httpx_mock.get_requests()[-1].content.decode())["assertion"][0]
    claims = json.loads(base64.urlsafe_b64decode(assertion.split(".")[1] + "=="))
    assert claims["scope"] == full_scope
    repo.update_credential(credential_ref=ref, fields={}, display_name="Same access")
    await resolve(repo, ref, project_id)
    assert len(httpx_mock.get_requests()) == 2
    repo.update_credential(
        credential_ref=ref, fields={"access_mode": "readonly"}, display_name=None
    )
    httpx_mock.add_response(url=TOKEN_URL, json=TOKEN)
    await resolve(repo, ref, project_id)
    assert {s.scope for s in session.exec(select(CredentialScope)).all()} == {SCOPE}


@pytest.mark.asyncio
async def test_search_console_full_scope_read_compatibility_does_not_enable_old_account_writes(
    session, project_id, service_key, httpx_mock
):
    repo = AuthRepository(session)
    full_scope = "https://www.googleapis.com/auth/webmasters"
    ref = create(repo, service_key, project_id)
    httpx_mock.add_response(url=TOKEN_URL, json={**TOKEN, "scope": full_scope})
    await resolve(repo, ref, project_id)
    assert {s.scope for s in session.exec(select(CredentialScope)).all()} == {full_scope}
    with pytest.raises(ConflictError, match="access mode"):
        await resolve(repo, ref, project_id, scopes=(full_scope,))
    credential, _ = repo._global_credential(ref)
    credential.config_json = {**credential.config_json, "scope_status": "unknown"}
    session.add(credential)
    session.commit()
    with pytest.raises(ConflictError, match="unknown"):
        await resolve(repo, ref, project_id)
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.asyncio
async def test_search_console_write_mode_still_requires_google_write_scope(
    session, project_id, service_key, httpx_mock
):
    repo = AuthRepository(session)
    ref = create(repo, service_key, project_id, access_mode="sitemap_write")
    httpx_mock.add_response(url=TOKEN_URL, json={**TOKEN, "scope": SCOPE})
    with pytest.raises(ConflictError, match="missing required scopes"):
        await resolve(repo, ref, project_id, scopes=("https://www.googleapis.com/auth/webmasters",))
    assert {s.scope for s in session.exec(select(CredentialScope)).all()} == {SCOPE}


@pytest.mark.asyncio
async def test_search_console_oauth_mode_change_requires_fresh_consent(
    session, project_id, settings, httpx_mock
):
    repo = AuthRepository(session)
    ref = repo.store_credential(
        provider_key="google-search-console",
        auth_method_key="oauth2_authorization_code",
        display_name="Interactive sitemap writer",
        attach_project_id=project_id,
        fields={"client_id": "synthetic-client", "client_secret": "synthetic-secret"},
    ).data.credential_ref

    def start():
        flow = repo.start(
            provider_key="google-search-console",
            auth_method_key="oauth2_authorization_code",
            credential_ref=ref,
            settings=settings,
        ).data
        return parse_qs(urlparse(flow.authorization_url).query)

    initial = start()
    assert initial["scope"] == [SCOPE]
    httpx_mock.add_response(
        url=TOKEN_URL, json={**TOKEN, "scope": SCOPE, "refresh_token": "old-refresh"}
    )
    await repo.complete_oauth_callback(
        state=initial["state"][0], code="read-code", settings=settings
    )
    stale = start()
    repo.update_credential(
        credential_ref=ref, fields={"access_mode": "sitemap_write"}, display_name=None
    )
    credential, row = repo._global_credential(ref)
    assert credential.status == "pending"
    assert not session.exec(select(CredentialScope)).all()
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id))
    assert not {"access_token", "refresh_token", "_oauth_pending"} & payload.keys()
    with pytest.raises(ConflictError, match="stale"):
        await repo.complete_oauth_callback(
            state=stale["state"][0], code="stale-code", settings=settings
        )
    with pytest.raises(ConflictError, match="not connected"):
        await resolve(repo, ref, project_id)
    writable = start()
    assert writable["scope"] == ["https://www.googleapis.com/auth/webmasters"]
    assert writable["prompt"] == ["consent"]
    httpx_mock.add_response(url=TOKEN_URL, json={**TOKEN, "scope": writable["scope"][0]})
    await repo.complete_oauth_callback(
        state=writable["state"][0], code="write-code", settings=settings
    )
    await resolve(repo, ref, project_id, scopes=tuple(writable["scope"]))
    await resolve(repo, ref, project_id)


@pytest.mark.asyncio
async def test_cache_expiry_and_concurrent_acquisition(
    repo, session, project_id, service_key, httpx_mock
):
    ref = create(repo, service_key, project_id)
    httpx_mock.add_response(url=TOKEN_URL, json=TOKEN)
    first, second = await asyncio.gather(
        resolve(repo, ref, project_id), resolve(repo, ref, project_id)
    )
    assert first.secret_payload == second.secret_payload
    await resolve(repo, ref, project_id)
    assert len(httpx_mock.get_requests()) == 1
    credential = first.credential
    credential.expires_at = utcnow() - timedelta(seconds=1)
    session.add(credential)
    session.commit()
    httpx_mock.add_response(url=TOKEN_URL, json={**TOKEN, "access_token": "renewed"})
    renewed = await resolve(repo, ref, project_id)
    assert json.loads(renewed.secret_payload)["access_token"] == "renewed"
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.parametrize("scope", ["", None, [], [SCOPE], {"scope": SCOPE}, 12])
@pytest.mark.asyncio
async def test_present_invalid_scopes_never_fall_back(
    repo, session, project_id, service_key, httpx_mock, scope
):
    ref = create(repo, service_key, project_id)
    httpx_mock.add_response(url=TOKEN_URL, json={**TOKEN, "scope": scope})
    with pytest.raises(ConflictError):
        await resolve(repo, ref, project_id)
    credential, row = repo._global_credential(ref)
    assert credential.status == "repair-required"
    assert credential.config_json["scope_status"] == "unknown"
    assert not session.exec(select(CredentialScope)).all()
    assert "access_token" not in json.loads(
        IntegrationCredentialRepository(session).get_decrypted(row.id)
    )


@pytest.mark.asyncio
async def test_present_scope_is_authoritative(repo, session, project_id, service_key, httpx_mock):
    ref = create(repo, service_key, project_id)
    httpx_mock.add_response(url=TOKEN_URL, json={**TOKEN, "scope": "different-scope"})
    with pytest.raises(ConflictError, match="missing required scopes"):
        await resolve(repo, ref, project_id)
    credential, _ = repo._global_credential(ref)
    assert credential.config_json["scope_evidence_basis"] == "provider_response"
    assert [row.scope for row in session.exec(select(CredentialScope)).all()] == ["different-scope"]


@pytest.mark.parametrize(
    "patch",
    [
        {"expires_in": 0},
        {"expires_in": -1},
        {"expires_in": True},
        {"expires_in": "3600"},
        {"expires_in": None},
        {"expires_in": float("nan")},
        {"expires_in": float("inf")},
        {"expires_in": 1e100},
        {"token_type": "MAC"},
        {"token_type": None},
        {"access_token": ""},
    ],
)
@pytest.mark.asyncio
async def test_malformed_exchange_is_not_committed(
    repo, project_id, service_key, httpx_mock, patch
):
    ref = create(repo, service_key, project_id)
    # Raw content permits deliberate non-finite JSON values for boundary tests.
    httpx_mock.add_response(url=TOKEN_URL, content=json.dumps({**TOKEN, **patch}))
    with pytest.raises(ConflictError):
        await resolve(repo, ref, project_id)
    credential, _ = repo._global_credential(ref)
    assert credential.config_json["scope_status"] == "unknown"
    assert credential.expires_at is None


@pytest.mark.parametrize(
    "status,error,retryable",
    [(400, "invalid_grant", False), (429, "rate_limit", True), (503, "busy", True)],
)
@pytest.mark.asyncio
async def test_terminal_vs_retryable_errors_are_safe(
    repo, session, project_id, service_key, httpx_mock, status, error, retryable
):
    ref = create(repo, service_key, project_id)
    httpx_mock.add_response(
        url=TOKEN_URL,
        status_code=status,
        json={"error": error, "error_description": "RAW PROVIDER SECRET ECHO"},
    )
    with pytest.raises(ConflictError) as exc:
        await resolve(repo, ref, project_id)
    assert exc.value.retryable is retryable
    assert "RAW PROVIDER SECRET ECHO" not in str(exc.value)
    credential, _ = repo._global_credential(ref)
    assert credential.status == ("connected" if retryable else "repair-required")
    assert "RAW PROVIDER SECRET ECHO" not in str(session.exec(select(CredentialUsageEvent)).all())


@pytest.mark.asyncio
async def test_no_redirect_following_or_credential_forwarding(
    repo, project_id, service_key, httpx_mock
):
    ref = create(repo, service_key, project_id)
    httpx_mock.add_response(
        url=TOKEN_URL, status_code=307, headers={"Location": "https://evil.test/token"}, json=TOKEN
    )
    with pytest.raises(ConflictError):
        await resolve(repo, ref, project_id)
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.asyncio
async def test_global_and_scoped_tests_share_acquisition_and_keep_attachments(
    repo, project_id, service_key, httpx_mock
):
    ref = create(repo, service_key, provider="google-ads")
    with pytest.raises(NotFoundError):
        await repo.test(project_id=project_id, credential_ref=ref)
    httpx_mock.add_response(url=TOKEN_URL, json=TOKEN)
    tested = (await repo.test(project_id=None, credential_ref=ref)).data
    assert tested.ok
    assert tested.metadata["resource_access"] == "unverified"
    assert tested.metadata["verification"] == "token_acquisition_only"
    repo.attach_account(project_id=project_id, credential_ref=ref)
    assert (await repo.test(project_id=project_id, credential_ref=ref)).data.ok
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.asyncio
async def test_rotation_and_subject_clear_invalidate_all_acquired_state(
    repo, session, project_id, service_key, httpx_mock
):
    ref = create(
        repo,
        service_key,
        project_id,
        provider="google-workspace",
        delegated_subject="reader@example.com",
    )
    httpx_mock.add_response(url=TOKEN_URL, json=TOKEN)
    initial = await resolve(repo, ref, project_id, scopes=(), provider="google-workspace")
    original_payload = initial.secret_payload
    for fields in ({}, {"service_account_json": ""}, {"service_account_json": "  "}):
        repo.update_credential(credential_ref=ref, fields=fields, display_name="Renamed")
        current = await resolve(repo, ref, project_id, scopes=(), provider="google-workspace")
        assert current.secret_payload == original_payload
    edit = repo.get_credential_edit_state(credential_ref=ref)
    assert edit.secret_present["service_account_json"]
    assert service_key not in str(edit)
    for fields in ({"delegated_subject": ""}, {"service_account_json": service_key + " "}):
        repo.update_credential(credential_ref=ref, fields=fields, display_name=None)
        credential, row = repo._global_credential(ref)
        assert credential.expires_at is None
        assert credential.config_json["scope_status"] == "unknown"
        assert "delegated_subject" not in credential.config_json
        assert "scope_evidence_source" not in credential.config_json
        assert not session.exec(select(CredentialScope)).all()
        assert "access_token" not in json.loads(
            IntegrationCredentialRepository(session).get_decrypted(row.id)
        )
        httpx_mock.add_response(url=TOKEN_URL, json=TOKEN)
        current = await resolve(repo, ref, project_id, scopes=(), provider="google-workspace")
        assert {row.scope for row in session.exec(select(CredentialScope)).all()} == {
            "https://www.googleapis.com/auth/calendar.events"
        }


@pytest.mark.asyncio
async def test_cas_rotation_during_exchange_cannot_install_old_subject_token(
    repo, session, project_id, service_key, httpx_mock
):
    ref = create(
        repo,
        service_key,
        project_id,
        provider="google-workspace",
        delegated_subject="before@example.com",
    )

    def rotate(request):
        repo.update_credential(
            credential_ref=ref, fields={"delegated_subject": "after@example.com"}, display_name=None
        )
        return httpx.Response(200, json=TOKEN)

    httpx_mock.add_callback(rotate, url=TOKEN_URL)
    with pytest.raises(ConflictError, match="changed during renewal"):
        await resolve(repo, ref, project_id, scopes=(), provider="google-workspace")
    credential, row = repo._global_credential(ref)
    assert credential.config_json["delegated_subject"] == "after@example.com"
    assert "access_token" not in json.loads(
        IntegrationCredentialRepository(session).get_decrypted(row.id)
    )
    httpx_mock.add_response(url=TOKEN_URL, json={**TOKEN, "access_token": "after-token"})
    current = await resolve(repo, ref, project_id, scopes=(), provider="google-workspace")
    assert json.loads(current.secret_payload)["access_token"] == "after-token"


def test_method_is_immutable_and_revocation_removes_key(repo, session, service_key):
    ref = create(repo, service_key)
    with pytest.raises(ValidationError, match="not declared"):
        repo.update_credential(
            credential_ref=ref, fields={"auth_method_key": "manual"}, display_name=None
        )
    _, row = repo._global_credential(ref)
    row_id = row.id
    assert repo.revoke(credential_ref=ref).data.status == "revoked"
    assert session.get(IntegrationCredential, row_id) is None


@pytest.mark.asyncio
async def test_waiting_acquisition_reloads_delegation_before_signing(
    repo,
    project_id,
    service_key,
    httpx_mock,
):
    ref = create(
        repo,
        service_key,
        project_id,
        provider="google-workspace",
        delegated_subject="before@example.com",
    )
    _, row = repo._global_credential(ref)
    lock = asyncio.Lock()
    repo._oauth_refresh_locks[(id(asyncio.get_running_loop()), row.id)] = lock
    await lock.acquire()
    started = asyncio.Event()

    async def waiting():
        started.set()
        return await resolve(repo, ref, project_id, scopes=(), provider="google-workspace")

    task = asyncio.create_task(waiting())
    await started.wait()
    try:
        repo.update_credential(
            credential_ref=ref, fields={"delegated_subject": ""}, display_name=None
        )
        httpx_mock.add_response(url=TOKEN_URL, json=TOKEN)
    finally:
        lock.release()
    await task
    assertion = parse_qs(httpx_mock.get_request().content.decode())["assertion"][0]
    encoded_claims = assertion.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(encoded_claims + "=" * (-len(encoded_claims) % 4)))
    assert "sub" not in claims
    assert claims["scope"] == "https://www.googleapis.com/auth/calendar.events"


@pytest.mark.asyncio
async def test_network_failure_keeps_account_retryable(repo, project_id, service_key, httpx_mock):
    ref = create(repo, service_key, project_id)
    httpx_mock.add_exception(httpx.ConnectError("RAW NETWORK ECHO"), url=TOKEN_URL)
    with pytest.raises(ConflictError) as exc:
        await resolve(repo, ref, project_id)
    assert exc.value.retryable
    assert "RAW NETWORK ECHO" not in str(exc.value)
    assert repo._global_credential(ref)[0].status == "connected"


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("global_test", [False, True])
@pytest.mark.asyncio
async def test_missing_service_account_method_denies_before_use(
    repo,
    session,
    project_id,
    service_key,
    httpx_mock,
    cached,
    global_test,
):
    ref = create(repo, service_key, project_id)
    if cached:
        httpx_mock.add_response(url=TOKEN_URL, json=TOKEN)
        await resolve(repo, ref, project_id)
    calls_before = len(httpx_mock.get_requests())
    provider = repo._get_provider("google-search-console", sync=False)
    config = dict(provider.config_json)
    config["auth_methods"] = [
        method for method in config["auth_methods"] if method["key"] != "service-account"
    ]
    provider.config_json = config
    session.add(provider)
    session.commit()
    # Optional response makes a regressed exchange observable without unused-mock noise.
    httpx_mock.add_response(url=TOKEN_URL, json=TOKEN, is_optional=True)
    with pytest.raises(ValidationError, match="saved service-account method is unavailable"):
        if global_test:
            await repo.test(project_id=None, credential_ref=ref)
        else:
            await resolve(repo, ref, project_id)
    assert len(httpx_mock.get_requests()) == calls_before


def test_injected_service_account_method_cannot_enable_unsupported_provider(
    repo,
    session,
    service_key,
    httpx_mock,
):
    google = repo._get_provider("google-search-console", sync=False)
    provider = repo._get_provider("firecrawl", sync=False)
    method = next(
        method
        for method in google.config_json["auth_methods"]
        if method["key"] == "service-account"
    )
    provider.config_json = {**dict(provider.config_json or {}), "auth_methods": [method]}
    session.add(provider)
    session.commit()
    with pytest.raises(ValidationError, match="does not support Google service accounts"):
        create(repo, service_key, provider="firecrawl")
    assert httpx_mock.get_requests() == []
