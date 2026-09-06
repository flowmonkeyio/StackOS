"""FIN-GAP001: REST action validation preserves keys without performing a dry run."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.actions.connectors import ActionConnectorRequest
from stackos.actions.stripe import StripeActionConnector
from stackos.auth_providers import AuthRepository
from stackos.db.models import ActionCall, IdempotencyKey
from stackos.repositories.projects import IntegrationCredentialRepository
from stackos.repositories.secrets import PayloadSecretRepository
from stackos.secret_refs import SECRET_REF_SENTINEL


@pytest.mark.parametrize(
    ("idempotency_key", "expected_issue"),
    [
        pytest.param("validate-rest-stripe-customer-v1", None, id="valid"),
        pytest.param(None, "required", id="missing"),
        pytest.param(" ", "required", id="blank"),
        pytest.param("v" * 256, "max_length", id="overlong"),
    ],
)
def test_rest_action_validate_forwards_idempotency_without_execution(
    api: TestClient,
    project_id: int,
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
    idempotency_key: str | None,
    expected_issue: str | None,
) -> None:
    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        credential_ref = (
            AuthRepository(session)
            .store_credential(
                provider_key="stripe",
                auth_method_key="api_key",
                display_name="Validator REST fixture",
                fields={"api_key": "sk_test_validator_rest_sentinel"},
                attach_project_id=project_id,
            )
            .data.credential_ref
        )
        # Fixture setup only: secret.set intentionally has no REST ingress.
        secret_ref = (
            PayloadSecretRepository(session)
            .set(project_id=project_id, value="validator-rest@example.test")
            .secret_ref
        )
    context_ref = "ctx_validator_rest_stripe"
    created = api.post(
        "/api/v1/operations/executionContext.create/call",
        json={
            "arguments": {
                "project_id": project_id,
                "context_ref": context_ref,
                "name": "Read-only Stripe validator fixture",
                "action_ref": "finance.stripe.customers.create",
                "credential_ref": credential_ref,
            }
        },
    )
    assert created.status_code == 200, created.text
    seen_keys: list[str | None] = []
    original_validate = StripeActionConnector.validate

    def observe_validation(self: StripeActionConnector, request: ActionConnectorRequest):
        seen_keys.append(request.idempotency_key)
        assert request.credential is None
        assert request.dry_run is True
        assert request.input_json == {"email": SECRET_REF_SENTINEL}
        return original_validate(self, request)

    def forbid_secret_resolution(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("read-only validation must not decrypt provider or payload secrets")

    async def forbid_execution(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("read-only validation must not execute the Stripe connector")

    monkeypatch.setattr(StripeActionConnector, "validate", observe_validation)
    monkeypatch.setattr(StripeActionConnector, "execute", forbid_execution)
    monkeypatch.setattr(IntegrationCredentialRepository, "get_decrypted", forbid_secret_resolution)
    monkeypatch.setattr(PayloadSecretRepository, "resolve", forbid_secret_resolution)
    with Session(engine) as session:
        before_calls = len(session.exec(select(ActionCall)).all())
        before_idempotency = len(session.exec(select(IdempotencyKey)).all())
    arguments: dict[str, Any] = {
        "project_id": project_id,
        "action_ref": "finance.stripe.customers.create",
        "context_ref": context_ref,
        "input_json": {"email": {"$secret_ref": secret_ref}},
        "response_mode": "raw",
    }
    if idempotency_key is not None:
        arguments["idempotency_key"] = idempotency_key
    for _ in range(2):
        response = api.post(
            "/api/v1/operations/action.validate/call", json={"arguments": arguments}
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["valid"] is (expected_issue is None), result
        assert result["credential_ref"] == credential_ref
        assert result["issues"] == (
            []
            if expected_issue is None
            else [
                {
                    "path": "$.idempotency_key",
                    "message": (
                        "Stripe idempotency key must be at most 255 characters"
                        if expected_issue == "max_length"
                        else "Stripe POST actions require a deterministic idempotency key"
                    ),
                    "code": expected_issue,
                }
            ]
        )
        assert "sk_test_validator_rest_sentinel" not in json.dumps(result)
        assert "validator-rest@example.test" not in json.dumps(result)
    assert seen_keys == [idempotency_key, idempotency_key]
    assert httpx_mock.get_requests() == []
    with Session(engine) as session:
        assert len(session.exec(select(ActionCall)).all()) == before_calls
        assert len(session.exec(select(IdempotencyKey)).all()) == before_idempotency
