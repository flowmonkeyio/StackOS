"""Bind communication surfaces to their exact provider profile.

Revision ID: 0029_canonical_communication_surface_bindings
Revises: 0028_cleanup_communication_account_bindings
Create Date: 2026-09-22

"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from stackos.communication_surface_bindings import communication_surface_binding_external_id

revision: str = "0029_canonical_communication_surface_bindings"
down_revision: str | None = "0028_cleanup_communication_account_bindings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _mapping(row: Any) -> Mapping[str, Any]:
    return row._mapping if hasattr(row, "_mapping") else row


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def _communication_records(bind: Any) -> list[dict[str, Any]]:
    return [
        dict(_mapping(row))
        for row in bind.execute(
            sa.text(
                """
                SELECT rr.id, rr.project_id, rr.resource_id, rr.external_id, rr.data_json,
                       r.key AS resource_key
                FROM resource_records AS rr
                JOIN resources AS r ON r.id = rr.resource_id
                JOIN plugins AS p ON p.id = r.plugin_id
                WHERE p.slug = 'communications'
                ORDER BY rr.id
                """
            )
        ).all()
    ]


def _profile_candidates(
    profiles: list[dict[str, Any]],
    *,
    project_id: int,
    provider_key: str,
    profile_ref: str | None,
    profile_key: str | None,
    credential_ref: str | None,
) -> list[str]:
    wanted_ref = profile_ref.strip() if profile_ref else None
    if wanted_ref and not wanted_ref.startswith("communication-profile:"):
        wanted_ref = f"communication-profile:{wanted_ref}"
    if not wanted_ref and profile_key:
        wanted_ref = f"communication-profile:{profile_key.strip()}"

    candidates: list[str] = []
    for row in profiles:
        if int(row["project_id"]) != project_id:
            continue
        data = _json_object(row["data_json"])
        candidate_ref = str(data.get("profile_ref") or row["external_id"] or "").strip()
        if not candidate_ref or (wanted_ref is not None and candidate_ref != wanted_ref):
            continue
        facets = data.get("provider_facets")
        facet = facets.get(provider_key) if isinstance(facets, dict) else None
        if not isinstance(facet, dict):
            if provider_key != "local-agent-chat" or credential_ref:
                continue
            bound_credential_ref = ""
        else:
            bound_credential_ref = str(facet.get("credential_ref") or "").strip()
        if credential_ref and bound_credential_ref != credential_ref:
            continue
        candidates.append(candidate_ref)
    return candidates


def _update_data(bind: Any, *, record_id: int, data: dict[str, Any]) -> None:
    bind.execute(
        sa.text("UPDATE resource_records SET data_json = :data_json WHERE id = :record_id"),
        {"record_id": record_id, "data_json": json.dumps(data, separators=(",", ":"))},
    )


def _mark_repair_required(
    bind: Any,
    *,
    record_id: int,
    data: dict[str, Any],
    reason: str,
) -> None:
    data["surface_binding_state"] = "repair-required"
    data["surface_binding_issue"] = reason
    _update_data(bind, record_id=record_id, data=data)


def _meaningful_surface_value(field: str, value: Any) -> bool:
    if field == "audience":
        return isinstance(value, str) and value not in {"", "unknown"}
    return value not in (None, "", {}, [])


def _merge_surface_metadata(current: Any, incoming: Any) -> tuple[bool, Any]:
    if not _meaningful_surface_value("metadata_json", current):
        return True, incoming
    if not _meaningful_surface_value("metadata_json", incoming):
        return True, current
    if not isinstance(current, dict) or not isinstance(incoming, dict):
        return current == incoming, current
    merged = dict(current)
    for key, value in incoming.items():
        if key in merged and merged[key] != value:
            return False, current
        merged[key] = value
    return True, merged


def _merge_surface_data(
    canonical: dict[str, Any],
    legacy: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    """Preserve matching intent/configuration without choosing conflicting policy."""

    merged = dict(canonical)
    fields = (
        "kind",
        "display_name",
        "credential_ref",
        "safe_external_ref",
        "send_enabled",
        "ingest_enabled",
        "capabilities",
        "audience",
        "intent",
        "agent_guidance",
        "data_scope",
        "external_context",
    )
    for field in fields:
        current = merged.get(field)
        incoming = legacy.get(field)
        if not _meaningful_surface_value(field, current):
            if _meaningful_surface_value(field, incoming):
                merged[field] = incoming
            continue
        if _meaningful_surface_value(field, incoming) and current != incoming:
            return False, canonical
    metadata_ok, metadata = _merge_surface_metadata(
        merged.get("metadata_json"), legacy.get("metadata_json")
    )
    if not metadata_ok:
        return False, canonical
    if _meaningful_surface_value("metadata_json", metadata):
        merged["metadata_json"] = metadata
    return True, merged


def _migrate_surface_bindings(bind: Any) -> None:
    records = _communication_records(bind)
    profiles = [row for row in records if row["resource_key"] == "communication-profile"]
    channels = [row for row in records if row["resource_key"] == "communication-channel"]
    canonical_rows: dict[tuple[int, int, str], dict[str, Any]] = {}
    for row in channels:
        external_id = str(row["external_id"] or "")
        if not (
            external_id.startswith("communication-surface:")
            and len(external_id.rsplit(":", 1)[-1]) == 64
        ):
            continue
        canonical_rows[(int(row["project_id"]), int(row["resource_id"]), external_id)] = {
            "record_id": int(row["id"]),
            "data": _json_object(row["data_json"]),
        }

    for row in channels:
        current = canonical_rows.get(
            (int(row["project_id"]), int(row["resource_id"]), str(row["external_id"] or ""))
        )
        # An earlier legacy row may already have merged context into this
        # canonical row or marked a conflict. Never restore its stale snapshot.
        data = dict(current["data"]) if current else _json_object(row["data_json"])
        provider_key = str(data.get("provider_key") or "").strip()
        if provider_key.startswith("telegram"):
            continue
        surface_ref = str(data.get("surface_ref") or data.get("channel_ref") or "").strip()
        if not provider_key or not surface_ref:
            _mark_repair_required(
                bind,
                record_id=int(row["id"]),
                data=data,
                reason="missing_provider_or_surface_ref",
            )
            continue
        credential_ref = str(data.get("credential_ref") or "").strip() or None
        candidates = _profile_candidates(
            profiles,
            project_id=int(row["project_id"]),
            provider_key=provider_key,
            profile_ref=str(data.get("profile_ref") or "").strip() or None,
            profile_key=str(data.get("profile_key") or "").strip() or None,
            credential_ref=credential_ref,
        )
        if len(candidates) != 1:
            _mark_repair_required(
                bind,
                record_id=int(row["id"]),
                data=data,
                reason="ambiguous_profile_binding" if candidates else "missing_profile_binding",
            )
            continue

        profile_ref = candidates[0]
        external_id = communication_surface_binding_external_id(
            provider_key=provider_key,
            profile_ref=profile_ref,
            surface_ref=surface_ref,
        )
        data["profile_ref"] = profile_ref
        if data.get("surface_binding_issue") != "conflicting_duplicate_binding_metadata":
            data.pop("surface_binding_state", None)
            data.pop("surface_binding_issue", None)
        identity = (int(row["project_id"]), int(row["resource_id"]), external_id)
        existing = canonical_rows.get(identity)
        if existing is not None and existing["record_id"] != int(row["id"]):
            merged_ok, merged_data = _merge_surface_data(existing["data"], data)
            if not merged_ok:
                canonical_data = dict(existing["data"])
                canonical_data["surface_binding_state"] = "repair-required"
                canonical_data["surface_binding_issue"] = "conflicting_duplicate_binding_metadata"
                _update_data(
                    bind,
                    record_id=int(existing["record_id"]),
                    data=canonical_data,
                )
                _mark_repair_required(
                    bind,
                    record_id=int(row["id"]),
                    data=data,
                    reason="conflicting_duplicate_binding_metadata",
                )
                existing["data"] = canonical_data
                continue
            _update_data(
                bind,
                record_id=int(existing["record_id"]),
                data=merged_data,
            )
            data["surface_binding_state"] = "superseded"
            data["surface_binding_issue"] = "canonical_binding_exists"
            data["surface_binding_ref"] = external_id
            _update_data(bind, record_id=int(row["id"]), data=data)
            existing["data"] = merged_data
            continue

        bind.execute(
            sa.text(
                """
                UPDATE resource_records
                SET external_id = :external_id, data_json = :data_json
                WHERE id = :record_id
                """
            ),
            {
                "record_id": int(row["id"]),
                "external_id": external_id,
                "data_json": json.dumps(data, separators=(",", ":")),
            },
        )
        canonical_rows[identity] = {"record_id": int(row["id"]), "data": data}


def upgrade() -> None:
    _migrate_surface_bindings(op.get_bind())


def downgrade() -> None:
    """Canonical surface identity cannot safely be reduced to a bare surface ref."""
