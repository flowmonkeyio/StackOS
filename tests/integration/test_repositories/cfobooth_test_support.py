"""Independent wire fixtures; native declarations, probes and transport stay real.

Local protocol controls only. Final837 qualifies the actual emitted CFO catalog.
"""

from copy import deepcopy

import pytest
from stackos_connectors import get_default_client

from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository

ORG = "73837d14-fbd6-40ca-81f4-19e2ce031fc9"
USER = "576bb550-1dcc-430b-bbf2-c3327f603027"
KEY_ID = "53761da2-37bc-40ab-afbb-489fb72a67a1"
CANARY = "s12-private-synthetic-key"
BASE = "http://127.0.0.1:31837/prefix;configured"
AVAILABLE = "cfobooth" in get_default_client().registry.connector_metadata
native_cfo = pytest.mark.skipif(not AVAILABLE, reason="Companion native CFO package required")


def identity(**overrides):
    return {
        "requestId": "probe-fixture",
        "data": {
            "keyId": KEY_ID,
            "organizationId": ORG,
            "user": {"id": USER, "email": "protocol@example.test"},
            "expiresAt": None,
            **overrides,
        },
    }


def descriptor(operation_id="fixture.read", *, method="GET", effect="read", body=None):
    strict_empty = {"type": "object", "properties": {}, "additionalProperties": False}
    return {
        "operationId": operation_id,
        "title": "Protocol fixture",
        "description": "Synthetic local protocol control",
        "route": {
            "method": method,
            "path": "/api/organizations/:organizationId/" + operation_id.replace(".", "/"),
            "body": "json" if body is not None else "none",
            "status": 200,
        },
        "authentication": {
            "kind": "authenticated",
            "credentials": ["api-key"],
            "organizationParameter": "organizationId",
        },
        "scope": "organization",
        "transport": "json",
        "effect": effect,
        "replay": "safe" if effect == "read" else "explicit-only",
        "execution": {"kind": "action"},
        "responseLimit": 16384,
        "schemaValidation": "server",
        "schemas": {
            "params": {
                "type": "object",
                "properties": {"organizationId": {"type": "string", "format": "uuid"}},
                "required": ["organizationId"],
                "additionalProperties": False,
            },
            "query": deepcopy(strict_empty),
            "body": body,
            "result": {"type": "object", "additionalProperties": True},
            "error": {"type": "object", "additionalProperties": True},
        },
    }


def catalog(*operations, token="a" * 64):
    return {
        "requestId": "catalog-fixture",
        "data": {"version": 1, "catalogHash": token, "operations": list(operations)},
    }


async def verified_account(
    session, project_id, httpx_mock, *, base=BASE, key=CANARY, name="CFO protocol"
):
    repo = AuthRepository(session)
    saved = repo.store_credential(
        provider_key="cfobooth",
        display_name=name,
        fields={"api_key": key, "api_base_url": base, "organization_id": ORG},
        attach_project_id=project_id,
    ).data
    httpx_mock.add_response(
        method="GET",
        url=base.rstrip("/") + f"/api/organizations/{ORG}/api-keys/current",
        json=identity(),
    )
    tested = (await repo.test(project_id=project_id, credential_ref=saved.credential_ref)).data
    assert tested.ok is True
    assert (
        repo.get_account(credential_ref=saved.credential_ref).account["metadata_json"]["key_id"]
        == KEY_ID
    )
    return saved.credential_ref


async def sync(session, project_id, ref, httpx_mock, export, **selectors):
    httpx_mock.add_response(
        method="GET", url=BASE + f"/api/organizations/{ORG}/operation-catalog", json=export
    )
    return (
        await ActionRepository(session).execute(
            project_id=project_id,
            action_ref="cfobooth.catalog.sync",
            input_json=selectors,
            credential_ref=ref,
        )
    ).data.output_json
