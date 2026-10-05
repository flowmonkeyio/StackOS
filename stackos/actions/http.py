"""Register one authorized static HTTP manifest on an isolated native client."""

from __future__ import annotations

import json

from stackos_connectors import (
    ActionDefinition,
    AuthMethodDefinition,
    ConnectorAuth,
    ConnectorError,
    ConnectorRequest,
    get_default_client,
)

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.package_bridge import PackageActionConnector, _translate_error, host_result
from stackos.repositories.base import ValidationError


class HttpActionConnector:
    key = "http"

    def _registration(self, request: ActionConnectorRequest):
        client = get_default_client()
        config = request.config_json.get("http", {})
        auth_type = (
            config.get("auth", {}).get("type", "none") if isinstance(config, dict) else "none"
        )
        methods = client.registry.connector_metadata["http"]["auth_methods"]
        declaration = next((item for item in methods if item["key"] == auth_type), None)
        definition = ActionDefinition(
            connector="http",
            key=request.action_key,
            operation=request.operation,
            config={"http": config},
            auth_methods=()
            if declaration is None
            else (
                AuthMethodDefinition(
                    key=auth_type,
                    fields_schema=declaration["fields_schema"],
                    config_schema=declaration["config_schema"],
                ),
            ),
        )
        return client.register_actions([definition]), definition, auth_type

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        try:
            client, definition, _ = self._registration(request)
            native = ConnectorRequest(
                "http", request.action_key, request.operation, request.input_json, definition.config
            )
            return [
                ActionValidationIssue(**issue.model_dump())
                for issue in client.registry.implementation("http").validate(native)
            ]
        except (ConnectorError, TypeError, ValueError, AttributeError):
            return [ActionValidationIssue(path="$.config.http", message="invalid HTTP manifest")]

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        client, _, auth_type = self._registration(request)
        auth = None
        if auth_type != "none":
            if request.credential is None:
                raise ValidationError("HTTP action requires a resolved credential")
            text = request.credential.secret_payload.decode("utf-8").strip()
            if auth_type == "basic":
                try:
                    fields = json.loads(text)
                except ValueError:
                    username, separator, password = text.partition(":")
                    if not separator:
                        raise ValidationError("basic auth requires username/password") from None
                    fields = {"username": username, "password": password}
                if not isinstance(fields, dict):
                    raise ValidationError("basic auth credential must be an object")
                fields = {
                    "username": fields.get("username") or fields.get("user"),
                    "password": fields.get("password") or fields.get("secret"),
                }
            else:
                fields = {"token": text}
            auth = ConnectorAuth(auth_type, fields)
        try:
            result = await client.execute(
                "http",
                request.action_key,
                request.input_json,
                auth,
                PackageActionConnector("http", client=client)._options(request),
            )
        except ConnectorError as exc:
            raise _translate_error(exc) from None
        return host_result(result)
