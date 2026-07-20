from __future__ import annotations

from stackos.actions.trackbooth_assets import _expand_schema_descriptor


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

    assert _expand_schema_descriptor(descriptor, {}) is None


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
