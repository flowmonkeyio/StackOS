"""Account verification evidence survives inventory reload without granting access."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.db.models import Credential, CredentialUsageEvent
from stackos.mcp.errors import IntegrationDownError


def test_account_inventory_keeps_latest_test_separate_from_connected_state(
    session: Session, project_id: int
) -> None:
    repo = AuthRepository(session)
    account = repo.store_credential(
        provider_key="firecrawl",
        display_name="Diagnostic history fixture",
        fields={"api_key": "fc-test-fixture"},
        attach_project_id=project_id,
    ).data
    assert repo.get_account(credential_ref=account.credential_ref).last_test is None
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == account.credential_ref)
    ).one()
    result = {
        "credential_ref": account.credential_ref,
        "provider_key": "firecrawl",
        "ok": False,
        "status": "failed",
        "summary": "Permission denied for the probe.",
        "checked_at": datetime.now(UTC).isoformat(),
        "retryable": False,
        "next_action": "Review the provider key permissions and test again.",
        "metadata": {"provider_status_code": 403, "api_key": "must-not-leak"},
    }
    repo.record_usage_event(
        credential=credential,
        provider_key="firecrawl",
        operation="account.test",
        status="failed",
        metadata_json={"ok": False, "metadata": result["metadata"], "result": result},
        project_id=project_id,
    )
    # A later action event is not evidence that the account probe passed.
    repo.record_usage_event(
        credential=credential,
        provider_key="firecrawl",
        operation="action.run",
        status="success",
        metadata_json={},
        project_id=project_id,
    )
    session.commit()
    for scope in (None, project_id):
        loaded = repo.status(project_id=scope, provider_key="firecrawl").accounts[0]
        assert loaded.status == "connected"
        assert loaded.setup_required is False
        assert loaded.last_test is not None
        assert loaded.last_test.ok is False
        assert loaded.last_test.summary == result["summary"]
        assert loaded.last_test.next_action == result["next_action"]
        assert loaded.last_test.retryable is False
        assert "must-not-leak" not in loaded.model_dump_json()
    result.update(ok=True, status="ok", summary="Authenticated", next_action=None)
    repo.record_usage_event(
        credential=credential,
        provider_key="firecrawl",
        operation="account.test",
        status="ok",
        metadata_json={"ok": True, "result": result},
    )
    session.commit()
    latest = repo.get_account(credential_ref=account.credential_ref)
    assert latest.last_test is not None
    assert latest.last_test.ok is True


def test_legacy_test_event_preserves_failure_without_inventing_diagnostics(
    session: Session, project_id: int
) -> None:
    repo = AuthRepository(session)
    account = repo.store_credential(
        provider_key="firecrawl",
        display_name="Legacy diagnostic fixture",
        fields={"api_key": "fc-legacy-fixture"},
        attach_project_id=project_id,
    ).data
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == account.credential_ref)
    ).one()
    repo.record_usage_event(
        credential=credential,
        provider_key="firecrawl",
        operation="account.test",
        status="failed",
        metadata_json={"ok": False, "metadata": {"stage": "test"}},
    )
    session.commit()
    loaded = repo.get_account(credential_ref=account.credential_ref)
    assert loaded.last_test is not None
    assert loaded.last_test.ok is False
    assert loaded.last_test.metadata == {"stage": "test"}
    assert loaded.last_test.next_action == "Test the Account again for current diagnostics."
    assert "permission" not in loaded.last_test.summary.lower()


@pytest.mark.parametrize(
    ("status", "error", "reason", "provider_reason", "retryable", "repair"),
    [
        (
            403,
            {
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "SERVICE_DISABLED",
                    }
                ]
            },
            "api_disabled",
            "SERVICE_DISABLED",
            False,
            "Enable the Google Search Console API",
        ),
        (
            403,
            {"errors": [{"reason": "accessNotConfigured"}]},
            "api_disabled",
            "accessNotConfigured",
            False,
            "Enable the Google Search Console API",
        ),
        (
            403,
            {"errors": [{"reason": "insufficientPermissions"}]},
            "permission_denied",
            "insufficientPermissions",
            False,
            "permissions",
        ),
        (
            403,
            {
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
                    }
                ]
            },
            "permission_denied",
            "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
            False,
            "permissions",
        ),
        (401, {}, "authentication_failed", None, False, "local Accounts"),
        (429, {}, "rate_limited", None, True, "Test again later"),
        (503, {}, "provider_unavailable", None, True, "Test again later"),
        (403, "<html>body-canary</html>", "permission_denied", None, False, "permissions"),
        (403, {"message": "body-canary" * 10000}, "permission_denied", None, False, "permissions"),
    ],
)
def test_google_probe_failure_preserves_safe_reason_in_test_and_inventory(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    status: int,
    error: dict | str,
    reason: str,
    provider_reason: str | None,
    retryable: bool,
    repair: str,
) -> None:
    """Exercise token acquisition, real wrapper failure, and durable inventory reads."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    repo = AuthRepository(session)
    account = repo.store_credential(
        provider_key="google-search-console",
        auth_method_key="service-account",
        display_name="Diagnostic service account",
        fields={
            "service_account_json": json.dumps(
                {
                    "type": "service_account",
                    "client_email": "reader@example-project.iam.gserviceaccount.com",
                    "private_key": private_key,
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            )
        },
        attach_project_id=project_id,
    ).data
    httpx_mock.add_response(
        url="https://oauth2.googleapis.com/token",
        json={"access_token": "token-canary", "token_type": "Bearer", "expires_in": 3600},
    )
    httpx_mock.add_response(
        url="https://www.googleapis.com/webmasters/v3/sites",
        status_code=status,
        **(
            {"text": error}
            if isinstance(error, str)
            else {
                "json": {
                    "error": {
                        "message": "body-canary",
                        "access_token": "secret-canary",
                        **error,
                    }
                }
            }
        ),
        is_reusable=True,
    )
    out = asyncio.run(repo.test(project_id=project_id, credential_ref=account.credential_ref)).data
    assert out.ok is False
    assert out.metadata["provider_status_code"] == status
    assert out.metadata["reason_code"] == reason
    assert out.metadata.get("provider_reason") == provider_reason
    assert out.retryable is retryable
    assert repair in (out.next_action or "")
    for scope in (None, project_id):
        loaded = repo.status(project_id=scope, provider_key="google-search-console").accounts[0]
        assert loaded.status == "connected"
        assert loaded.last_test == out
    event = session.exec(
        select(CredentialUsageEvent).where(CredentialUsageEvent.operation == "account.test")
    ).one()
    assert event.metadata_json["result"] == out.model_dump(mode="json")
    rendered = json.dumps(event.metadata_json)
    for forbidden in ("body-canary", "secret-canary", "token-canary", private_key):
        assert forbidden not in rendered


@pytest.mark.parametrize("provider_key", ["firecrawl", "google-search-console"])
@pytest.mark.parametrize(
    "data",
    [
        {
            "stage": "<script>hostile-canary</script>",
            "reason_code": "https://secret.invalid/hostile-canary",
        },
        {"stage": {"access_token": "hostile-canary"}, "reason_code": ["SERVICE_DISABLED"]},
        {"status": True, "reply_code": "hostile-canary"},
        {"status": "403", "provider_error": "<html>hostile-canary</html>"},
        {"status": 403, "provider_error": {"error": {"details": "hostile-canary"}}},
        {
            "status": 403,
            "provider_error": {
                "error": {"errors": [{"reason": {"access_token": "hostile-canary"}}]}
            },
        },
        {"status": 403, "provider_error": {"error": {"details": [{"reason": "SERVICE_DISABLED"}]}}},
        {
            "status": 403,
            "provider_error": {"error": {"errors": [{"reason": "hostile-canary" * 10000}]}},
        },
        {
            "status": 403,
            "provider_error": {
                "error": {"errors": [{}] * 10000 + [{"reason": "SERVICE_DISABLED"}]}
            },
        },
        {
            "status": 403,
            "provider_error": {
                "error": {
                    "errors": [{"reason": "SERVICE_DISABLED"}],
                    "private_key": (
                        "-----BEGIN PRIVATE KEY-----\nhostile-canary\n-----END PRIVATE KEY-----"
                    ),
                }
            },
        },
    ],
)
def test_thrown_probe_failure_does_not_echo_unreviewed_provider_data(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
    data: dict,
    provider_key: str,
) -> None:
    """Opaque provider payloads must not become summaries or arbitrary metadata."""

    class FailingIntegration:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def test_credentials(self) -> dict:
            raise IntegrationDownError("exception-message-hostile-canary", data=data)

    repo = AuthRepository(session)
    account = repo.store_credential(
        provider_key=provider_key,
        display_name="Failure fixture",
        auth_method_key="api_key" if provider_key == "firecrawl" else "oauth2_access_token",
        fields={
            "api_key"
            if provider_key == "firecrawl"
            else "access_token": "credential-hostile-canary"
        },
        attach_project_id=project_id,
    ).data
    monkeypatch.setattr(
        "stackos.auth_providers.repository.testing._integration_class_for",
        lambda _: FailingIntegration,
    )
    out = asyncio.run(repo.test(project_id=project_id, credential_ref=account.credential_ref)).data
    assert out.ok is False
    assert out.next_action
    assert out.metadata["stage"] == "test"
    assert out.metadata["reason_code"] != "api_disabled"
    assert set(out.metadata) <= {"stage", "reason_code", "provider_status_code"}
    if type(data.get("status")) is int and 400 <= data["status"] <= 599:
        assert out.metadata["provider_status_code"] == data["status"]
    else:
        assert "provider_status_code" not in out.metadata
    event = session.exec(
        select(CredentialUsageEvent).where(CredentialUsageEvent.operation == "account.test")
    ).one()
    rendered = out.model_dump_json() + json.dumps(event.metadata_json)
    assert "hostile-canary" not in rendered
    assert "<script>" not in rendered
    assert "BEGIN PRIVATE KEY" not in rendered
    assert len(out.model_dump_json()) < 1200
