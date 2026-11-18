"""CFOBooth host catalog composition; native package owns all provider protocol.

Wire contract: CFOBooth docs/engineering/stackos-wire-contract-837.md.
Ordinary calls use stored native bindings, never a catalog preflight.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime

from stackos_connectors import get_default_client
from stackos_connectors.contracts import thaw
from stackos_connectors.errors import ValidationError as NativeValidationError

from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.generated_inventory import GeneratedInventoryIdentity, upsert_generated_actions
from stackos.actions.package_bridge import PackageActionConnector
from stackos.generated_inventory import generated_inventory_scope_key
from stackos.integrations.cfobooth import inventory_scope, project_attachment_revision
from stackos.repositories.base import ValidationError

IDENTITY = GeneratedInventoryIdentity(
    plugin_slug="cfobooth",
    provider_key="cfobooth",
    inventory_source="cfobooth.live-catalog",
    scope_fields=(
        "project_id",
        "credential_ref",
        "api_base_url",
        "subject",
        "organization_id",
        "key_id",
    ),
)


class CFOBoothActionConnector:
    key = "cfobooth"

    def __init__(self, *, client=None, options=None):
        self._client = client
        self._options = options

    def _bridge(self, request):
        from stackos_connectors.connectors.cfobooth import declare_action

        client = self._client or get_default_client()
        if request.operation == "catalog.sync":
            prepared = replace(
                request,
                action_key="cfobooth.catalog.export",
                operation="catalog.export",
                input_json={},
            )
        elif request.operation == "operation.execute":
            declaration = declare_action(
                request.action_key, request.config_json["native_descriptor"]
            )
            client = client.register_actions([declaration])
            prepared = request
        else:
            raise ValidationError("Unsupported CFOBooth host operation")
        return PackageActionConnector("cfobooth", client=client, options=self._options), prepared

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        if request.operation == "catalog.sync":
            # The public sync schema validates selectors. Native export takes no input.
            return []
        try:
            bridge, prepared = self._bridge(request)
            # Host admission precedes credential resolution. The native executor
            # validates auth/config and the fixed binding again before HTTP.
            return [
                ActionValidationIssue(**item.model_dump())
                for item in bridge.client.validate_data(
                    "cfobooth", prepared.action_key, prepared.input_json
                )
            ]
        except (KeyError, NativeValidationError, ValidationError):
            return [ActionValidationIssue(path="$", message="Invalid stored CFOBooth binding")]

    def estimate_cost_cents(self, _request):
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        scope = inventory_scope(request)
        assert request.credential is not None and request.session is not None
        attachment_revision = project_attachment_revision(request)
        if attachment_revision is None:
            raise ValidationError("CFOBooth Account is no longer attached to this project")
        if request.operation != "catalog.sync":
            config = request.config_json
            if (
                config.get("inventory_source") != IDENTITY.inventory_source
                or config.get("inventory_state") != "active"
                or config.get("inventory_scope_key") != generated_inventory_scope_key(scope)
                or any(config.get(f"inventory_{field}") != value for field, value in scope.items())
            ):
                raise ValidationError("CFOBooth inventory scope changed; verify and sync again")
        revision = request.credential.credential.updated_at
        bridge, prepared = self._bridge(request)
        # Supply the tenant through the native auth config projection only.
        prepared = replace(
            prepared,
            credential=replace(
                request.credential,
                config_json={
                    **(request.credential.config_json or {}),
                    "organization_id": scope["organization_id"],
                },
            ),
        )
        native = await bridge.execute_native(prepared)
        # Do not return results or publish inventory after Account replacement,
        # revocation, or removal of this project's original Connection.
        request.session.refresh(request.credential.credential)
        try:
            current_scope = inventory_scope(request)
        except ValidationError:
            current_scope = None
        if (
            request.credential.credential.updated_at != revision
            or current_scope != scope
            or project_attachment_revision(request) != attachment_revision
        ):
            raise ActionConnectorError(
                "CFOBooth Account changed during execution; verify and sync again",
                metadata_json={
                    **native.metadata_json,
                    "provider_executed": True,
                    "result_discarded": True,
                    "retry_safe": False,
                },
            )
        if request.operation != "catalog.sync":
            return ActionConnectorResult(
                output_json=native.output_json, metadata_json=native.metadata_json
            )
        return self._sync(request, scope, native)

    def _sync(self, request, scope, native):
        from stackos_connectors.connectors.cfobooth import catalog_actions

        # Native validates the entire export (including excluded entries) before
        # filtering or the first inventory write. Do not project a second schema.
        definitions = catalog_actions(native.output_json)
        selected_ids = request.input_json.get("operation_ids")
        limit = request.input_json.get("limit")
        selected = [
            item
            for item in definitions
            if not selected_ids or item.config["operation_id"] in selected_ids
        ]
        if limit is not None:
            selected = selected[:limit]
        scope_key = generated_inventory_scope_key(scope)
        manifests = []
        for definition in selected:
            operation_id = definition.config["operation_id"]
            # The digest preserves arbitrary native operation IDs without slug collisions.
            public = "api.op" + hashlib.sha256(operation_id.encode()).hexdigest()
            descriptor = thaw(definition.config["descriptor"])
            manifests.append(
                (
                    operation_id,
                    {
                        "key": f"api.{scope_key}.{public[4:]}",
                        "name": definition.metadata["name"],
                        "description": definition.description,
                        "provider": "cfobooth",
                        "capability": "organization-api",
                        "risk_level": "read" if descriptor["effect"] == "read" else "write",
                        "input_schema": thaw(definition.input_schema),
                        "output_schema": thaw(definition.output_schema),
                        "config": {
                            "schema_version": "stackos.action.v1",
                            "connector": "cfobooth",
                            "operation": "operation.execute",
                            "requires_credential": True,
                            "provider_context_schema": {
                                "type": "object",
                                "additionalProperties": False,
                            },
                            "public_action_key": public,
                            "native_descriptor": descriptor,
                        },
                    },
                )
            )
        result = upsert_generated_actions(
            session=request.session,
            identity=IDENTITY,
            scope=scope,
            actions=manifests,
            catalog_hash=native.output_json["data"]["catalogHash"],
            prune_missing=not selected_ids and limit is None,
            now=datetime.now(UTC).replace(tzinfo=None),
        )
        return ActionConnectorResult(
            output_json={
                **result,
                "catalog_hash": native.output_json["data"]["catalogHash"],
                "manual_sync": True,
            },
            metadata_json=native.metadata_json,
        )
