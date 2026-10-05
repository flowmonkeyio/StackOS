"""Fixture-registry proof of the host/package boundary, without production activation."""

from __future__ import annotations

import json
from dataclasses import fields, replace
from types import SimpleNamespace

import pytest
from stackos_connectors import (
    ActionDefinition,
    AuthMethodDefinition,
    ConnectorClient,
    ConnectorError,
    ConnectorFile,
    ConnectorRegistry,
    ConnectorResult,
    ValidationError,
)

from stackos.actions.connectors import ActionConnectorError, ActionConnectorRequest
from stackos.actions.package_bridge import PackageActionConnector, host_result, resolved_auth


@pytest.mark.parametrize("with_file", [False, True])
def test_host_result_redacts_native_signed_data_and_projection_failure(with_file):
    native = ConnectorResult(
        output_json={
            "url": "https://provider.example/file?signature=private-signature",
            "cursor": "next?token=private-page-token",
            "token": "private-token",
        },
        metadata_json={"token": "private-token"},
        files=[ConnectorFile(path="/tmp/private-file")] if with_file else [],
    )
    if with_file:
        with pytest.raises(ActionConnectorError) as failure:
            host_result(native)
        result = failure.value
        assert result.metadata_json["provider_executed"] is True
        assert result.metadata_json["retry_safe"] is False
    else:
        result = host_result(native)
    assert result.output_json == {
        "url": "https://provider.example/file?signature=[redacted]",
        "cursor": "next?token=[redacted]",
        "token": "[redacted]",
    }
    assert result.metadata_json["token"] == "[redacted]"


@pytest.mark.parametrize("rate_limited", [False, True])
def test_native_probe_error_preserves_category_retry_and_safe_data(rate_limited):
    from stackos_connectors.errors import IntegrationDownError as NativeDown
    from stackos_connectors.errors import RateLimitedError as NativeLimited

    from stackos.actions.package_bridge import host_probe_error
    from stackos.auth_providers.repository.testing import _failed_test_result
    from stackos.mcp.errors import IntegrationDownError, RateLimitedError

    error = (NativeLimited if rate_limited else NativeDown)(
        "Bearer secret-canary failed",
        data={
            "status": 429 if rate_limited else 503,
            "retry_after": 12,
            "stage": "auth.test",
            "provider_error": {"api_key": "secret-canary"},
        },
    )
    translated = host_probe_error(error)
    assert type(translated) is (RateLimitedError if rate_limited else IntegrationDownError)
    assert translated.retryable is True
    assert translated.data["retry_after"] == 12
    assert "secret-canary" not in json.dumps(translated.to_dict())
    normalized = _failed_test_result("pipedrive", translated)
    assert normalized["retryable"] is True
    assert normalized["metadata"]["reason_code"] == (
        "rate_limited" if rate_limited else "provider_unavailable"
    )
    with pytest.raises(TypeError, match="unsupported native probe error"):
        host_probe_error(ValueError("unknown"))


class FixtureConnector:
    key = "fixture"

    def __init__(self):
        self.calls = []
        self.validations = 0
        self.cost_calls = []
        self.error = None

    def validate(self, request):
        self.validations += 1
        return []

    def estimate_cost_cents(self, request):
        self.cost_calls.append(request)
        return 7

    async def execute(self, request):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return ConnectorResult(
            output_json={"id": "native-123"},
            metadata_json={"request_id": "provider-receipt"},
            cost_cents=7,
        )


@pytest.fixture
def fixture_bridge():
    implementation = FixtureConnector()
    method = AuthMethodDefinition(
        "oauth2_authorization_code",
        fields_schema={
            "type": "object",
            "required": ["access_token"],
            "properties": {"access_token": {"type": "string"}},
        },
    )
    action = ActionDefinition(
        connector="fixture",
        key="fixture.lookup",
        operation="lookup",
        input_schema={
            "type": "object",
            "required": ["object_id"],
            "properties": {"object_id": {"type": "string"}},
            "additionalProperties": False,
        },
        auth_methods=(method,),
        config={"path": "/fixed"},
    )
    client = ConnectorClient(
        registry=ConnectorRegistry(
            actions=[action],
            implementations={"fixture": lambda: implementation},
        )
    )
    return PackageActionConnector("fixture", client=client), implementation


def request(**overrides):
    values = {
        "project_id": 123,
        "plugin_slug": "host",
        "action_key": "fixture.lookup",
        "action_ref": "host::fixture.lookup",
        "provider_key": "fixture",
        "operation": "lookup",
        "input_json": {"object_id": "native-123"},
        "config_json": {"connector": "fixture", "requires_credential": True, "path": "/evil"},
        "credential_ref": "cred_saved",
        "session": object(),
    }
    values.update(overrides)
    return ActionConnectorRequest(**values)


def credential(method="oauth2_authorization_code", payload=None):
    return SimpleNamespace(
        credential=SimpleNamespace(auth_method_key=method),
        secret_payload=payload or b'{"access_token":"test-secret-token"}',
        config_json={"auth_method_key": method, "instance_url": "https://example.test"},
    )


def test_host_validate_and_cost_do_not_need_decrypted_auth(fixture_bridge):
    bridge, implementation = fixture_bridge
    value = request()
    assert bridge.validate(value) == []
    assert bridge.estimate_cost_cents(value) == 7
    assert implementation.validations == 1
    assert implementation.calls == []
    assert implementation.cost_calls[0].auth is None


def test_host_data_validation_is_independent_of_credential_ref_without_dispatch(fixture_bridge):
    bridge, implementation = fixture_bridge
    assert bridge.validate(request(credential_ref=None)) == []
    assert bridge.validate(request(input_json={"object_ref": "host-saved-ref"}))
    assert implementation.calls == []


@pytest.mark.asyncio
async def test_execute_passes_plain_auth_native_data_and_reserved_route(fixture_bridge):
    bridge, implementation = fixture_bridge
    result = await bridge.execute(
        request(
            credential=credential(),
            idempotency_key="stable",
            correlation_ref="opaque-correlation",
            provider_context_json={"tenant": "selected"},
        )
    )
    dispatched = implementation.calls[0]
    assert {field.name for field in fields(dispatched)} == {
        "connector",
        "action_key",
        "operation",
        "input_json",
        "config_json",
        "auth",
        "options",
    }
    assert dispatched.config_json["path"] == "/fixed"
    assert dispatched.auth.method == "oauth2_authorization_code"
    assert dispatched.auth.fields["access_token"] == "test-secret-token"
    assert dispatched.options.idempotency_key == "stable"
    assert dispatched.options.correlation_id == "opaque-correlation"
    assert dispatched.options.provider_context["tenant"] == "selected"
    assert "test-secret-token" not in repr(dispatched.auth)
    assert result.output_json == {"id": "native-123"}
    assert result.metadata_json == {"request_id": "provider-receipt"}
    assert result.cost_cents == 7


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resolved", [None, credential("api_key"), credential(payload=b'{"client_secret":"setup-only"}')]
)
async def test_invalid_execution_auth_never_dispatches(fixture_bridge, resolved):
    bridge, implementation = fixture_bridge
    with pytest.raises(ActionConnectorError) as error:
        await bridge.execute(request(credential=resolved))
    assert implementation.calls == []
    assert error.value.metadata_json["provider_executed"] is False


def test_auth_method_comes_from_saved_identity_and_never_token_shape():
    value = credential("api_key")
    value.config_json["auth_method_key"] = "oauth2_authorization_code"
    with pytest.raises(ActionConnectorError):
        resolved_auth(value)
    assert resolved_auth(
        credential("api_key", b"raw-api-key"),
        payload_format="raw",
        payload_field="api_key",
    ).fields == {"api_key": "raw-api-key"}


@pytest.mark.asyncio
async def test_partial_unknown_provider_error_fields_survive_translation(fixture_bridge):
    bridge, implementation = fixture_bridge
    implementation.error = ConnectorError(
        "partial provider failure",
        provider_status_code=503,
        provider_error={"message": "test-secret-token", "reason": "unavailable"},
        output_json={"receipt": "provider-123", "partial": True},
        metadata_json={"outcome": "unknown", "retry_safe": False, "provider_executed": True},
    )
    with pytest.raises(ActionConnectorError) as error:
        await bridge.execute(request(credential=credential()))
    assert len(implementation.calls) == 1
    assert error.value.provider_status_code == 503
    assert error.value.output_json == {"receipt": "provider-123", "partial": True}
    assert error.value.metadata_json["retry_safe"] is False
    assert error.value.metadata_json["outcome"] == "unknown"
    assert "test-secret-token" not in json.dumps(error.value.provider_error)


def test_cost_failure_is_pre_dispatch_and_does_not_echo_payload(fixture_bridge):
    bridge, implementation = fixture_bridge

    def fail(value):
        raise ValueError(value.input_json["object_id"])

    implementation.estimate_cost_cents = fail
    with pytest.raises(ActionConnectorError) as error:
        bridge.estimate_cost_cents(request(input_json={"object_id": "payload-secret"}))
    assert error.value.metadata_json["provider_executed"] is False
    assert "payload-secret" not in str(error.value)
    assert implementation.calls == []


@pytest.mark.asyncio
async def test_operation_mismatch_stops_before_dispatch(fixture_bridge):
    bridge, implementation = fixture_bridge
    value = replace(request(credential=credential()), operation="other-operation")
    with pytest.raises(ActionConnectorError):
        await bridge.execute(value)
    assert implementation.calls == []


def test_files_require_explicit_host_projection_without_claiming_retry_safe():
    native = ConnectorResult(
        output_json={"receipt": "completed-provider-job"},
        files=[ConnectorFile(path="/tmp/caller-selected-output.png")],
    )
    with pytest.raises(ActionConnectorError) as error:
        host_result(native)
    assert error.value.output_json == {"receipt": "completed-provider-job"}
    assert error.value.metadata_json["provider_executed"] is True
    assert error.value.metadata_json["retry_safe"] is False


def test_invalid_secret_encoding_does_not_echo_payload():
    with pytest.raises(ActionConnectorError) as error:
        resolved_auth(credential(payload=b"secret-value-\xff"))
    assert "secret-value" not in str(error.value)
    assert error.value.metadata_json["provider_executed"] is False


@pytest.mark.parametrize("raw", [b"raw-api-key", b'{"looks":"like-json"}'])
def test_declared_raw_payload_field_controls_auth_deserialization(raw):
    auth = resolved_auth(credential("api_key", raw), payload_format="raw", payload_field="api_key")
    assert auth.fields == {"api_key": raw.decode("utf-8")}


def test_json_auth_payload_does_not_fall_back_to_raw_value():
    with pytest.raises(ActionConnectorError) as error:
        resolved_auth(credential(payload=b"not-json"), payload_format="json")
    assert error.value.metadata_json["provider_executed"] is False


@pytest.mark.asyncio
async def test_bridge_uses_saved_method_catalog_payload_declaration():
    implementation = FixtureConnector()
    method = AuthMethodDefinition(
        "api_key",
        fields_schema={"type": "object", "required": ["api_key"]},
    )
    definition = ActionDefinition(
        connector="fixture",
        key="fixture.lookup",
        operation="lookup",
        auth_methods=(method,),
    )
    client = ConnectorClient(
        registry=ConnectorRegistry(
            actions=[definition],
            implementations={"fixture": lambda: implementation},
            connector_metadata={
                "fixture": {
                    "auth_methods": [
                        {"key": "api_key", "payload_format": "raw", "payload_field": "api_key"},
                    ]
                }
            },
        )
    )
    bridge = PackageActionConnector("fixture", client=client)
    await bridge.execute(request(credential=credential("api_key", b"raw-api-key")))
    assert implementation.calls[0].auth.fields == {"api_key": "raw-api-key"}


@pytest.mark.asyncio
async def test_host_translation_preserves_structured_validation_diagnostics(fixture_bridge):
    bridge, implementation = fixture_bridge
    implementation.error = ValidationError(
        "provider limit exceeded",
        data={"requested_limit": 200, "effective_row_limit": 100, "echo": "test-secret-token"},
    )
    with pytest.raises(ActionConnectorError) as error:
        await bridge.execute(request(credential=credential()))
    assert error.value.metadata_json["data"]["effective_row_limit"] == 100
    assert error.value.metadata_json["provider_executed"] is False
    assert "test-secret-token" not in repr(error.value.metadata_json)
