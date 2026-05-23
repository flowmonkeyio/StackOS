"""REST route tests for StackOS action audit rows."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from stackos.db.models import ActionCall, ActionCallStatus, Credential


def test_action_call_route_returns_redacted_audit_rows(
    api: TestClient,
    project_id: int,
) -> None:
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        credential = Credential(
            project_id=project_id,
            credential_ref="cred_123",
            provider_key="openai-images",
            auth_type="api-key",
        )
        session.add(credential)
        session.flush()
        row = ActionCall(
            project_id=project_id,
            action_key="image.generate",
            plugin_slug="utils",
            provider_key="openai-images",
            connector_key="openai-images",
            operation="image.generate",
            status=ActionCallStatus.SUCCESS,
            dry_run=False,
            credential_id=credential.id,
            idempotency_key="caller-secret-key",
            credential_ref="cred_123",
            request_json={"prompt": "test", "api_key": "secret"},
            response_json={"asset_url": "/generated-assets/test.webp", "token": "secret"},
            metadata_json={"credential_ref": "cred_123", "refresh_token": "secret"},
        )
        failed = ActionCall(
            project_id=project_id,
            action_key="image.generate",
            plugin_slug="utils",
            provider_key="openai-images",
            connector_key="openai-images",
            operation="image.generate",
            status=ActionCallStatus.FAILED,
            dry_run=False,
            credential_id=credential.id,
            credential_ref="cred_123",
            request_json={"prompt": "bad"},
            response_json=None,
            error="provider rejected request",
        )
        session.add(row)
        session.add(failed)
        session.commit()

    resp = api.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={"plugin_slug": "utils", "action_key": "image.generate", "status": "success"},
    )

    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1
    item = resp.json()["items"][0]
    assert item["status"] == "success"
    assert item["request_json"]["api_key"] == "[redacted]"
    assert item["response_json"]["token"] == "[redacted]"
    assert item["metadata_json"]["credential_ref"] == "cred_123"
    assert item["metadata_json"]["refresh_token"] == "[redacted]"
    assert "credential_id" not in item
    assert "idempotency_key" not in item
    assert "caller-secret-key" not in resp.text
