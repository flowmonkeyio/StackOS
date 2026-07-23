"""HubSpot signed webhook and custom workflow-action ingress tests."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.db.models import (
    ActionCall,
    Credential,
    CredentialAccount,
    ProviderObjectReference,
    RunPlan,
)
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.resources import ResourceRepository

_APP_ID = "16111050"
_PORTAL_ID = "48807704"
_PROFILE_KEY = "primary"
_CLIENT_SECRET = "hubspot-app-client-secret"
_PUBLIC_BASE_URL = "https://auth.stackos.example"


def _store_hubspot_ingress_profile(
    api: TestClient,
    project_id: int,
    *,
    event_allowlist: list[str] | None = None,
    workflow_action_allowlist: list[str] | None = None,
) -> str:
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        stored = AuthRepository(session).store_credential(
            project_id=project_id,
            provider_key="hubspot",
            auth_method_key="oauth2_authorization_code",
            profile_key=_PROFILE_KEY,
            fields={
                "client_id": "hubspot-app-client-id",
                "client_secret": _CLIENT_SECRET,
                "app_id": int(_APP_ID),
                "webhook_enabled": True,
                "webhook_event_allowlist": event_allowlist or [],
                "workflow_action_allowlist": ",".join(workflow_action_allowlist or []),
            },
        )
        credential = session.exec(
            select(Credential).where(Credential.credential_ref == stored.data.credential_ref)
        ).one()
        credential.status = "connected"
        session.add(credential)
        assert credential.id is not None
        session.add(
            CredentialAccount(
                credential_id=credential.id,
                provider_account_id=_PORTAL_ID,
                metadata_json={"hub_id": int(_PORTAL_ID), "app_id": int(_APP_ID)},
            )
        )
        session.commit()
        ResourceRepository(session).upsert_record(
            project_id=project_id,
            plugin_slug="communications",
            resource_key="ingress-endpoint",
            external_id="ingress-endpoint:default",
            title="Default ingress endpoint",
            data_json={
                "key": "default",
                "endpoint_ref": "ingress-endpoint:default",
                "driver": "public-url",
                "enabled": True,
                "status": "running",
                "public_base_url": _PUBLIC_BASE_URL,
                "local_base_url": "http://127.0.0.1:5180",
                "driver_config": {},
                "metadata_json": {},
            },
            provenance_json={"source": "test"},
        )
        return credential.credential_ref


def _canonical_url(project_id: int) -> str:
    return f"{_PUBLIC_BASE_URL}/api/v1/ingress/hubspot/{project_id}/{_PROFILE_KEY}"


def _v3_headers(
    raw_body: bytes,
    *,
    project_id: int,
    canonical_url: str | None = None,
    timestamp_ms: int | None = None,
) -> dict[str, str]:
    timestamp = str(timestamp_ms if timestamp_ms is not None else int(time.time() * 1_000))
    source = (
        b"POST"
        + (canonical_url or _canonical_url(project_id)).encode("utf-8")
        + raw_body
        + timestamp.encode("utf-8")
    )
    signature = base64.b64encode(
        hmac.new(_CLIENT_SECRET.encode("utf-8"), source, hashlib.sha256).digest()
    ).decode("ascii")
    return {
        "X-HubSpot-Signature-V3": signature,
        "X-HubSpot-Request-Timestamp": timestamp,
        "Content-Type": "application/json",
    }


def _v2_headers(
    raw_body: bytes,
    *,
    project_id: int,
    canonical_url: str | None = None,
) -> dict[str, str]:
    source = (
        _CLIENT_SECRET.encode("utf-8")
        + b"POST"
        + (canonical_url or _canonical_url(project_id)).encode("utf-8")
        + raw_body
    )
    return {
        "X-HubSpot-Signature": hashlib.sha256(source).hexdigest(),
        "X-HubSpot-Signature-Version": "v2",
        "Content-Type": "application/json",
    }


def _post_without_bearer(
    api: TestClient,
    project_id: int,
    *,
    raw_body: bytes,
    headers: dict[str, str],
) -> Any:
    original_auth = api.headers.pop("Authorization", None)
    try:
        return api.post(
            f"/api/v1/ingress/hubspot/{project_id}/{_PROFILE_KEY}",
            content=raw_body,
            headers={"Host": "temporary-tunnel.example", **headers},
        )
    finally:
        if original_auth is not None:
            api.headers["Authorization"] = original_auth


def _subscription_event(
    *,
    subscription_type: str = "contact.propertyChange",
    event_id: str = "event-100",
    subscription_id: str = "subscription-200",
    object_id: str = "contact-300",
    portal_id: str = _PORTAL_ID,
    app_id: str = _APP_ID,
    attempt_number: int = 0,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "subscriptionType": subscription_type,
        "eventId": event_id,
        "subscriptionId": subscription_id,
        "portalId": int(portal_id),
        "appId": int(app_id),
        "objectId": object_id,
        "occurredAt": 1784745600123,
        "attemptNumber": attempt_number,
        "changeSource": "CRM_UI",
    }
    if subscription_type.endswith(".propertyChange"):
        event.update(
            {
                "propertyName": "email",
                "propertyValue": "customer-value-must-not-be-stored@example.test",
            }
        )
    return event


def _workflow_action_payload(*, definition_id: str = "9001") -> dict[str, Any]:
    return {
        "callbackId": "callback-provider-id-700",
        "origin": {
            "portalId": int(_PORTAL_ID),
            "actionDefinitionId": int(definition_id),
        },
        "context": {"source": "WORKFLOWS", "workflowId": 8001},
        "object": {
            "objectId": "contact-provider-id-900",
            "objectType": "CONTACT",
            "properties": {
                "email": "object-property-must-not-be-stored@example.test",
            },
        },
        "inputFields": {
            "request": "Review this lifecycle event",
            "access_token": "incoming-secret-must-be-redacted",
        },
    }


def _stored_state(api: TestClient, project_id: int) -> dict[str, Any]:
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        events = (
            ResourceRepository(session)
            .query_records(
                project_id=project_id,
                plugin_slug="communications",
                resource_key="communication-event",
            )
            .items
        )
        requests = AgentRequestRepository(session).list(project_id=project_id).items
        refs = session.exec(
            select(ProviderObjectReference).where(ProviderObjectReference.project_id == project_id)
        ).all()
        run_plans = session.exec(select(RunPlan).where(RunPlan.project_id == project_id)).all()
        action_calls = session.exec(
            select(ActionCall).where(ActionCall.project_id == project_id)
        ).all()
        return {
            "events": events,
            "requests": requests,
            "refs": refs,
            "run_plans": run_plans,
            "action_calls": action_calls,
        }


def test_v3_batch_uses_safe_refs_exact_allowlist_and_never_starts_execution(
    api: TestClient,
    project_id: int,
) -> None:
    credential_ref = _store_hubspot_ingress_profile(
        api,
        project_id,
        event_allowlist=["contact.propertyChange"],
    )
    payload = [
        _subscription_event(),
        _subscription_event(
            subscription_type="company.creation",
            event_id="event-101",
            subscription_id="subscription-201",
            object_id="company-301",
        ),
    ]
    raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    response = _post_without_bearer(
        api,
        project_id,
        raw_body=raw_body,
        headers=_v3_headers(raw_body, project_id=project_id),
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "ok": True,
        "profile_key": _PROFILE_KEY,
        "received": 2,
        "request_created": 1,
        "request_deduped": 0,
        "not_allowlisted": 1,
    }
    state = _stored_state(api, project_id)
    assert len(state["events"]) == 2
    assert len(state["requests"]) == 1
    assert state["requests"][0].source_provider == "hubspot"
    assert state["requests"][0].run_plan_id is None
    assert not state["run_plans"]
    assert not state["action_calls"]
    assert state["refs"]

    public_records = json.dumps(
        {
            "events": [item.data_json for item in state["events"]],
            "requests": [item.metadata_json for item in state["requests"]],
        },
        sort_keys=True,
    )
    for provider_identifier in (
        _APP_ID,
        _PORTAL_ID,
        "event-100",
        "subscription-200",
        "contact-300",
        "event-101",
        "subscription-201",
        "company-301",
        "customer-value-must-not-be-stored@example.test",
        credential_ref,
    ):
        assert provider_identifier not in public_records
    assert "provider-object:" in public_records
    assert any(item.data_json["policy_status"] == "request_created" for item in state["events"])
    assert any(
        item.data_json["policy_status"] == "event_not_allowlisted" for item in state["events"]
    )


def test_v3_replay_dedupes_the_request_and_preserves_the_first_event(
    api: TestClient,
    project_id: int,
) -> None:
    _store_hubspot_ingress_profile(
        api,
        project_id,
        event_allowlist=["contact.propertyChange"],
    )
    first = [_subscription_event(attempt_number=0)]
    replay = [_subscription_event(attempt_number=1)]

    for payload, expected_status in ((first, "request_created"), (replay, "request_deduped")):
        raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        response = _post_without_bearer(
            api,
            project_id,
            raw_body=raw_body,
            headers=_v3_headers(raw_body, project_id=project_id),
        )
        assert response.status_code == 200, response.text
        assert response.json()[expected_status] == 1

    state = _stored_state(api, project_id)
    assert len(state["events"]) == 1
    assert len(state["requests"]) == 1
    assert state["events"][0].data_json["attempt_number"] == 0


def test_v3_rejects_bad_or_expired_signatures_before_any_write(
    api: TestClient,
    project_id: int,
) -> None:
    _store_hubspot_ingress_profile(
        api,
        project_id,
        event_allowlist=["contact.creation"],
    )
    raw_body = json.dumps([_subscription_event(subscription_type="contact.creation")]).encode()
    bad_headers = _v3_headers(raw_body, project_id=project_id)
    bad_headers["X-HubSpot-Signature-V3"] = "invalid-signature"

    bad = _post_without_bearer(
        api,
        project_id,
        raw_body=raw_body,
        headers=bad_headers,
    )
    expired_at = int(time.time() * 1_000) - (6 * 60 * 1_000)
    expired = _post_without_bearer(
        api,
        project_id,
        raw_body=raw_body,
        headers=_v3_headers(
            raw_body,
            project_id=project_id,
            timestamp_ms=expired_at,
        ),
    )

    assert bad.status_code == 403
    assert expired.status_code == 403
    state = _stored_state(api, project_id)
    assert not state["events"]
    assert not state["requests"]
    assert not state["refs"]


def test_v3_signs_the_configured_https_url_not_the_untrusted_host_header(
    api: TestClient,
    project_id: int,
) -> None:
    _store_hubspot_ingress_profile(
        api,
        project_id,
        event_allowlist=["contact.creation"],
    )
    raw_body = json.dumps([_subscription_event(subscription_type="contact.creation")]).encode()

    wrong = _post_without_bearer(
        api,
        project_id,
        raw_body=raw_body,
        headers=_v3_headers(
            raw_body,
            project_id=project_id,
            canonical_url=(
                f"https://temporary-tunnel.example/api/v1/ingress/hubspot/"
                f"{project_id}/{_PROFILE_KEY}"
            ),
        ),
    )
    correct = _post_without_bearer(
        api,
        project_id,
        raw_body=raw_body,
        headers=_v3_headers(raw_body, project_id=project_id),
    )

    assert wrong.status_code == 403
    assert correct.status_code == 200, correct.text
    assert len(_stored_state(api, project_id)["requests"]) == 1


def test_v3_validates_the_whole_batch_and_connection_binding_before_writes(
    api: TestClient,
    project_id: int,
) -> None:
    _store_hubspot_ingress_profile(
        api,
        project_id,
        event_allowlist=["contact.creation"],
    )
    payload = [
        _subscription_event(subscription_type="contact.creation"),
        _subscription_event(
            subscription_type="contact.creation",
            event_id="event-wrong-portal",
            portal_id="99999999",
        ),
    ]
    raw_body = json.dumps(payload).encode()

    response = _post_without_bearer(
        api,
        project_id,
        raw_body=raw_body,
        headers=_v3_headers(raw_body, project_id=project_id),
    )

    assert response.status_code == 403
    state = _stored_state(api, project_id)
    assert not state["events"]
    assert not state["requests"]
    assert not state["refs"]


def test_v3_rejects_malformed_oversized_and_overlarge_batches_without_writes(
    api: TestClient,
    project_id: int,
) -> None:
    _store_hubspot_ingress_profile(
        api,
        project_id,
        event_allowlist=["contact.creation"],
    )
    malformed = b'{"not":"finished"'
    malformed_response = _post_without_bearer(
        api,
        project_id,
        raw_body=malformed,
        headers=_v3_headers(malformed, project_id=project_id),
    )

    large_payload = [
        _subscription_event(
            subscription_type="contact.creation",
            event_id=f"event-{index}",
            object_id=f"contact-{index}",
        )
        for index in range(101)
    ]
    large_body = json.dumps(large_payload).encode()
    large_response = _post_without_bearer(
        api,
        project_id,
        raw_body=large_body,
        headers=_v3_headers(large_body, project_id=project_id),
    )

    oversized = b" " * 1_000_001
    oversized_response = _post_without_bearer(
        api,
        project_id,
        raw_body=oversized,
        headers={"Content-Type": "application/json"},
    )

    assert malformed_response.status_code == 400
    assert large_response.status_code == 400
    assert oversized_response.status_code == 413
    state = _stored_state(api, project_id)
    assert not state["events"]
    assert not state["requests"]
    assert not state["refs"]


def test_v3_workflow_action_is_allowlisted_deduped_redacted_and_nonexecuting(
    api: TestClient,
    project_id: int,
) -> None:
    _store_hubspot_ingress_profile(
        api,
        project_id,
        workflow_action_allowlist=["9001"],
    )
    payload = _workflow_action_payload()
    raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    responses = [
        _post_without_bearer(
            api,
            project_id,
            raw_body=raw_body,
            headers=_v3_headers(raw_body, project_id=project_id),
        )
        for _ in range(2)
    ]

    assert all(response.status_code == 200 for response in responses)
    assert all(
        response.json() == {"outputFields": {"hs_execution_state": "SUCCESS"}}
        for response in responses
    )
    state = _stored_state(api, project_id)
    assert len(state["events"]) == 1
    assert len(state["requests"]) == 1
    assert not state["run_plans"]
    assert not state["action_calls"]
    public_records = json.dumps(
        {
            "event": state["events"][0].data_json,
            "request": state["requests"][0].metadata_json,
        },
        sort_keys=True,
    )
    for provider_identifier in (
        _PORTAL_ID,
        "9001",
        "8001",
        "callback-provider-id-700",
        "contact-provider-id-900",
        "object-property-must-not-be-stored@example.test",
        "incoming-secret-must-be-redacted",
    ):
        assert provider_identifier not in public_records
    assert "provider-object:" in public_records
    assert "[redacted]" in public_records


def test_v3_nonallowlisted_definition_continues_without_creating_agent_work(
    api: TestClient,
    project_id: int,
) -> None:
    _store_hubspot_ingress_profile(
        api,
        project_id,
        workflow_action_allowlist=["9001"],
    )
    raw_body = json.dumps(_workflow_action_payload(definition_id="9002")).encode()

    response = _post_without_bearer(
        api,
        project_id,
        raw_body=raw_body,
        headers=_v3_headers(raw_body, project_id=project_id),
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"outputFields": {"hs_execution_state": "FAIL_CONTINUE"}}
    state = _stored_state(api, project_id)
    assert len(state["events"]) == 1
    assert state["events"][0].data_json["policy_status"] == "event_not_allowlisted"
    assert not state["requests"]
    assert not state["run_plans"]
    assert not state["action_calls"]


def test_legacy_v2_workflow_action_signature_is_rejected(
    api: TestClient,
    project_id: int,
) -> None:
    _store_hubspot_ingress_profile(
        api,
        project_id,
        workflow_action_allowlist=["9001"],
    )
    workflow_body = json.dumps(_workflow_action_payload()).encode()

    response = _post_without_bearer(
        api,
        project_id,
        raw_body=workflow_body,
        headers=_v2_headers(workflow_body, project_id=project_id),
    )

    assert response.status_code == 403
    state = _stored_state(api, project_id)
    assert not state["events"]
    assert not state["requests"]
    assert not state["refs"]


def test_generic_ingress_routes_expose_one_manual_hubspot_setup_url(
    api: TestClient,
    project_id: int,
) -> None:
    credential_ref = _store_hubspot_ingress_profile(api, project_id)

    response = api.post(
        "/api/v1/operations/ingressEndpoint.routes/call",
        json={
            "arguments": {
                "project_id": project_id,
                "key": "default",
                "response_mode": "raw",
            }
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    routes = body.get("data", body)["routes"]
    assert routes == [
        {
            "provider_key": "hubspot",
            "profile_key": _PROFILE_KEY,
            "profile_ref": credential_ref,
            "profile_resource_key": "credential",
            "ingress_path": (f"/api/v1/ingress/hubspot/{project_id}/{_PROFILE_KEY}"),
            "ingress_url": _canonical_url(project_id),
            "local_url": (
                f"http://127.0.0.1:5180/api/v1/ingress/hubspot/{project_id}/{_PROFILE_KEY}"
            ),
            "remote_status": "manual_provider_update_required",
            "notes": [
                "HubSpot webhook Target URL and any custom workflow action URL must be set "
                "in the HubSpot app configuration."
            ],
            "action_required": True,
            "next_action": {
                "kind": "manual-provider-update",
                "label": "Copy webhook URL",
                "title": "Update HubSpot ingress for primary",
                "instructions": (
                    "Copy this URL into the HubSpot app Webhooks Target URL. Use the same "
                    "URL as actionUrl only for explicitly allowlisted custom workflow actions."
                ),
                "url": _canonical_url(project_id),
                "provider_fields": [
                    "Webhooks Target URL",
                    "Custom workflow action actionUrl",
                ],
            },
        }
    ]
