"""Decision-free Cloudflare v4 zone discovery and DNS record CRUD actions.

Official contracts:
- https://developers.cloudflare.com/api/resources/zones/methods/list/
- https://developers.cloudflare.com/api/resources/dns/subresources/records/
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.provider_utils import credential_value, issue, unknown_operation
from stackos.artifacts import redact_secret_text, redact_secrets
from stackos.integrations.cloudflare import CloudflareIntegration
from stackos.mcp.errors import IntegrationDownError, RateLimitedError
from stackos.repositories.base import ValidationError

_OPERATIONS = {
    "zones.list",
    "dns.records.list",
    "dns.records.get",
    "dns.records.create",
    "dns.records.edit",
    "dns.records.replace",
    "dns.records.delete",
}
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
_CONTENT_RECORD_TYPES = {"A", "AAAA", "CNAME", "MX", "NS", "OPENPGPKEY", "PTR", "TXT"}
_ZONE_QUERY_FIELDS = {
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
_RECORD_QUERY_FIELDS = {
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
_COMMON_RECORD_FIELDS = {"name", "ttl", "type", "comment", "proxied", "settings", "tags"}
_DATA_FIELDS: dict[str, dict[str, str]] = {
    "CAA": {"flags": "number", "tag": "string", "value": "string"},
    "CERT": {
        "algorithm": "number",
        "certificate": "string",
        "key_tag": "number",
        "type": "number",
    },
    "DNSKEY": {
        "algorithm": "number",
        "flags": "number",
        "protocol": "number",
        "public_key": "string",
    },
    "DS": {
        "algorithm": "number",
        "digest": "string",
        "digest_type": "number",
        "key_tag": "number",
    },
    "HTTPS": {"priority": "number", "target": "string", "value": "string"},
    "LOC": {
        "altitude": "number",
        "lat_degrees": "number",
        "lat_direction": "string",
        "lat_minutes": "number",
        "lat_seconds": "number",
        "long_degrees": "number",
        "long_direction": "string",
        "long_minutes": "number",
        "long_seconds": "number",
        "precision_horz": "number",
        "precision_vert": "number",
        "size": "number",
    },
    "NAPTR": {
        "flags": "string",
        "order": "number",
        "preference": "number",
        "regex": "string",
        "replacement": "string",
        "service": "string",
    },
    "SMIMEA": {
        "certificate": "string",
        "matching_type": "number",
        "selector": "number",
        "usage": "number",
    },
    "SRV": {"port": "number", "priority": "number", "target": "string", "weight": "number"},
    "SSHFP": {"algorithm": "number", "fingerprint": "string", "type": "number"},
    "SVCB": {"priority": "number", "target": "string", "value": "string"},
    "TLSA": {
        "certificate": "string",
        "matching_type": "number",
        "selector": "number",
        "usage": "number",
    },
    "URI": {"target": "string", "weight": "number"},
}
_DATA_RANGES: dict[tuple[str, str], tuple[float, float]] = {
    ("CAA", "flags"): (0, 255),
    ("CERT", "algorithm"): (0, 255),
    ("CERT", "key_tag"): (0, 65535),
    ("CERT", "type"): (0, 65535),
    ("DNSKEY", "algorithm"): (0, 255),
    ("DNSKEY", "flags"): (0, 65535),
    ("DNSKEY", "protocol"): (0, 255),
    ("DS", "algorithm"): (0, 255),
    ("DS", "digest_type"): (0, 255),
    ("DS", "key_tag"): (0, 65535),
    ("HTTPS", "priority"): (0, 65535),
    ("LOC", "altitude"): (-100000, 42849672.95),
    ("LOC", "lat_degrees"): (0, 90),
    ("LOC", "lat_minutes"): (0, 59),
    ("LOC", "lat_seconds"): (0, 59.999),
    ("LOC", "long_degrees"): (0, 180),
    ("LOC", "long_minutes"): (0, 59),
    ("LOC", "long_seconds"): (0, 59.999),
    ("LOC", "precision_horz"): (0, 90000000),
    ("LOC", "precision_vert"): (0, 90000000),
    ("LOC", "size"): (0, 90000000),
    ("NAPTR", "order"): (0, 65535),
    ("NAPTR", "preference"): (0, 65535),
    ("SMIMEA", "matching_type"): (0, 255),
    ("SMIMEA", "selector"): (0, 255),
    ("SMIMEA", "usage"): (0, 255),
    ("SRV", "port"): (0, 65535),
    ("SRV", "priority"): (0, 65535),
    ("SRV", "weight"): (0, 65535),
    ("SSHFP", "algorithm"): (0, 255),
    ("SSHFP", "type"): (0, 255),
    ("SVCB", "priority"): (0, 65535),
    ("TLSA", "matching_type"): (0, 255),
    ("TLSA", "selector"): (0, 255),
    ("TLSA", "usage"): (0, 255),
    ("URI", "weight"): (0, 65535),
}


class CloudflareActionConnector:
    """Execute exactly one explicitly selected Cloudflare DNS action."""

    key = "cloudflare"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        if request.operation not in _OPERATIONS:
            return unknown_operation(request)
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case "zones.list":
                _unknown_fields(payload, _ZONE_QUERY_FIELDS, "$", issues)
                _validate_zone_query(payload, issues)
            case "dns.records.list":
                _unknown_fields(payload, _RECORD_QUERY_FIELDS | {"zone_id"}, "$", issues)
                _identifier(payload.get("zone_id"), "$.zone_id", issues)
                _validate_record_query(payload, issues)
            case "dns.records.get":
                _unknown_fields(
                    payload,
                    {"zone_id", "dns_record_id", "include_shadow_metadata"},
                    "$",
                    issues,
                )
                _identifier(payload.get("zone_id"), "$.zone_id", issues)
                _identifier(payload.get("dns_record_id"), "$.dns_record_id", issues)
                _boolean(payload, "include_shadow_metadata", issues)
            case "dns.records.delete":
                _unknown_fields(payload, {"zone_id", "dns_record_id"}, "$", issues)
                _identifier(payload.get("zone_id"), "$.zone_id", issues)
                _identifier(payload.get("dns_record_id"), "$.dns_record_id", issues)
            case "dns.records.create":
                _unknown_fields(
                    payload,
                    {"zone_id", "record", "include_shadow_metadata"},
                    "$",
                    issues,
                )
                _identifier(payload.get("zone_id"), "$.zone_id", issues)
                _boolean(payload, "include_shadow_metadata", issues)
                _record(payload.get("record"), "$.record", issues)
            case "dns.records.edit" | "dns.records.replace":
                _unknown_fields(
                    payload,
                    {"zone_id", "dns_record_id", "record", "include_shadow_metadata"},
                    "$",
                    issues,
                )
                _identifier(payload.get("zone_id"), "$.zone_id", issues)
                _identifier(payload.get("dns_record_id"), "$.dns_record_id", issues)
                _boolean(payload, "include_shadow_metadata", issues)
                _record(payload.get("record"), "$.record", issues)
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.operation not in _OPERATIONS:
            raise ValidationError(f"unsupported Cloudflare operation {request.operation!r}")
        token = credential_value(request, "api_token", "token")
        payload = request.input_json
        try:
            async with httpx.AsyncClient(timeout=60.0) as http:
                client = CloudflareIntegration(
                    payload=token.encode("utf-8"),
                    project_id=request.project_id,
                    http=http,
                )
                match request.operation:
                    case "zones.list":
                        call_result = await client.list_zones(params=payload)
                    case "dns.records.list":
                        call_result = await client.list_dns_records(
                            zone_id=str(payload["zone_id"]),
                            params={
                                key: value for key, value in payload.items() if key != "zone_id"
                            },
                        )
                    case "dns.records.get":
                        call_result = await client.get_dns_record(
                            zone_id=str(payload["zone_id"]),
                            dns_record_id=str(payload["dns_record_id"]),
                            params=_shadow_params(payload),
                        )
                    case "dns.records.create":
                        call_result = await client.create_dns_record(
                            zone_id=str(payload["zone_id"]),
                            record=_mapping(payload["record"], "record"),
                            params=_shadow_params(payload),
                        )
                    case "dns.records.edit":
                        call_result = await client.edit_dns_record(
                            zone_id=str(payload["zone_id"]),
                            dns_record_id=str(payload["dns_record_id"]),
                            record=_mapping(payload["record"], "record"),
                            params=_shadow_params(payload),
                        )
                    case "dns.records.replace":
                        call_result = await client.replace_dns_record(
                            zone_id=str(payload["zone_id"]),
                            dns_record_id=str(payload["dns_record_id"]),
                            record=_mapping(payload["record"], "record"),
                            params=_shadow_params(payload),
                        )
                    case "dns.records.delete":
                        call_result = await client.delete_dns_record(
                            zone_id=str(payload["zone_id"]),
                            dns_record_id=str(payload["dns_record_id"]),
                        )
                    case _:  # pragma: no cover - guarded above
                        raise ValidationError(
                            f"unsupported Cloudflare operation {request.operation!r}"
                        )
        except (IntegrationDownError, RateLimitedError) as exc:
            raise _connector_error(exc, operation=request.operation, secret=token) from exc

        metadata = {
            "vendor": "cloudflare",
            "operation": request.operation,
            **(call_result.metadata or {}),
        }
        return ActionConnectorResult(
            output_json={
                "provider": "cloudflare",
                "operation": request.operation,
                "status_code": metadata.get("status_code", 200),
                "body": call_result.data,
            },
            metadata_json=metadata,
            cost_cents=round(call_result.cost_usd * 100),
        )


def _validate_zone_query(payload: Mapping[str, Any], issues: list[ActionValidationIssue]) -> None:
    _strings(payload, {"name", "account.id", "account.name"}, issues)
    _enum(payload, "status", {"initializing", "pending", "active", "moved"}, issues)
    _string_list(payload, "type", {"full", "partial", "secondary", "internal"}, issues)
    _integer(payload, "page", 1, None, issues)
    _integer(payload, "per_page", 5, 50, issues)
    _enum(payload, "order", {"name", "status", "account.id", "account.name", "plan.id"}, issues)
    _enum(payload, "direction", {"asc", "desc"}, issues)
    _enum(payload, "match", {"any", "all"}, issues)
    for key in ("name", "account.name"):
        value = payload.get(key)
        if isinstance(value, str) and len(value) > 253:
            issues.append(issue(f"$.{key}", f"{key} must contain at most 253 characters"))


def _validate_record_query(payload: Mapping[str, Any], issues: list[ActionValidationIssue]) -> None:
    string_fields = _RECORD_QUERY_FIELDS - {
        "type",
        "proxied",
        "match",
        "tag_match",
        "page",
        "per_page",
        "order",
        "direction",
        "include_shadow_metadata",
    }
    _strings(payload, string_fields, issues)
    _enum(payload, "type", _RECORD_TYPES, issues)
    _boolean(payload, "proxied", issues)
    _boolean(payload, "include_shadow_metadata", issues)
    _enum(payload, "match", {"any", "all"}, issues)
    _enum(payload, "tag_match", {"any", "all"}, issues)
    _integer(payload, "page", 1, None, issues)
    _integer(payload, "per_page", 1, 5_000_000, issues)
    _enum(payload, "order", {"type", "name", "content", "ttl", "proxied"}, issues)
    _enum(payload, "direction", {"asc", "desc"}, issues)


def _record(
    value: Any,
    path: str,
    issues: list[ActionValidationIssue],
) -> None:
    if not isinstance(value, Mapping):
        issues.append(issue(path, "record must be an object", "type_error"))
        return
    record_type = value.get("type")
    if not isinstance(record_type, str) or record_type not in _RECORD_TYPES:
        issues.append(issue(f"{path}.type", "type must be a current Cloudflare DNS type"))
        return
    allowed = set(_COMMON_RECORD_FIELDS)
    if record_type in _CONTENT_RECORD_TYPES:
        allowed.add("content")
    else:
        allowed.add("content")
        allowed.add("data")
    if record_type in {"MX", "URI"}:
        allowed.add("priority")
    if record_type in {"A", "AAAA"}:
        allowed.add("private_routing")
    _unknown_fields(value, allowed, path, issues)

    name = value.get("name")
    if not isinstance(name, str) or not name:
        issues.append(issue(f"{path}.name", "name is required", "required"))
    elif len(name) > 255:
        issues.append(issue(f"{path}.name", "name must contain at most 255 characters"))
    _ttl(value.get("ttl"), f"{path}.ttl", issues)

    if record_type in _CONTENT_RECORD_TYPES:
        content = value.get("content")
        if content is not None and not isinstance(content, str):
            issues.append(issue(f"{path}.content", "content must be a string", "type_error"))
    else:
        has_content = "content" in value
        has_data = "data" in value
        content = value.get("content")
        if has_content and not isinstance(content, str):
            issues.append(issue(f"{path}.content", "content must be a string", "type_error"))
        if has_data:
            _record_data(value.get("data"), record_type, f"{path}.data", issues)

    if record_type in {"MX", "URI"}:
        _bounded_number(value.get("priority"), f"{path}.priority", 0, 65535, issues, required=True)
    _optional_boolean(value, "proxied", path, issues)
    if record_type in {"A", "AAAA"}:
        _optional_boolean(value, "private_routing", path, issues)
    _settings(value.get("settings"), record_type, f"{path}.settings", issues)
    _tags(value.get("tags"), f"{path}.tags", issues)
    if "comment" in value and not isinstance(value.get("comment"), str):
        issues.append(issue(f"{path}.comment", "comment must be a string", "type_error"))


def _record_data(
    value: Any,
    record_type: str,
    path: str,
    issues: list[ActionValidationIssue],
) -> None:
    if not isinstance(value, Mapping):
        issues.append(issue(path, "data must be an object", "type_error"))
        return
    contract = _DATA_FIELDS[record_type]
    _unknown_fields(value, set(contract), path, issues)
    for key, kind in contract.items():
        if key not in value:
            continue
        field_value = value[key]
        if kind == "string":
            if not isinstance(field_value, str):
                issues.append(issue(f"{path}.{key}", f"{key} must be a string", "type_error"))
            continue
        bounds = _DATA_RANGES.get((record_type, key))
        if bounds is not None:
            _bounded_number(field_value, f"{path}.{key}", *bounds, issues, required=True)
        elif not _is_number(field_value):
            issues.append(issue(f"{path}.{key}", f"{key} must be a number", "type_error"))
    if record_type == "LOC":
        _value_enum(value, "lat_direction", {"N", "S"}, path, issues)
        _value_enum(value, "long_direction", {"E", "W"}, path, issues)


def _settings(
    value: Any,
    record_type: str,
    path: str,
    issues: list[ActionValidationIssue],
) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        issues.append(issue(path, "settings must be an object", "type_error"))
        return
    allowed = {"ipv4_only", "ipv6_only"}
    if record_type == "CNAME":
        allowed.add("flatten_cname")
    _unknown_fields(value, allowed, path, issues)
    for key, item in value.items():
        if key in allowed and not isinstance(item, bool):
            issues.append(issue(f"{path}.{key}", f"{key} must be a boolean", "type_error"))


def _tags(value: Any, path: str, issues: list[ActionValidationIssue]) -> None:
    if value is None:
        return
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        issues.append(issue(path, "tags must be an array of strings", "type_error"))


def _identifier(value: Any, path: str, issues: list[ActionValidationIssue]) -> None:
    if not isinstance(value, str) or not value.strip():
        issues.append(issue(path, "identifier is required", "required"))
    elif len(value.strip()) > 32:
        issues.append(issue(path, "identifier must contain at most 32 characters"))


def _ttl(value: Any, path: str, issues: list[ActionValidationIssue]) -> None:
    if not _is_number(value):
        issues.append(issue(path, "ttl is required and must be a number", "required"))
        return
    if value != 1 and not 30 <= value <= 86400:
        issues.append(issue(path, "ttl must be 1 (automatic) or between 30 and 86400"))


def _unknown_fields(
    payload: Mapping[str, Any],
    allowed: set[str],
    path: str,
    issues: list[ActionValidationIssue],
) -> None:
    for key in payload:
        if key not in allowed:
            issues.append(
                issue(f"{path}.{key}", f"unknown Cloudflare field {key!r}", "unknown_field")
            )


def _strings(
    payload: Mapping[str, Any], keys: set[str], issues: list[ActionValidationIssue]
) -> None:
    for key in keys:
        value = payload.get(key)
        if value is not None and not isinstance(value, str):
            issues.append(issue(f"$.{key}", f"{key} must be a string", "type_error"))


def _string_list(
    payload: Mapping[str, Any],
    key: str,
    allowed: set[str],
    issues: list[ActionValidationIssue],
) -> None:
    value = payload.get(key)
    if value is None:
        return
    if not isinstance(value, list) or any(item not in allowed for item in value):
        issues.append(issue(f"$.{key}", f"{key} must be an array of documented values"))


def _enum(
    payload: Mapping[str, Any],
    key: str,
    allowed: set[str],
    issues: list[ActionValidationIssue],
) -> None:
    value = payload.get(key)
    if value is not None and value not in allowed:
        issues.append(issue(f"$.{key}", f"{key} must be one of {sorted(allowed)}", "enum_mismatch"))


def _value_enum(
    payload: Mapping[str, Any],
    key: str,
    allowed: set[str],
    path: str,
    issues: list[ActionValidationIssue],
) -> None:
    value = payload.get(key)
    if value is not None and value not in allowed:
        issues.append(issue(f"{path}.{key}", f"{key} must be one of {sorted(allowed)}"))


def _integer(
    payload: Mapping[str, Any],
    key: str,
    minimum: int,
    maximum: int | None,
    issues: list[ActionValidationIssue],
) -> None:
    value = payload.get(key)
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool):
        issues.append(issue(f"$.{key}", f"{key} must be an integer", "type_error"))
    elif value < minimum or (maximum is not None and value > maximum):
        issues.append(issue(f"$.{key}", f"{key} is outside the documented range"))


def _boolean(payload: Mapping[str, Any], key: str, issues: list[ActionValidationIssue]) -> None:
    value = payload.get(key)
    if value is not None and not isinstance(value, bool):
        issues.append(issue(f"$.{key}", f"{key} must be a boolean", "type_error"))


def _optional_boolean(
    payload: Mapping[str, Any], key: str, path: str, issues: list[ActionValidationIssue]
) -> None:
    value = payload.get(key)
    if value is not None and not isinstance(value, bool):
        issues.append(issue(f"{path}.{key}", f"{key} must be a boolean", "type_error"))


def _bounded_number(
    value: Any,
    path: str,
    minimum: float,
    maximum: float,
    issues: list[ActionValidationIssue],
    *,
    required: bool,
) -> None:
    if value is None:
        if required:
            issues.append(issue(path, f"{path.rsplit('.', 1)[-1]} is required", "required"))
        return
    if not _is_number(value):
        issues.append(issue(path, f"{path.rsplit('.', 1)[-1]} must be a number", "type_error"))
    elif not minimum <= value <= maximum:
        issues.append(issue(path, f"value must be between {minimum} and {maximum}"))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"Cloudflare {label} must be an object")
    return value


def _shadow_params(payload: Mapping[str, Any]) -> dict[str, bool] | None:
    if "include_shadow_metadata" not in payload:
        return None
    return {"include_shadow_metadata": bool(payload["include_shadow_metadata"])}


def _connector_error(
    exc: IntegrationDownError | RateLimitedError,
    *,
    operation: str,
    secret: str,
) -> ActionConnectorError:
    data = exc.data if isinstance(exc.data, dict) else {}
    status = data.get("status")
    try:
        status_code = int(status) if status is not None else None
    except (TypeError, ValueError):
        status_code = None
    provider_error = _redact_exact(data.get("provider_error") or {"message": exc.detail}, secret)
    output: dict[str, Any] = {
        "provider": "cloudflare",
        "operation": operation,
        "status": "failed",
        "provider_status_code": status_code,
        "provider_error": redact_secrets(provider_error),
    }
    metadata: dict[str, Any] = {"vendor": "cloudflare", "operation": operation}
    if status_code is not None:
        metadata["status_code"] = status_code
    for key in (
        "mutation",
        "automatic_retry_count",
        "retry_safe",
        "outcome_unknown",
        "retry_after",
        "reconciliation_action",
        "reconciliation_guidance",
        "repair_guidance",
    ):
        if key in data:
            output[key] = _redact_exact(data[key], secret)
            metadata[key] = _redact_exact(data[key], secret)
    if isinstance(provider_error, Mapping):
        for key in ("cf_ray", "ratelimit", "ratelimit_policy", "retry_after"):
            if provider_error.get(key) is not None:
                metadata[key] = _redact_exact(provider_error[key], secret)
    detail = redact_secret_text(exc.detail.replace(secret, "[REDACTED]"))
    return ActionConnectorError(
        detail,
        provider_status_code=status_code,
        provider_error=redact_secrets(provider_error),
        output_json=output,
        metadata_json=metadata,
    )


def _redact_exact(value: Any, secret: str) -> Any:
    if isinstance(value, str):
        return redact_secret_text(value.replace(secret, "[REDACTED]"))
    if isinstance(value, list):
        return [_redact_exact(item, secret) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _redact_exact(item, secret) for key, item in value.items()}
    return value


__all__ = ["CloudflareActionConnector"]
