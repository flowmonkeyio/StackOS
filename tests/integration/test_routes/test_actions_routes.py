"""REST route tests for StackOS action audit rows."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from stackos.db.models import ActionCall, ActionCallStatus, Credential
from tests.integration.account_test_support import seed_test_account


def test_action_call_route_returns_redacted_audit_rows(
    api: TestClient,
    project_id: int,
) -> None:
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        backing = seed_test_account(
            session,
            project_id=project_id,
            provider_key="openai-images",
            display_name="OpenAI Images - Default",
            secret_payload=b"secret",
        )
        credential = session.exec(
            select(Credential).where(
                Credential.integration_credential_id == backing.data.id,
            )
        ).one()
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
            credential_ref=credential.credential_ref,
            request_json={"prompt": "test", "api_key": "secret"},
            response_json={"asset_url": "/generated-assets/test.webp", "token": "secret"},
            metadata_json={
                "credential_ref": credential.credential_ref,
                "refresh_token": "secret",
            },
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
            credential_ref=credential.credential_ref,
            request_json={"prompt": "bad"},
            response_json=None,
            error="provider rejected request",
        )
        session.add(row)
        session.add(failed)
        session.commit()
        credential_ref = credential.credential_ref

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
    assert item["metadata_json"]["credential_ref"] == credential_ref
    assert item["metadata_json"]["refresh_token"] == "[redacted]"
    assert "credential_id" not in item
    assert "idempotency_key" not in item
    assert "caller-secret-key" not in resp.text


def test_action_call_route_hides_generated_inventory_scope_ids(
    api: TestClient,
    project_id: int,
) -> None:
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        row = ActionCall(
            project_id=project_id,
            action_key="api.ctx_e35abd8c2055.reporting_getlinklistmetrics",
            plugin_slug="trackbooth",
            provider_key="trackbooth",
            connector_key="trackbooth",
            operation="operation.execute",
            status=ActionCallStatus.SUCCESS,
            dry_run=False,
            request_json={"body": {"row_ids": ["rec_1"]}},
            response_json={"status": "ok"},
            metadata_json={
                "inventory_scope_key": "ctx_e35abd8c2055",
                "nested": {"inventory_scope_key": "inv_current"},
            },
        )
        session.add(row)
        session.commit()

    resp = api.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={"plugin_slug": "trackbooth"},
    )

    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["action_key"] == "api.reporting_getlinklistmetrics"
    assert "ctx_e35abd8c2055" not in resp.text
    assert "inv_current" not in resp.text
    assert item["metadata_json"]["inventory_scope_key"] == "[generated-inventory-scope]"
    assert item["metadata_json"]["nested"]["inventory_scope_key"] == "[generated-inventory-scope]"


def test_action_call_route_returns_newest_first_with_older_cursor(
    api: TestClient,
    project_id: int,
) -> None:
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        calls = [
            ActionCall(
                project_id=project_id,
                action_key=f"call.{idx}",
                plugin_slug="utils",
                provider_key="mock",
                connector_key="mock",
                operation=f"call.{idx}",
                status=ActionCallStatus.SUCCESS,
                dry_run=False,
                request_json={"idx": idx},
            )
            for idx in range(3)
        ]
        session.add_all(calls)
        session.commit()
        call_ids = [int(call.id or 0) for call in calls]

    first = api.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={"limit": 2},
    )

    assert first.status_code == 200
    first_payload = first.json()
    assert [item["id"] for item in first_payload["items"]] == [call_ids[2], call_ids[1]]
    assert first_payload["next_cursor"] == call_ids[1]

    second = api.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={"limit": 2, "after": first_payload["next_cursor"]},
    )

    assert second.status_code == 200
    second_payload = second.json()
    assert [item["id"] for item in second_payload["items"]] == [call_ids[0]]
    assert second_payload["next_cursor"] is None
