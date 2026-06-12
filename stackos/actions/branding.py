"""Branding internal resource action connector."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.vendor_utils import (
    dict_field,
    issue,
    optional_str,
    required_dict,
    required_str,
    str_list,
    unknown_operation,
)
from stackos.repositories.base import NotFoundError, ValidationError
from stackos.repositories.resources import ResourceRepository


class BrandingActionConnector:
    """Decision-free adapter for internal branding resource actions."""

    key = "branding"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        match request.operation:
            case "evidence.capture":
                return _validate_capture(request.input_json)
            case "evidence.sanitize-mark":
                return _validate_sanitize_mark(request.input_json)
            case _:
                return unknown_operation(request)

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        match request.operation:
            case "evidence.capture":
                return _capture_evidence(request)
            case "evidence.sanitize-mark":
                return _sanitize_mark(request)
            case _:
                raise ValidationError(f"unsupported branding operation {request.operation!r}")


def _validate_capture(payload: dict[str, Any]) -> list[ActionValidationIssue]:
    issues: list[ActionValidationIssue] = []
    required_str(payload, "title", issues)
    required_str(payload, "evidence_type", issues)
    required_dict(payload, "source", issues)
    required_str(payload, "captured_at", issues)
    optional_str(payload, "summary", issues)
    optional_str(payload, "altitude", issues)
    optional_str(payload, "external_id", issues)
    dict_field(payload, "evidence_payload", issues)
    str_list(payload, "stream_refs", issues)
    source = payload.get("source")
    if isinstance(source, dict):
        required_str(source, "system", issues)
        required_str(source, "ref", issues)
        optional_str(source, "url", issues)
    if payload.get("sanitization_status") not in {None, "raw"}:
        issues.append(
            issue(
                "$.sanitization_status",
                "capture can only create raw evidence; use evidence.sanitize-mark for clearance",
                "fail_closed_clearance",
            )
        )
    return issues


def _validate_sanitize_mark(payload: dict[str, Any]) -> list[ActionValidationIssue]:
    issues: list[ActionValidationIssue] = []
    required_str(payload, "evidence_ref", issues)
    required_str(payload, "verdict", issues)
    required_str(payload, "reviewer", issues)
    required_str(payload, "reason", issues)
    required_str(payload, "decision_ref", issues)
    optional_str(payload, "decided_at", issues)
    return issues


def _capture_evidence(request: ActionConnectorRequest) -> ActionConnectorResult:
    payload = request.input_json
    external_id = str(payload.get("external_id") or _source_external_id(payload["source"]))
    data = {
        "title": payload["title"],
        "evidence_type": payload["evidence_type"],
        "source": payload["source"],
        "captured_at": payload["captured_at"],
        "sanitization_status": "raw",
    }
    for key in ("summary", "evidence_payload", "stream_refs", "altitude"):
        if key in payload:
            data[key] = payload[key]

    repo = ResourceRepository(_session(request))
    created = repo.upsert_record(
        project_id=request.project_id,
        plugin_slug="branding",
        resource_key="evidence-item",
        external_id=external_id,
        title=str(payload["title"]),
        data_json=data,
        provenance_json={
            "action_ref": request.action_ref,
            "operation": request.operation,
            "source_system": payload["source"]["system"],
            "source_ref": payload["source"]["ref"],
        },
    ).data
    return ActionConnectorResult(
        output_json={
            "evidence_ref": _record_ref(created.id),
            "resource_record_id": created.id,
            "sanitization_status": "raw",
        },
        metadata_json={
            "resource_record_id": created.id,
            "resource_key": "evidence-item",
            "external_id": external_id,
        },
    )


def _sanitize_mark(request: ActionConnectorRequest) -> ActionConnectorResult:
    payload = request.input_json
    record_id = _parse_record_ref(payload["evidence_ref"])
    repo = ResourceRepository(_session(request))
    existing = repo.get_record(record_id)
    if existing.project_id != request.project_id or existing.resource_key != "evidence-item":
        raise NotFoundError(f"branding evidence record {payload['evidence_ref']!r} not found")
    data = dict(existing.data_json)
    data["sanitization_status"] = payload["verdict"]
    data["sanitization_verdict"] = {
        "verdict": payload["verdict"],
        "reviewer": payload["reviewer"],
        "reason": payload["reason"],
        "decision_ref": payload["decision_ref"],
        "decided_at": payload.get("decided_at") or _now_iso(),
    }
    updated = repo.upsert_record(
        project_id=request.project_id,
        plugin_slug="branding",
        resource_key="evidence-item",
        record_id=record_id,
        external_id=existing.external_id,
        title=existing.title,
        data_json=data,
        provenance_json={
            **(existing.provenance_json or {}),
            "last_sanitization_action_ref": request.action_ref,
            "last_sanitization_operation": request.operation,
            "last_sanitization_decision_ref": payload["decision_ref"],
        },
    ).data
    return ActionConnectorResult(
        output_json={
            "evidence_ref": _record_ref(updated.id),
            "resource_record_id": updated.id,
            "verdict": payload["verdict"],
            "decision_ref": payload["decision_ref"],
        },
        metadata_json={
            "resource_record_id": updated.id,
            "resource_key": "evidence-item",
            "decision_ref": payload["decision_ref"],
        },
    )


def _source_external_id(source: dict[str, Any]) -> str:
    return f"{source['system']}:{source['ref']}"


def _session(request: ActionConnectorRequest) -> Session:
    if not isinstance(request.session, Session):
        raise ValidationError("branding connector requires a database session")
    return request.session


def _record_ref(record_id: int) -> str:
    return f"resource-record:{record_id}"


def _parse_record_ref(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        raw = value.removeprefix("resource-record:")
        if raw.isdigit():
            return int(raw)
    raise ValidationError(
        "evidence_ref must be a resource-record ref",
        data={"evidence_ref": value},
    )


def _now_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()


__all__ = ["BrandingActionConnector"]
