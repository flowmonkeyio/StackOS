"""Repository tests for the StackOS auth-provider boundary."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.db.models import (
    AuthProvider,
    Credential,
    CredentialAccount,
    CredentialRefreshEvent,
    CredentialScope,
    CredentialUsageEvent,
    IntegrationCredential,
)
from stackos.mcp.errors import IntegrationDownError
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository


def test_status_wraps_existing_credentials_with_opaque_refs(
    session: Session,
    project_id: int,
) -> None:
    stored = (
        AuthRepository(session)
        .store_credential(
            provider_key="firecrawl",
            display_name="Primary Firecrawl",
            fields={"api_key": "fc-secret"},
            attach_project_id=project_id,
        )
        .data
    )

    status = AuthRepository(session).status(project_id=project_id, provider_key="firecrawl")

    assert [provider.key for provider in status.providers] == ["firecrawl"]
    assert len(status.accounts) == 1
    connection = status.accounts[0]
    assert connection.credential_ref.startswith("cred_")
    assert connection.provider_key == "firecrawl"
    assert connection.status == "connected"
    assert connection.last_tested_at is None
    assert connection.setup_required is False

    credential = session.exec(
        select(Credential).where(Credential.credential_ref == stored.credential_ref)
    ).one()
    assert credential.credential_ref == connection.credential_ref
    assert credential.display_name == "Primary Firecrawl"
    assert credential.config_json == {"auth_method_key": "api_key"}


def _credential_for_account(
    session: Session,
    credential_ref: str,
) -> Credential:
    return session.exec(select(Credential).where(Credential.credential_ref == credential_ref)).one()


def _integration_for_account(
    session: Session,
    credential_ref: str,
) -> IntegrationCredential:
    credential = _credential_for_account(session, credential_ref)
    assert credential.integration_credential_id is not None
    row = session.get(IntegrationCredential, credential.integration_credential_id)
    assert row is not None
    return row


def _add_permission_probe_test_provider(session: Session) -> None:
    session.add(
        AuthProvider(
            key="permission-probe-test",
            name="Permission Probe Test",
            description="Test-only provider for saved-method evidence routing.",
            auth_type="oauth",
            config_json={
                "auth_methods": [
                    {
                        "key": "oauth-import",
                        "label": "OAuth import",
                        "auth_type": "oauth",
                        "payload_format": "raw",
                        "payload_field": "access_token",
                        "fields": [
                            {
                                "key": "access_token",
                                "label": "Access token",
                                "secret": True,
                                "required": True,
                            }
                        ],
                        "permission_verification": {
                            "evidence_source": "oauth_response",
                            "enforcement": "local_required",
                        },
                    },
                    {
                        "key": "static-token",
                        "label": "Static token",
                        "auth_type": "api-key",
                        "payload_format": "raw",
                        "payload_field": "access_token",
                        "fields": [
                            {
                                "key": "access_token",
                                "label": "Access token",
                                "secret": True,
                                "required": True,
                            }
                        ],
                        "permission_verification": {
                            "evidence_source": "provider_probe",
                            "enforcement": "local_required",
                        },
                    },
                ]
            },
        )
    )
    session.commit()


def test_auth_test_uses_saved_method_for_probe_context_and_evidence(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_probe_contexts: list[object] = []

    class _ProbeIntegration:
        def __init__(self, *, probe_context: object, **_kwargs: object) -> None:
            seen_probe_contexts.append(probe_context)

        async def test_credentials(self) -> dict[str, object]:
            return {
                "ok": True,
                "metadata": {
                    "evidence": {
                        "grants": ["records.read"],
                        "account": {
                            "provider_account_id": "account-1",
                            "display_name": "Test account",
                            "metadata": {"region": "us"},
                        },
                    }
                },
            }

    _add_permission_probe_test_provider(session)
    repo = AuthRepository(session)
    methods = {
        method.key: method
        for method in repo.list_providers(provider_key="permission-probe-test")[0].auth_methods
    }
    assert methods["static-token"].permission_verification is not None
    assert methods["static-token"].permission_verification.evidence_source == "provider_probe"
    oauth = repo.store_credential(
        attach_project_id=project_id,
        provider_key="permission-probe-test",
        auth_method_key="oauth-import",
        display_name="oauth",
        fields={"access_token": "identical-token-shape"},
    ).data
    static = repo.store_credential(
        attach_project_id=project_id,
        provider_key="permission-probe-test",
        auth_method_key="static-token",
        display_name="static",
        fields={"access_token": "identical-token-shape"},
    ).data
    monkeypatch.setattr(
        "stackos.auth_providers.repository.testing._integration_class_for",
        lambda kind: _ProbeIntegration if kind == "permission-probe-test" else None,
    )

    oauth_result = asyncio.run(
        repo.test(project_id=project_id, credential_ref=oauth.credential_ref)
    ).data
    static_result = asyncio.run(
        repo.test(project_id=project_id, credential_ref=static.credential_ref)
    ).data

    assert [context.auth_method_key for context in seen_probe_contexts] == [
        "oauth-import",
        "static-token",
    ]
    assert seen_probe_contexts[0].permission_verification.evidence_source == "oauth_response"
    assert seen_probe_contexts[1].permission_verification.evidence_source == "provider_probe"
    assert oauth_result.metadata["evidence"]["grants"] == ["records.read"]
    assert static_result.metadata["evidence"]["grants"] == ["records.read"]

    credentials = {
        credential.display_name: credential
        for credential in session.exec(select(Credential)).all()
        if credential.provider_key == "permission-probe-test"
    }
    oauth_scopes = session.exec(
        select(CredentialScope).where(CredentialScope.credential_id == credentials["oauth"].id)
    ).all()
    static_scopes = session.exec(
        select(CredentialScope).where(CredentialScope.credential_id == credentials["static"].id)
    ).all()
    static_account = session.exec(
        select(CredentialAccount).where(CredentialAccount.credential_id == credentials["static"].id)
    ).one()

    assert oauth_scopes == []
    assert [scope.scope for scope in static_scopes] == ["records.read"]
    assert (credentials["oauth"].config_json or {})["scope_status"] == "unknown"
    assert (credentials["static"].config_json or {})["scope_status"] == "known"
    assert static_account.provider_account_id == "account-1"
    assert static_account.display_name == "Test account"
    resolved = asyncio.run(
        repo.resolve_for_execution(
            project_id=project_id,
            provider_key="permission-probe-test",
            credential_ref=static.credential_ref,
            operation="test.permission-probe-scope-gate",
            required_scopes=["records.read"],
        )
    )
    assert resolved.credential.credential_ref == static.credential_ref
    with pytest.raises(ConflictError, match="missing required scopes"):
        asyncio.run(
            repo.resolve_for_execution(
                project_id=project_id,
                provider_key="permission-probe-test",
                credential_ref=static.credential_ref,
                operation="test.permission-probe-scope-gate",
                required_scopes=["records.write"],
            )
        )


def test_account_update_preserves_method_and_duplicate_name_is_rejected(
    session: Session,
    project_id: int,
) -> None:
    _add_permission_probe_test_provider(session)
    repo = AuthRepository(session)
    stored = repo.store_credential(
        attach_project_id=project_id,
        provider_key="permission-probe-test",
        auth_method_key="static-token",
        display_name="shared",
        fields={"access_token": "original-static-token"},
    ).data
    rotated = repo.update_credential(
        credential_ref=stored.credential_ref,
        fields={"access_token": "rotated-static-token"},
        display_name=None,
    ).data
    assert rotated.credential_ref == stored.credential_ref

    with pytest.raises(ConflictError, match="already exists"):
        repo.store_credential(
            attach_project_id=project_id,
            provider_key="permission-probe-test",
            auth_method_key="oauth-import",
            display_name="shared",
            fields={"access_token": "replacement-oauth-token"},
        )

    credential = session.exec(
        select(Credential).where(Credential.credential_ref == stored.credential_ref)
    ).one()
    assert credential.integration_credential_id is not None
    assert credential.auth_method_key == "static-token"
    assert IntegrationCredentialRepository(session).get_decrypted(
        credential.integration_credential_id
    ) == (b"rotated-static-token")


def test_secret_fields_preserve_significant_whitespace(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)
    password = "  leading and trailing whitespace\t"

    stored = repo.store_credential(
        attach_project_id=project_id,
        provider_key="ftp",
        auth_method_key="ftp-password",
        display_name="primary",
        fields={
            "password": password,
            "host": "ftp.example.com",
            "port": 21,
            "tls_mode": "none",
            "username": "deploy",
            "passive_mode": True,
            "timeout_s": 30,
            "encoding": "utf-8",
        },
    ).data

    row = _integration_for_account(session, stored.credential_ref)
    assert row.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id))
    assert payload["password"] == password


def test_status_preserves_failed_account_state(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)
    repo.store_credential(
        provider_key="firecrawl",
        display_name="Firecrawl - Default",
        fields={"api_key": "fc-secret"},
        attach_project_id=project_id,
    )
    status = repo.status(project_id=project_id, provider_key="firecrawl")
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == status.accounts[0].credential_ref)
    ).one()
    credential.status = "failed"
    session.add(credential)
    session.commit()

    status = repo.status(project_id=project_id, provider_key="firecrawl")

    assert status.accounts[0].status == "failed"
    assert status.accounts[0].setup_required is True


def test_credential_edit_preserves_omitted_secret_and_validates_host(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        attach_project_id=project_id,
        provider_key="ftp",
        auth_method_key="ftp-password",
        display_name="Production FTP",
        fields={
            "password": "  exact password  ",
            "host": "old.example.test",
            "username": "deploy",
            "tls_mode": "none",
        },
    ).data

    edit = repo.get_credential_edit_state(
        credential_ref=stored.credential_ref,
    )
    assert edit.values["host"] == "old.example.test"
    assert "password" not in edit.values
    assert edit.secret_present == {"password": True}

    updated = repo.update_credential(
        credential_ref=stored.credential_ref,
        fields={"host": "192.0.2.10"},
        display_name="Production FTP",
    ).data
    assert updated.credential_ref == stored.credential_ref

    row = _integration_for_account(session, stored.credential_ref)
    assert row.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id))
    assert payload["password"] == "  exact password  "
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == stored.credential_ref)
    ).one()
    assert credential.config_json["host"] == "192.0.2.10"

    with pytest.raises(ValidationError):
        repo.update_credential(
            credential_ref=stored.credential_ref,
            fields={"host": "ftp://192.0.2.10/public_html"},
            display_name=None,
        )
    with pytest.raises(ValidationError):
        repo.update_credential(
            credential_ref=stored.credential_ref,
            fields={"password": ""},
            display_name=None,
        )

    session.refresh(row)
    session.refresh(credential)
    assert credential.config_json["host"] == "192.0.2.10"
    assert (
        json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id))["password"]
        == "  exact password  "
    )


def test_s3_account_keeps_all_aws_keys_secret_and_preserves_them_on_safe_edit(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        attach_project_id=project_id,
        provider_key="aws-s3",
        auth_method_key="aws-access-key",
        display_name="Production S3",
        fields={
            "access_key_id": "AKIAEXPLICIT12345678",
            "secret_access_key": "exact-secret-access-key",
            "session_token": "exact-session-token",
            "bucket": "stackos-production",
            "prefix": " data ",
            "region": "us-west-2",
        },
    ).data

    edit = repo.get_credential_edit_state(credential_ref=stored.credential_ref)
    assert edit.values == {
        "bucket": "stackos-production",
        "prefix": "data/",
        "region": "us-west-2",
    }
    assert edit.secret_present == {
        "access_key_id": True,
        "secret_access_key": True,
        "session_token": True,
    }

    repo.update_credential(
        credential_ref=stored.credential_ref,
        fields={"prefix": "data/solar", "region": "us-east-2"},
        display_name=None,
    )
    row = _integration_for_account(session, stored.credential_ref)
    assert row.id is not None
    assert json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id)) == {
        "access_key_id": "AKIAEXPLICIT12345678",
        "secret_access_key": "exact-secret-access-key",
        "session_token": "exact-session-token",
    }
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == stored.credential_ref)
    ).one()
    assert credential.config_json["region"] == "us-east-2"
    assert credential.config_json["prefix"] == "data/solar/"
    serialized_account = json.dumps(stored.model_dump(mode="json"))
    assert "AKIAEXPLICIT12345678" not in serialized_account
    assert "exact-secret-access-key" not in serialized_account
    assert "exact-session-token" not in serialized_account

    with pytest.raises(ValidationError, match=r"unknown selection|supported AWS"):
        repo.update_credential(
            credential_ref=stored.credential_ref,
            fields={"region": "moon-west-1"},
            display_name=None,
        )
    with pytest.raises(ValidationError, match="relative path"):
        repo.update_credential(
            credential_ref=stored.credential_ref,
            fields={"prefix": "../outside"},
            display_name=None,
        )

    session.refresh(row)
    session.refresh(credential)
    assert credential.config_json["region"] == "us-east-2"
    assert credential.config_json["prefix"] == "data/solar/"
    assert json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id)) == {
        "access_key_id": "AKIAEXPLICIT12345678",
        "secret_access_key": "exact-secret-access-key",
        "session_token": "exact-session-token",
    }


def test_s3_account_upgrade_preserves_preexisting_noncommercial_region(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        attach_project_id=project_id,
        provider_key="aws-s3",
        auth_method_key="aws-access-key",
        display_name="Existing GovCloud S3",
        fields={
            "access_key_id": "AKIAGOVEXPLICIT12345",
            "secret_access_key": "exact-gov-secret",
            "bucket": "stackos-gov-existing",
            "prefix": "archive/",
            "region": "us-west-2",
        },
    ).data
    credential = _credential_for_account(session, stored.credential_ref)
    credential.config_json = {
        **credential.config_json,
        "region": "us-gov-west-1",
    }
    session.add(credential)
    session.commit()

    edit = repo.get_credential_edit_state(credential_ref=stored.credential_ref)
    assert edit.values["region"] == "us-gov-west-1"
    repo.update_credential(
        credential_ref=stored.credential_ref,
        fields={"prefix": "archive/2026"},
        display_name=None,
    )

    session.refresh(credential)
    assert credential.config_json["region"] == "us-gov-west-1"
    assert credential.config_json["prefix"] == "archive/2026/"


def test_thrown_auth_test_failure_is_sanitized_and_persisted(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingIntegration:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def test_credentials(self) -> dict[str, object]:
            raise IntegrationDownError(
                "provider secret=do-not-store",
                data={
                    "stage": "connect",
                    "reason_code": "connection_refused",
                    "retryable": True,
                    "password": "do-not-store",
                },
            )

    repo = AuthRepository(session)
    stored = repo.store_credential(
        attach_project_id=project_id,
        provider_key="firecrawl",
        auth_method_key="api_key",
        display_name="primary",
        fields={"api_key": "do-not-store"},
    ).data
    monkeypatch.setattr(
        "stackos.auth_providers.repository.testing._integration_class_for",
        lambda kind: _FailingIntegration if kind == "firecrawl" else None,
    )

    tested = asyncio.run(
        repo.test(project_id=project_id, credential_ref=stored.credential_ref)
    ).data
    resolved = asyncio.run(
        repo.resolve_for_execution(
            project_id=project_id,
            provider_key="firecrawl",
            credential_ref=stored.credential_ref,
            operation="test.after-failed-auth-test",
        )
    )
    refreshed = repo.status(project_id=project_id, provider_key="firecrawl").accounts[0]
    event = session.exec(
        select(CredentialUsageEvent).where(CredentialUsageEvent.operation == "account.test")
    ).one()

    assert tested.ok is False
    assert tested.metadata["stage"] == "connect"
    assert tested.metadata["reason_code"] == "connection_refused"
    assert resolved.credential.status == "connected"
    assert refreshed.status == "connected"
    assert refreshed.setup_required is False
    assert refreshed.last_tested_at is not None
    rendered = json.dumps(
        {
            "result": tested.model_dump(mode="json"),
            "connection": refreshed.model_dump(mode="json"),
            "event": event.metadata_json,
        }
    )
    assert "do-not-store" not in rendered


def test_reddit_auth_test_acquires_core_token_before_connector(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        attach_project_id=project_id,
        provider_key="reddit",
        auth_method_key="client_credentials",
        display_name="research",
        fields={
            "client_id": "reddit-client-id",
            "client_secret": "reddit-client-secret",
            "user_agent": "stackos:test-suite:v1",
        },
    ).data
    httpx_mock.add_response(
        method="POST",
        url="https://www.reddit.com/api/v1/access_token",
        json={
            "access_token": "reddit-access-value",
            "token_type": "bearer",
            "expires_in": 3600,
        },
    )

    tested = asyncio.run(
        repo.test(project_id=project_id, credential_ref=stored.credential_ref)
    ).data

    request = httpx_mock.get_requests()[0]
    form = parse_qs(request.content.decode())
    assert tested.ok is True
    assert tested.status == "ok"
    assert request.headers["Authorization"].startswith("Basic ")
    assert request.headers["User-Agent"] == "stackos:test-suite:v1"
    assert form == {"grant_type": ["client_credentials"]}
    row = _integration_for_account(session, stored.credential_ref)
    assert row.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id))
    assert payload["access_token"] == "reddit-access-value"
    assert payload["client_secret"] == "reddit-client-secret"


def test_acquired_client_credential_profile_can_be_edited_without_losing_token(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        attach_project_id=project_id,
        provider_key="reddit",
        auth_method_key="client_credentials",
        display_name="editable-research",
        fields={
            "client_id": "reddit-client-id",
            "client_secret": "reddit-client-secret",
            "user_agent": "stackos:editable-test:v1",
        },
    ).data
    httpx_mock.add_response(
        method="POST",
        url="https://www.reddit.com/api/v1/access_token",
        json={"access_token": "reddit-access-value", "expires_in": 3600},
    )
    asyncio.run(repo.test(project_id=project_id, credential_ref=stored.credential_ref))

    edit = repo.get_credential_edit_state(
        credential_ref=stored.credential_ref,
    )
    assert edit.secret_present == {
        "client_id": True,
        "client_secret": True,
        "user_agent": True,
    }
    updated = repo.update_credential(
        credential_ref=stored.credential_ref,
        fields={},
        display_name="Editable Reddit",
    ).data

    assert updated.status == "connected"
    assert updated.display_name == "Editable Reddit"
    assert len(httpx_mock.get_requests()) == 1
    row = _integration_for_account(session, stored.credential_ref)
    assert row.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id))
    assert payload["access_token"] == "reddit-access-value"
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == stored.credential_ref)
    ).one()
    assert (credential.config_json or {}).get("scope_status") == "known"


def test_pending_interactive_profile_can_be_edited_without_reentering_secrets(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        attach_project_id=project_id,
        provider_key="google-search-console",
        auth_method_key="oauth2_authorization_code",
        display_name="editable-pending",
        fields={
            "client_id": "google-client-id",
            "client_secret": "google-client-secret",
            "default_site_url": "https://example.test/",
        },
    ).data

    edit = repo.get_credential_edit_state(
        credential_ref=stored.credential_ref,
    )
    assert edit.secret_present == {"client_id": True, "client_secret": True}
    updated = repo.update_credential(
        credential_ref=stored.credential_ref,
        fields={"default_site_url": "https://updated.example.test/"},
        display_name="Pending Google",
    ).data

    assert updated.status == "pending"
    row = _integration_for_account(session, stored.credential_ref)
    assert row.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id))
    assert payload["_oauth_application_pending"] == {
        "client_id": "google-client-id",
        "client_secret": "google-client-secret",
    }
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == stored.credential_ref)
    ).one()
    assert credential.config_json["default_site_url"] == "https://updated.example.test/"


def test_telegram_bot_store_generates_webhook_secret(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)

    stored = repo.store_credential(
        attach_project_id=project_id,
        provider_key="telegram-bot",
        auth_method_key="bot-token",
        display_name="support",
        fields={"bot_token": "123456:ABC"},
    ).data

    row = _integration_for_account(session, stored.credential_ref)
    assert row.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id).decode())
    assert stored.credential_ref.startswith("cred_")
    assert payload["bot_token"] == "123456:ABC"
    assert isinstance(payload["webhook_secret_token"], str)
    assert len(payload["webhook_secret_token"]) >= 32
    credential = _credential_for_account(session, stored.credential_ref)
    assert credential.config_json["provider_account_id"] == "123456"
    assert "webhook_secret_token" not in credential.config_json


def test_telegram_bot_token_can_only_claim_one_active_account(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)

    first = repo.store_credential(
        attach_project_id=project_id,
        provider_key="telegram-bot",
        auth_method_key="bot-token",
        display_name="support",
        fields={"bot_token": "123456:ABC"},
    ).data
    replacement = repo.update_credential(
        credential_ref=first.credential_ref,
        fields={"bot_token": "123456:ROTATED"},
        display_name=None,
    ).data

    assert replacement.credential_ref == first.credential_ref
    with pytest.raises(ConflictError) as exc:
        repo.store_credential(
            attach_project_id=project_id,
            provider_key="telegram-bot",
            auth_method_key="bot-token",
            display_name="analytics",
            fields={"bot_token": "123456:ROTATED"},
        )
    assert exc.value.data["provider_account_id"] == "123456"
    assert exc.value.data["existing_credential_ref"] == first.credential_ref


def test_slack_bot_auth_test_syncs_safe_workspace_account(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)

    stored = repo.store_credential(
        attach_project_id=project_id,
        provider_key="slack-bot",
        auth_method_key="bot-token",
        display_name="support",
        fields={
            "bot_token": "xoxb-1234567890-safe-test-token",
            "signing_secret": "slack-signing-secret",
        },
    ).data
    row = _integration_for_account(session, stored.credential_ref)
    assert row.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id).decode())
    assert payload["bot_token"] == "xoxb-1234567890-safe-test-token"
    assert payload["signing_secret"] == "slack-signing-secret"
    credential = _credential_for_account(session, stored.credential_ref)
    assert "bot_token" not in credential.config_json
    assert "signing_secret" not in credential.config_json

    httpx_mock.add_response(
        method="POST",
        url="https://slack.com/api/auth.test",
        json={
            "ok": True,
            "team_id": "T123",
            "team": "Acme",
            "user_id": "U_BOT",
            "user": "stackos",
            "bot_id": "B123",
            "url": "https://acme.slack.com/",
        },
    )
    tested = asyncio.run(repo.test(project_id=project_id, credential_ref=stored.credential_ref))
    account = session.exec(select(CredentialAccount)).one()
    rendered = json.dumps(tested.data.model_dump(mode="json"))

    assert tested.data.ok is True
    assert tested.data.metadata["team_id"] == "T123"
    assert account.provider_account_id == "T123"
    assert account.display_name == "Acme"
    assert account.metadata_json["bot_id"] == "B123"
    assert "xoxb-1234567890-safe-test-token" not in rendered
    assert "slack-signing-secret" not in rendered


def test_failed_detached_account_can_be_revoked(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        provider_key="firecrawl",
        display_name="Firecrawl - Default",
        fields={"api_key": "fc-secret"},
        attach_project_id=project_id,
    ).data
    credential = _credential_for_account(session, stored.credential_ref)
    credential.status = "failed"
    session.add(credential)
    session.commit()

    repo.detach_account(
        project_id=project_id,
        credential_ref=credential.credential_ref,
    )
    revoked = repo.revoke(credential_ref=credential.credential_ref).data

    assert revoked.credential_ref == credential.credential_ref
    assert revoked.revoked_at is not None


def test_openrouter_auth_test_passes_safe_attribution_config(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        attach_project_id=project_id,
        provider_key="openrouter",
        auth_method_key="api_key",
        display_name="default",
        fields={
            "api_key": "or-secret",
            "http_referer": "https://stackos.local",
            "app_title": "StackOS",
        },
    ).data
    row = _integration_for_account(session, stored.credential_ref)
    assert row.id is not None
    assert IntegrationCredentialRepository(session).get_decrypted(row.id) == b"or-secret"
    credential = _credential_for_account(session, stored.credential_ref)
    assert credential.config_json["http_referer"] == "https://stackos.local"
    assert credential.config_json["app_title"] == "StackOS"
    httpx_mock.add_response(
        method="GET",
        url="https://openrouter.ai/api/v1/models",
        json={"data": [{"id": "openai/gpt-4.1"}]},
    )

    tested = asyncio.run(repo.test(project_id=project_id, credential_ref=stored.credential_ref))
    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(tested.data.model_dump(mode="json"))

    assert tested.data.ok is True
    assert tested.data.metadata["models_count"] == 1
    assert request.headers["Authorization"] == "Bearer or-secret"
    assert request.headers["HTTP-Referer"] == "https://stackos.local"
    assert request.headers["X-OpenRouter-Title"] == "StackOS"
    assert "or-secret" not in rendered


def test_trackbooth_auth_test_passes_safe_custom_api_url_config(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        attach_project_id=project_id,
        provider_key="trackbooth",
        auth_method_key="api-key",
        display_name="local",
        fields={
            "api_key": "tb-secret",
            "api_base_url": "http://localhost:3030",
        },
    ).data
    row = _integration_for_account(session, stored.credential_ref)
    assert row.id is not None
    payload = json.loads(IntegrationCredentialRepository(session).get_decrypted(row.id).decode())
    assert payload == {"api_key": "tb-secret"}
    credential = _credential_for_account(session, stored.credential_ref)
    assert credential.config_json["api_base_url"] == "http://localhost:3030"

    httpx_mock.add_response(
        method="GET",
        url="http://localhost:3030/api/agent-api/catalog",
        json={"data": [{"operation_id": "LinksController.findAll"}]},
    )

    tested = asyncio.run(repo.test(project_id=project_id, credential_ref=stored.credential_ref))
    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(tested.data.model_dump(mode="json"))

    assert tested.data.ok is True
    assert tested.data.metadata["endpoint_count"] == 1
    assert tested.data.metadata["api_base_url"] == "http://localhost:3030"
    assert request.headers["X-API-Key"] == "tb-secret"
    assert "tb-secret" not in rendered


def test_shopify_auth_test_passes_static_token_and_safe_store_config(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        attach_project_id=project_id,
        provider_key="shopify",
        auth_method_key="admin-api-token",
        display_name="primary",
        fields={
            "admin_api_access_token": "shpat-secret",
            "store_domain": "demo.myshopify.com",
            "api_version": "2026-07",
        },
    ).data
    row = _integration_for_account(session, stored.credential_ref)
    assert row.id is not None
    assert IntegrationCredentialRepository(session).get_decrypted(row.id) == b"shpat-secret"
    credential = _credential_for_account(session, stored.credential_ref)
    assert credential.config_json["store_domain"] == "demo.myshopify.com"
    assert credential.config_json["api_version"] == "2026-07"

    httpx_mock.add_response(
        method="POST",
        url="https://demo.myshopify.com/admin/api/2026-07/graphql.json",
        json={
            "data": {
                "shop": {
                    "id": "gid://shopify/Shop/1",
                    "name": "Demo Shop",
                    "myshopifyDomain": "demo.myshopify.com",
                }
            }
        },
    )

    tested = asyncio.run(repo.test(project_id=project_id, credential_ref=stored.credential_ref))
    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(tested.data.model_dump(mode="json"))

    assert tested.data.ok is True
    assert tested.data.metadata["shop_name"] == "Demo Shop"
    assert tested.data.metadata["store_domain"] == "demo.myshopify.com"
    assert request.headers["X-Shopify-Access-Token"] == "shpat-secret"
    assert "shpat-secret" not in rendered


def test_cloudflare_auth_test_verifies_token_without_zone_permission(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        attach_project_id=project_id,
        provider_key="cloudflare",
        auth_method_key="api_token",
        display_name="primary",
        fields={"api_token": "cloudflare-secret"},
    ).data
    row = _integration_for_account(session, stored.credential_ref)
    assert row.id is not None
    assert IntegrationCredentialRepository(session).get_decrypted(row.id) == b"cloudflare-secret"
    credential = _credential_for_account(session, stored.credential_ref)
    assert credential.config_json == {"auth_method_key": "api_token"}

    httpx_mock.add_response(
        method="GET",
        url="https://api.cloudflare.com/client/v4/user/tokens/verify",
        json={
            "success": True,
            "errors": [],
            "messages": [],
            "result": {"id": "token-id", "status": "active"},
        },
    )

    tested = asyncio.run(repo.test(project_id=project_id, credential_ref=stored.credential_ref))
    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(tested.data.model_dump(mode="json"))

    assert request.headers["Authorization"] == "Bearer cloudflare-secret"
    assert tested.data.ok is True
    assert tested.data.metadata["zone_read_verified"] is False
    assert tested.data.metadata["dns_read_verified"] is False
    assert tested.data.metadata["dns_write_verified"] is False
    assert "token-id" not in rendered
    assert "cloudflare-secret" not in rendered


def test_usage_and_refresh_events_redact_secret_metadata(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        provider_key="firecrawl",
        display_name="Firecrawl - Default",
        fields={"api_key": "fc-secret"},
        attach_project_id=project_id,
    ).data
    credential = _credential_for_account(session, stored.credential_ref)

    repo.record_usage_event(
        credential=credential,
        provider_key="firecrawl",
        operation="account.test",
        status="ok",
        metadata_json={"access_token": "tok", "nested": {"api_key": "secret"}},
        project_id=project_id,
    )
    repo.record_refresh_event(
        credential=credential,
        provider_key="firecrawl",
        status="refreshed",
        metadata_json={"refresh_token": "rt", "safe": "value"},
    )
    session.commit()

    usage = session.exec(
        select(CredentialUsageEvent).where(CredentialUsageEvent.operation == "account.test")
    ).one()
    refresh = session.exec(select(CredentialRefreshEvent)).one()

    assert usage.metadata_json == {
        "access_token": "[redacted]",
        "nested": {"api_key": "[redacted]"},
    }
    assert refresh.metadata_json == {"refresh_token": "[redacted]", "safe": "value"}


def test_auth_test_redacts_vendor_controlled_text_fields(
    session: Session,
    project_id: int,
    monkeypatch,
) -> None:
    class _TextLeakIntegration:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def test_credentials(self) -> dict:
            return {
                "ok": False,
                "vendor": "firecrawl",
                "status": "failed api_key=fc-secret",
                "summary": "Authorization: Bearer fc-secret",
                "next_action": "rotate refresh_token=rt-secret",
                "metadata": {"access_token": "tok-secret"},
            }

    repo = AuthRepository(session)
    stored = repo.store_credential(
        provider_key="firecrawl",
        display_name="Firecrawl - Default",
        fields={"api_key": "fc-secret"},
        attach_project_id=project_id,
    ).data
    monkeypatch.setattr(
        "stackos.auth_providers.repository.integration_class_for",
        lambda kind: _TextLeakIntegration if kind == "firecrawl" else None,
    )
    out = asyncio.run(
        repo.test(
            project_id=project_id,
            credential_ref=stored.credential_ref,
        )
    ).data

    assert out.status == "failed api_key=[redacted]"
    assert out.summary == "Authorization: Bearer [redacted]"
    assert out.next_action == "rotate refresh_token=[redacted]"
    assert out.metadata["access_token"] == "[redacted]"
