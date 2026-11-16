from __future__ import annotations

import pytest
from stackos_connectors.connectors.trackbooth.assets import _expand_schema_descriptor
from stackos_connectors.connectors.trackbooth.schema import _schema_property
from stackos_connectors.errors import ValidationError as NativeValidationError

from stackos.actions.trackbooth_assets import _detail_from_endpoint
from stackos.repositories.base import ValidationError


def test_canonical_json_schema_is_authoritative_and_independent() -> None:
    canonical = {
        "type": "object",
        "required": ["account_type"],
        "properties": {
            "account_type": {
                "type": "string",
                "const": "network",
                "default": "network",
                "description": "New commercial accounts are independent network accounts",
            }
        },
    }
    descriptor = {
        "json_schema": canonical,
        "details": {
            "fields": [
                {
                    "name": "account_type",
                    "type": "literal",
                    "default_value": '"network"',
                    "required": True,
                }
            ]
        },
    }

    expanded = _expand_schema_descriptor(descriptor, {})

    assert expanded == canonical
    assert expanded is not canonical
    assert expanded is not None
    expanded["properties"]["account_type"]["description"] = "changed"
    assert canonical["properties"]["account_type"]["description"] == (
        "New commercial accounts are independent network accounts"
    )


def test_legacy_fields_are_not_a_schema_source() -> None:
    descriptor = {
        "details": {
            "fields": [
                {
                    "name": "account_type",
                    "type": "literal",
                    "default_value": '"network"',
                    "required": True,
                }
            ]
        }
    }

    if _schema_property(True) is True:
        with pytest.raises(NativeValidationError, match="no supported schema"):
            _expand_schema_descriptor(descriptor, {})
    else:
        # The pinned 0.2.8 API returns no schema; the companion rejects it.
        # Neither may reinterpret legacy field descriptions as a schema.
        assert _expand_schema_descriptor(descriptor, {}) is None


@pytest.mark.parametrize("schema", [True, False])
def test_boolean_schema_is_preserved_or_explicitly_unsupported(schema: bool) -> None:
    endpoint = {"operation_id": "Fixtures.boolean", "body_schema": {"json_schema": schema}}
    if _schema_property(True) is True:
        assert _detail_from_endpoint(endpoint)["request_body"] is schema
    else:
        with pytest.raises(ValidationError, match="does not support boolean"):
            _detail_from_endpoint(endpoint)


def test_openapi_component_remains_the_only_noncanonical_fallback() -> None:
    component = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }
    descriptor = {
        "component_name": "CreateAccountBody",
        "details": {"fields": [{"name": "name", "type": "number"}]},
    }

    expanded = _expand_schema_descriptor(descriptor, {"CreateAccountBody": component})

    assert expanded == component
    assert expanded is not component
    assert expanded is not None
    expanded["properties"]["name"]["type"] = "boolean"
    assert component["properties"]["name"]["type"] == "string"
