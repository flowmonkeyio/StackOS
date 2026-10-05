"""Translate the host action boundary into the standalone connector contract.

This adapter owns no credentials, references, grants or durable state. The
action repository authorizes and resolves them before execution. Providers with
host reference or artifact semantics prepare/project around this boundary.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING

from stackos_connectors import (
    ActionDefinition,
    CallOptions,
    ConnectorAuth,
    ConnectorClient,
    ConnectorError,
    ConnectorRequest,
    ConnectorResult,
    ValidationError,
    get_default_client,
)
from stackos_connectors.contracts import thaw
from stackos_connectors.errors import IntegrationDownError as NativeIntegrationDownError
from stackos_connectors.errors import RateLimitedError as NativeRateLimitedError

from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.artifacts import redact_secret_text, redact_secrets
from stackos.mcp.errors import IntegrationDownError, RateLimitedError

if TYPE_CHECKING:
    from stackos.auth_providers import ResolvedCredential


def _failure(detail: str) -> ActionConnectorError:
    return ActionConnectorError(detail, metadata_json={"provider_executed": False})


def resolved_auth(
    credential: ResolvedCredential | None,
    *,
    payload_format: str = "json",
    payload_field: str | None = None,
    declaration: Mapping | None = None,
) -> ConnectorAuth | None:
    """Copy the already resolved saved method and payload; never acquire tokens."""
    if credential is None:
        return None
    method = credential.credential.auth_method_key
    config = dict(credential.config_json or {})
    if not method or config.get("auth_method_key", method) != method:
        raise _failure("resolved credential has no consistent saved auth method")
    try:
        text = credential.secret_payload.decode("utf-8")
        if payload_format == "none":
            fields = {}
        elif payload_format == "raw":
            if not payload_field or not text:
                raise ValueError("raw payload requires a declared field and value")
            fields = {payload_field: text}
        elif payload_format == "json":
            fields = json.loads(text)
            if not isinstance(fields, dict):
                raise ValueError("JSON payload must be an object")
        else:
            raise ValueError("unknown payload format")
        if declaration is not None:
            fields_schema = declaration.get("fields_schema", {})
            config_schema = declaration.get("config_schema", {})
            fields = {
                key: value
                for key, value in fields.items()
                if key in fields_schema.get("properties", {})
                or key in fields_schema.get("required", ())
            }
            config = {
                key: value
                for key, value in config.items()
                if key in config_schema.get("properties", {})
                or key in config_schema.get("required", ())
            }
        return ConnectorAuth(method=method, fields=fields, config=config)
    except (UnicodeError, TypeError, ValueError):
        raise _failure("resolved credential payload is invalid") from None


def _translate_error(error: ConnectorError) -> ActionConnectorError:
    return ActionConnectorError(
        error.detail,
        provider_status_code=error.provider_status_code,
        provider_error=error.provider_error,
        output_json=error.output_json,
        metadata_json=error.metadata_json,
    )


def host_probe_error(error: NativeIntegrationDownError) -> IntegrationDownError | RateLimitedError:
    """Translate known native probe failures into the existing host diagnostics."""
    kind: type[IntegrationDownError] | type[RateLimitedError]
    if isinstance(error, NativeRateLimitedError):
        kind = RateLimitedError
    elif isinstance(error, NativeIntegrationDownError):
        kind = IntegrationDownError
    else:
        raise TypeError("unsupported native probe error")
    return kind(redact_secret_text(error.detail), data=redact_secrets(error.data))


def host_result(result: ConnectorResult) -> ActionConnectorResult:
    """Redact the public envelope; private file custody/projection is caller-owned."""
    if result.files:
        # File-producing host wrappers must register/project files explicitly.
        raise ActionConnectorError(
            "connector files require host projection",
            output_json=redact_secrets(result.output_json),
            metadata_json={
                **redact_secrets(result.metadata_json or {}),
                "provider_executed": True,
                "retry_safe": False,
            },
        )
    return ActionConnectorResult(
        output_json=redact_secrets(result.output_json),
        metadata_json=redact_secrets(result.metadata_json),
        cost_cents=result.cost_cents,
    )


class PackageActionConnector:
    """Host adapter for actions whose data is already provider-native.

    ``options`` supplies narrow caller-owned transport capabilities. Request
    context, output directory and correlation remain per-call values; host
    identity/session objects never enter the package request.
    """

    def __init__(
        self,
        key: str,
        *,
        client: ConnectorClient | None = None,
        options: CallOptions | None = None,
    ) -> None:
        self.key = key
        self.client = client if client is not None else get_default_client()
        self.options = options or CallOptions()

    def _definition(self, request: ActionConnectorRequest) -> ActionDefinition:
        definition = self.client.registry.action(self.key, request.action_key)
        if request.operation != definition.operation:
            raise ValidationError("host operation does not match the declared connector action")
        if request.config_json.get("connector", self.key) != self.key:
            raise ValidationError("host connector does not match the declared connector action")
        return definition

    def _options(self, request: ActionConnectorRequest) -> CallOptions:
        from stackos.integrations import integration_class_for
        from stackos.integrations._rate_limit import get_bucket

        limiter = self.options.rate_limiter
        integration = integration_class_for(self.key)
        if limiter is None and integration is not None:
            limiter = get_bucket(
                project_id=request.project_id, kind=self.key, qps=integration.default_qps
            )
        return replace(
            self.options,
            provider_context=request.provider_context_json,
            idempotency_key=request.idempotency_key,
            correlation_id=request.correlation_ref,
            output_dir=request.asset_dir
            if request.asset_dir is not None
            else self.options.output_dir,
            progress_callback=request.progress_callback or self.options.progress_callback,
            rate_limiter=limiter,
        )

    def _auth(self, request: ActionConnectorRequest) -> ConnectorAuth | None:
        if request.credential is None:
            return None
        method_key = request.credential.credential.auth_method_key
        methods = self.client.registry.connector_metadata.get(self.key, {}).get("auth_methods", ())
        declaration: Mapping = next((item for item in methods if item.get("key") == method_key), {})
        definition = next(
            (item for item in self._definition(request).auth_methods if item.key == method_key),
            None,
        )
        if definition is None:
            raise _failure("saved auth method is not declared by the connector")
        declaration = {
            "fields_schema": definition.fields_schema,
            "config_schema": definition.config_schema,
            "payload_format": "json",
            **declaration,
        }
        return resolved_auth(
            request.credential,
            payload_format=declaration.get("payload_format", "json"),
            payload_field=declaration.get("payload_field"),
            declaration=declaration,
        )

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        from stackos.actions.package_inputs import needs_preparation, prepare_request
        from stackos.repositories.base import ValidationError as HostValidationError

        try:
            definition = self._definition(request)
            # Public manifest schemas validate safe refs before credential resolution.
            # Native schemas validate the resolved provider IDs at execute time.
            issues = (
                []
                if needs_preparation(self.key)
                else self.client.validate_data(self.key, request.action_key, request.input_json)
            )
            try:
                prepared = prepare_request(self.key, request)
            except HostValidationError:
                # Saved aliases/defaults may not be available before auth resolution.
                # The public manifest still validates the caller's reference fields.
                prepared = None
            if prepared is not None:
                native = ConnectorRequest(
                    self.key,
                    request.action_key,
                    definition.operation,
                    thaw(prepared.input_json),
                    definition.config,
                )
                provider_issues = self.client.registry.implementation(self.key).validate(native)
                if provider_issues:
                    issues = provider_issues
        except ValidationError as exc:
            return [ActionValidationIssue(path="$", message=exc.detail)]
        return [ActionValidationIssue(**item.model_dump()) for item in issues]

    def estimate_cost_cents(self, request: ActionConnectorRequest) -> int:
        # The host estimates before auth resolution. Estimators already operate
        # on data/config only; do not call provider validate or execute here.
        try:
            definition = self._definition(request)
            neutral = ConnectorRequest(
                connector=self.key,
                action_key=request.action_key,
                operation=definition.operation,
                input_json=thaw(request.input_json),
                config_json=definition.config,
                auth=None,
                options=self._options(request),
            )
            implementation = self.client.registry.implementation(self.key)
            return implementation.estimate_cost_cents(neutral)
        except Exception:
            # Payload can still contain caller data; do not echo preflight exceptions.
            raise _failure("connector cost estimation failed") from None

    async def execute_native(self, request: ActionConnectorRequest) -> ConnectorResult:
        """Execute after host preparation; wrappers may project files/native IDs."""
        from stackos.actions.package_inputs import prepare_request

        try:
            request = prepare_request(self.key, request)
            self._definition(request)
            return await self.client.execute(
                self.key,
                request.action_key,
                request.input_json,
                self._auth(request),
                self._options(request),
            )
        except ConnectorError as exc:
            raise _translate_error(exc) from None

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        return host_result(await self.execute_native(request))
