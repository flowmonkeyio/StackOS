"""Cloudflare DNS actions through the generic StackOS executor."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository
from stackos.db.models import ActionCall
from stackos.plugins.manifest import BUILTIN_PLUGIN_MANIFESTS
from stackos.repositories.base import ConflictError
from stackos.repositories.projects import IntegrationCredentialRepository

_ZONE_ID = "023e105f4ecef8ad9ca31a8372d0c353"
_RECORD_ID = "372e67954025e0ba6aaa6d586b9e0b59"
_RECORD_TYPES = {
    "A",
    "AAAA",
    "CAA",
    "CERT",
    "CNAME",
    "DNSKEY",
    "DS",
    "HTTPS",
    "LOC",
    "MX",
    "NAPTR",
    "NS",
    "OPENPGPKEY",
    "PTR",
    "SMIMEA",
    "SRV",
    "SSHFP",
    "SVCB",
    "TLSA",
    "TXT",
    "URI",
}
_FORMATTED_CONTENT_RECORD_TYPES = {
    "CAA",
    "CERT",
    "DNSKEY",
    "DS",
    "HTTPS",
    "LOC",
    "NAPTR",
    "SMIMEA",
    "SRV",
    "SSHFP",
    "SVCB",
    "TLSA",
    "URI",
}
_SIMPLE_CONTENT_RECORD_TYPES = {
    "A",
    "AAAA",
    "CNAME",
    "MX",
    "NS",
    "OPENPGPKEY",
    "PTR",
    "TXT",
}
_STRUCTURED_BOUNDS = [
    ("CAA", "flags", 0, 255),
    ("CERT", "algorithm", 0, 255),
    ("CERT", "key_tag", 0, 65535),
    ("CERT", "type", 0, 65535),
    ("DNSKEY", "algorithm", 0, 255),
    ("DNSKEY", "flags", 0, 65535),
    ("DNSKEY", "protocol", 0, 255),
    ("DS", "algorithm", 0, 255),
    ("DS", "digest_type", 0, 255),
    ("DS", "key_tag", 0, 65535),
    ("HTTPS", "priority", 0, 65535),
    ("LOC", "altitude", -100000, 42849672.95),
    ("LOC", "lat_degrees", 0, 90),
    ("LOC", "lat_minutes", 0, 59),
    ("LOC", "lat_seconds", 0, 59.999),
    ("LOC", "long_degrees", 0, 180),
    ("LOC", "long_minutes", 0, 59),
    ("LOC", "long_seconds", 0, 59.999),
    ("LOC", "precision_horz", 0, 90000000),
    ("LOC", "precision_vert", 0, 90000000),
    ("LOC", "size", 0, 90000000),
    ("NAPTR", "order", 0, 65535),
    ("NAPTR", "preference", 0, 65535),
    ("SMIMEA", "matching_type", 0, 255),
    ("SMIMEA", "selector", 0, 255),
    ("SMIMEA", "usage", 0, 255),
    ("SRV", "port", 0, 65535),
    ("SRV", "priority", 0, 65535),
    ("SRV", "weight", 0, 65535),
    ("SSHFP", "algorithm", 0, 255),
    ("SSHFP", "type", 0, 255),
    ("SVCB", "priority", 0, 65535),
    ("TLSA", "matching_type", 0, 255),
    ("TLSA", "selector", 0, 255),
    ("TLSA", "usage", 0, 255),
    ("URI", "weight", 0, 65535),
]


def _credential_ref(session: Session, project_id: int) -> str:
    ActionRepository(session).describe(action_ref="utils.cloudflare.zones.list")
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="cloudflare",
        secret_payload=b"cloudflare-action-secret",
    )
    status = AuthRepository(session).status(project_id=project_id, provider_key="cloudflare")
    return status.connections[0].credential_ref


def _envelope(result: Any, *, result_info: dict[str, int] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "success": True,
        "errors": [],
        "messages": [],
        "result": result,
    }
    if result_info is not None:
        body["result_info"] = result_info
    return body


def _record_for(record_type: str) -> dict[str, Any]:
    record: dict[str, Any] = {
        "name": "service.example.org",
        "ttl": 3600,
        "type": record_type,
    }
    content_by_type = {
        "A": "198.51.100.4",
        "AAAA": "2001:db8::4",
        "CNAME": "target.example.org",
        "MX": "mail.example.org",
        "NS": "ns1.example.org",
        "OPENPGPKEY": "b3BlbnBncC1rZXk=",
        "PTR": "host.example.org",
        "TXT": "verification=value",
    }
    data_by_type: dict[str, dict[str, Any]] = {
        "CAA": {"flags": 0, "tag": "issue", "value": "letsencrypt.org"},
        "CERT": {"algorithm": 8, "certificate": "certificate", "key_tag": 1, "type": 1},
        "DNSKEY": {"algorithm": 13, "flags": 257, "protocol": 3, "public_key": "key"},
        "DS": {"algorithm": 13, "digest": "ABCDEF", "digest_type": 2, "key_tag": 1},
        "HTTPS": {"priority": 1, "target": ".", "value": "alpn=h2"},
        "LOC": {
            "altitude": 0,
            "lat_degrees": 34,
            "lat_direction": "N",
            "lat_minutes": 3,
            "lat_seconds": 8.4,
            "long_degrees": 118,
            "long_direction": "W",
            "long_minutes": 14,
            "long_seconds": 37.2,
            "precision_horz": 100,
            "precision_vert": 100,
            "size": 1,
        },
        "NAPTR": {
            "flags": "S",
            "order": 10,
            "preference": 20,
            "regex": "",
            "replacement": "_sip._tcp.example.org",
            "service": "SIP+D2T",
        },
        "SMIMEA": {"certificate": "ABCDEF", "matching_type": 1, "selector": 1, "usage": 3},
        "SRV": {"port": 443, "priority": 10, "target": "host.example.org", "weight": 5},
        "SSHFP": {"algorithm": 4, "fingerprint": "ABCDEF", "type": 2},
        "SVCB": {"priority": 1, "target": ".", "value": "alpn=h2"},
        "TLSA": {"certificate": "ABCDEF", "matching_type": 1, "selector": 1, "usage": 3},
        "URI": {"target": "https://example.org/path", "weight": 10},
    }
    if record_type in content_by_type:
        record["content"] = content_by_type[record_type]
    else:
        record["data"] = data_by_type[record_type]
    if record_type in {"MX", "URI"}:
        record["priority"] = 10
    return record


def _formatted_record_for(record_type: str) -> dict[str, Any]:
    record: dict[str, Any] = {
        "name": "service.example.org",
        "ttl": 3600,
        "type": record_type,
        "content": "provider-formatted record content",
    }
    if record_type == "URI":
        record["priority"] = 10
    return record


def test_all_current_record_types_and_contract_boundaries_validate(
    session: Session,
    project_id: int,
) -> None:
    credential_ref = _credential_ref(session, project_id)
    repo = ActionRepository(session)

    for record_type in sorted(_RECORD_TYPES):
        validation = repo.validate(
            project_id=project_id,
            action_ref="utils.cloudflare.dns.records.create",
            input_json={"zone_id": _ZONE_ID, "record": _record_for(record_type)},
            credential_ref=credential_ref,
        )
        assert validation.valid is True, (record_type, validation.issues)

    for record_type in sorted(_FORMATTED_CONTENT_RECORD_TYPES):
        record = _formatted_record_for(record_type)
        for action_ref, input_json in (
            (
                "utils.cloudflare.dns.records.create",
                {"zone_id": _ZONE_ID, "record": record},
            ),
            (
                "utils.cloudflare.dns.records.edit",
                {
                    "zone_id": _ZONE_ID,
                    "dns_record_id": _RECORD_ID,
                    "record": record,
                },
            ),
            (
                "utils.cloudflare.dns.records.replace",
                {
                    "zone_id": _ZONE_ID,
                    "dns_record_id": _RECORD_ID,
                    "record": record,
                },
            ),
        ):
            validation = repo.validate(
                project_id=project_id,
                action_ref=action_ref,
                input_json=input_json,
                credential_ref=credential_ref,
            )
            assert validation.valid is True, (
                action_ref,
                record_type,
                validation.issues,
            )

    for record_type in sorted(_SIMPLE_CONTENT_RECORD_TYPES):
        record: dict[str, Any] = {
            "name": "service.example.org",
            "ttl": 3600,
            "type": record_type,
        }
        if record_type == "MX":
            record["priority"] = 10
        for action_ref, input_json in (
            (
                "utils.cloudflare.dns.records.create",
                {"zone_id": _ZONE_ID, "record": record},
            ),
            (
                "utils.cloudflare.dns.records.edit",
                {
                    "zone_id": _ZONE_ID,
                    "dns_record_id": _RECORD_ID,
                    "record": record,
                },
            ),
            (
                "utils.cloudflare.dns.records.replace",
                {
                    "zone_id": _ZONE_ID,
                    "dns_record_id": _RECORD_ID,
                    "record": record,
                },
            ),
        ):
            validation = repo.validate(
                project_id=project_id,
                action_ref=action_ref,
                input_json=input_json,
                credential_ref=credential_ref,
            )
            assert validation.valid is True, (
                action_ref,
                record_type,
                validation.issues,
            )

    for record in (
        {
            "name": "service.example.org",
            "ttl": 3600,
            "type": "CAA",
        },
        {
            "name": "service.example.org",
            "ttl": 3600,
            "type": "CAA",
            "data": {"flags": 0},
        },
        {
            "name": "service.example.org",
            "ttl": 3600,
            "type": "CAA",
            "content": '0 issue "letsencrypt.org"',
            "data": {"flags": 0, "tag": "issue", "value": "letsencrypt.org"},
        },
    ):
        validation = repo.validate(
            project_id=project_id,
            action_ref="utils.cloudflare.dns.records.create",
            input_json={"zone_id": _ZONE_ID, "record": record},
            credential_ref=credential_ref,
        )
        assert validation.valid is True, validation.issues

    edit = repo.validate(
        project_id=project_id,
        action_ref="utils.cloudflare.dns.records.edit",
        input_json={
            "zone_id": _ZONE_ID,
            "dns_record_id": _RECORD_ID,
            "record": {
                "name": "service.example.org",
                "ttl": 1,
                "type": "A",
                "comment": "metadata-only edit",
            },
        },
        credential_ref=credential_ref,
    )
    assert edit.valid is True, edit.issues

    invalid_inputs = [
        {
            "zone_id": _ZONE_ID,
            "record": {
                "name": "service.example.org",
                "ttl": 29,
                "type": "A",
                "content": "198.51.100.4",
            },
        },
        {
            "zone_id": _ZONE_ID,
            "record": {**_record_for("A"), "unknown": "not-a-provider-field"},
        },
        {
            "zone_id": _ZONE_ID,
            "record": {**_record_for("A"), "comment": None},
        },
        {
            "zone_id": _ZONE_ID,
            "record": {
                "name": "service.example.org",
                "ttl": 3600,
                "type": "CAA",
                "content": {"flags": 0},
            },
        },
    ]
    for input_json in invalid_inputs:
        validation = repo.validate(
            project_id=project_id,
            action_ref="utils.cloudflare.dns.records.create",
            input_json=input_json,
            credential_ref=credential_ref,
        )
        assert validation.valid is False


def test_explicit_dns_mutations_execute_without_connector_specific_gates_and_are_audited(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _credential_ref(session, project_id)
    record = _record_for("A")
    created_record = {"id": _RECORD_ID, **record, "proxiable": True}
    httpx_mock.add_response(
        method="POST",
        url=(
            f"https://api.cloudflare.com/client/v4/zones/{_ZONE_ID}/dns_records"
            "?include_shadow_metadata=true"
        ),
        json=_envelope(created_record),
        headers={"CF-Ray": "create-ray-LAX"},
    )
    httpx_mock.add_response(
        method="DELETE",
        url=f"https://api.cloudflare.com/client/v4/zones/{_ZONE_ID}/dns_records/{_RECORD_ID}",
        json={"result": {"id": _RECORD_ID}},
        headers={"CF-Ray": "delete-ray-LAX"},
    )
    repo = ActionRepository(session)

    created = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="utils.cloudflare.dns.records.create",
            input_json={
                "zone_id": _ZONE_ID,
                "include_shadow_metadata": True,
                "record": record,
            },
            credential_ref=credential_ref,
        )
    ).data
    deleted = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="utils.cloudflare.dns.records.delete",
            input_json={"zone_id": _ZONE_ID, "dns_record_id": _RECORD_ID},
            credential_ref=credential_ref,
        )
    ).data

    requests = httpx_mock.get_requests()
    assert [request.method for request in requests] == ["POST", "DELETE"]
    assert dict(requests[0].url.params) == {"include_shadow_metadata": "true"}
    assert json.loads(requests[0].content) == record
    assert requests[1].content == b""
    assert created.output_json["body"] == _envelope(created_record)
    assert deleted.output_json["body"] == {"result": {"id": _RECORD_ID}}
    assert created.action_call.connector_key == "cloudflare"
    assert deleted.action_call.connector_key == "cloudflare"
    assert created.action_call.request_json == {
        "zone_id": _ZONE_ID,
        "include_shadow_metadata": True,
        "record": record,
    }
    assert created.action_call.response_json == created.output_json
    assert created.metadata_json["cf_ray"] == "create-ray-LAX"
    assert deleted.metadata_json["cf_ray"] == "delete-ray-LAX"
    rendered = json.dumps(
        {
            "created": created.model_dump(mode="json"),
            "deleted": deleted.model_dump(mode="json"),
        }
    )
    assert "cloudflare-action-secret" not in rendered


def test_formatted_structured_content_is_forwarded_without_stackos_grammar_rules(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _credential_ref(session, project_id)
    record = {
        "name": "example.org",
        "ttl": 3600,
        "type": "CAA",
        "content": '0 issue "letsencrypt.org"',
    }
    provider_record = {"id": _RECORD_ID, **record}
    httpx_mock.add_response(
        method="POST",
        url=f"https://api.cloudflare.com/client/v4/zones/{_ZONE_ID}/dns_records",
        json=_envelope(provider_record),
    )

    result = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="utils.cloudflare.dns.records.create",
            input_json={"zone_id": _ZONE_ID, "record": record},
            credential_ref=credential_ref,
        )
    ).data

    request = httpx_mock.get_request()
    assert request is not None
    assert json.loads(request.content) == record
    assert result.output_json["body"]["result"] == provider_record


def test_all_documented_structured_numeric_boundaries_are_pinned(
    session: Session,
    project_id: int,
) -> None:
    credential_ref = _credential_ref(session, project_id)
    repo = ActionRepository(session)

    for record_type, field, minimum, maximum in _STRUCTURED_BOUNDS:
        base = _record_for(record_type)
        for value in (minimum, maximum):
            record = {**base, "data": {**base["data"], field: value}}
            validation = repo.validate(
                project_id=project_id,
                action_ref="utils.cloudflare.dns.records.create",
                input_json={"zone_id": _ZONE_ID, "record": record},
                credential_ref=credential_ref,
            )
            assert validation.valid is True, (record_type, field, value, validation.issues)

        delta = 0.001 if isinstance(maximum, float) else 1
        for value in (minimum - delta, maximum + delta):
            record = {**base, "data": {**base["data"], field: value}}
            validation = repo.validate(
                project_id=project_id,
                action_ref="utils.cloudflare.dns.records.create",
                input_json={"zone_id": _ZONE_ID, "record": record},
                credential_ref=credential_ref,
            )
            assert validation.valid is False, (record_type, field, value)


def test_cloudflare_provider_errors_preserve_repair_data_and_redact_echoed_token(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _credential_ref(session, project_id)
    httpx_mock.add_response(
        method="POST",
        url=f"https://api.cloudflare.com/client/v4/zones/{_ZONE_ID}/dns_records",
        status_code=400,
        json={
            "success": False,
            "errors": [
                {
                    "code": 9005,
                    "message": "bad content; echoed cloudflare-action-secret",
                    "source": {"pointer": "/content"},
                }
            ],
            "messages": [],
            "result": None,
        },
        headers={"CF-Ray": "validation-ray-LAX"},
    )

    with pytest.raises(ConflictError) as exc_info:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="utils.cloudflare.dns.records.create",
                input_json={"zone_id": _ZONE_ID, "record": _record_for("A")},
                credential_ref=credential_ref,
            )
        )

    data = exc_info.value.data
    rendered = json.dumps(data)
    assert data["provider_status_code"] == 400
    assert data["provider_error"]["errors"][0]["code"] == 9005
    assert "cloudflare-action-secret" not in rendered
    call = session.exec(select(ActionCall).where(ActionCall.id == data["action_call_id"])).one()
    audit = json.dumps(
        {"response": call.response_json, "metadata": call.metadata_json},
        sort_keys=True,
    )
    assert "validation-ray-LAX" in audit
    assert "cloudflare-action-secret" not in audit


def test_cloudflare_action_catalog_is_dns_only_and_has_no_extra_gate_config(
    session: Session,
) -> None:
    repo = ActionRepository(session)
    expected = {
        "utils.cloudflare.zones.list": "read",
        "utils.cloudflare.dns.records.list": "read",
        "utils.cloudflare.dns.records.get": "read",
        "utils.cloudflare.dns.records.create": "write",
        "utils.cloudflare.dns.records.edit": "write",
        "utils.cloudflare.dns.records.replace": "write",
        "utils.cloudflare.dns.records.delete": "write",
    }

    for action_ref, risk_level in expected.items():
        described = repo.describe(action_ref=action_ref)
        assert described.connector_registered is True
        assert described.manifest.connector_key == "cloudflare"
        assert described.manifest.risk_level == risk_level
        assert described.manifest.requires_credential is True
        config = described.manifest.config_json
        assert config is not None
        assert "approval" not in json.dumps(config).lower()
        assert "allowlist" not in json.dumps(config).lower()
        assert "default_zone" not in json.dumps(config).lower()

    utils = next(manifest for manifest in BUILTIN_PLUGIN_MANIFESTS if manifest.slug == "utils")
    installed = {
        f"utils.{action.key}" for action in utils.actions if action.provider == "cloudflare"
    }
    assert installed == set(expected)

    shadow_param_actions = {
        "utils.cloudflare.dns.records.get",
        "utils.cloudflare.dns.records.create",
        "utils.cloudflare.dns.records.edit",
        "utils.cloudflare.dns.records.replace",
    }
    for action_ref in shadow_param_actions:
        properties = repo.describe(action_ref=action_ref).manifest.input_schema_json["properties"]
        assert properties["include_shadow_metadata"] == {"type": "boolean"}
    delete_properties = repo.describe(
        action_ref="utils.cloudflare.dns.records.delete"
    ).manifest.input_schema_json["properties"]
    assert "include_shadow_metadata" not in delete_properties


def test_cloudflare_list_action_schemas_use_exact_current_query_names(session: Session) -> None:
    repo = ActionRepository(session)
    zone_schema = repo.describe(action_ref="utils.cloudflare.zones.list").manifest.input_schema_json
    record_schema = repo.describe(
        action_ref="utils.cloudflare.dns.records.list"
    ).manifest.input_schema_json

    assert set(zone_schema["properties"]) == {
        "name",
        "status",
        "type",
        "account.id",
        "account.name",
        "page",
        "per_page",
        "order",
        "direction",
        "match",
    }
    assert set(record_schema["properties"]) == {
        "zone_id",
        "name",
        "name.exact",
        "name.contains",
        "name.startswith",
        "name.endswith",
        "type",
        "content",
        "content.exact",
        "content.contains",
        "content.startswith",
        "content.endswith",
        "proxied",
        "match",
        "comment",
        "comment.present",
        "comment.absent",
        "comment.exact",
        "comment.contains",
        "comment.startswith",
        "comment.endswith",
        "tag",
        "tag.present",
        "tag.absent",
        "tag.exact",
        "tag.contains",
        "tag.startswith",
        "tag.endswith",
        "search",
        "tag_match",
        "page",
        "per_page",
        "order",
        "direction",
        "include_shadow_metadata",
        "shadowed_by_name",
        "shadowing_name",
    }
