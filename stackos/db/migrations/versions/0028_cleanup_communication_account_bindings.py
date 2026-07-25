"""Clean legacy communication bindings and preserve Account audit continuity.

Revision ID: 0028_cleanup_communication_account_bindings
Revises: 0027_repair_global_account_backings
Create Date: 2026-07-25

"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0028_cleanup_communication_account_bindings"
down_revision: str | None = "0027_repair_global_account_backings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DERIVED_INGRESS_FIELDS = {
    "ingress_path",
    "ingress_url",
    "ingress_public_base_url",
    "ingress_driver",
    "ingress_endpoint_ref",
    "manual_ingress_confirmation",
    "webhook_base_url",
    "allowed_webhook_hosts",
    "webhook_policy",
}


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


def _contains_profile_reference(value: Any, *, profile_key: str, profile_ref: str) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"profile_ref", "profile_key", "bot_profile_key"} and str(item) in {
                profile_key,
                profile_ref,
            }:
                return True
            if _contains_profile_reference(item, profile_key=profile_key, profile_ref=profile_ref):
                return True
    elif isinstance(value, list):
        return any(
            _contains_profile_reference(item, profile_key=profile_key, profile_ref=profile_ref)
            for item in value
        )
    return False


def _contains_any_reference(value: Any, references: set[str]) -> bool:
    if isinstance(value, dict):
        return any(
            (isinstance(item, str) and item in references)
            or _contains_any_reference(item, references)
            for item in value.values()
        )
    if isinstance(value, list):
        return any(
            (isinstance(item, str) and item in references)
            or _contains_any_reference(item, references)
            for item in value
        )
    return False


def _strip_legacy_aliases(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {
            key: _strip_legacy_aliases(item)
            for key, item in value.items()
            if key not in {"auth_profile_key", "bot_profile_key"}
        }
        if not cleaned.get("profile_ref"):
            profile_key = value.get("profile_key") or value.get("bot_profile_key")
            if isinstance(profile_key, str) and profile_key.strip():
                cleaned["profile_ref"] = f"communication-profile:{profile_key.strip()}"
        return cleaned
    if isinstance(value, list):
        return [_strip_legacy_aliases(item) for item in value]
    return value


def _attached_account_ref(
    bind: Any,
    *,
    project_id: int,
    provider_key: str,
    facet: dict[str, Any],
) -> str | None:
    expected_account_id = (
        facet.get("bot_id")
        if provider_key == "telegram-bot"
        else facet.get("team_id")
        if provider_key == "slack-bot"
        else None
    )
    if not isinstance(expected_account_id, str) or not expected_account_id.strip():
        return None
    rows = bind.execute(
        sa.text(
            """
            SELECT c.credential_ref, c.config_json,
                   ca.provider_account_id, ca.metadata_json
            FROM credentials AS c
            JOIN project_credentials AS pc ON pc.credential_id = c.id
            LEFT JOIN credential_accounts AS ca ON ca.credential_id = c.id
            WHERE pc.project_id = :project_id
              AND c.provider_key = :provider_key
              AND c.status = 'connected'
              AND c.revoked_at IS NULL
              AND c.integration_credential_id IS NOT NULL
            ORDER BY c.id
            """
        ),
        {"project_id": project_id, "provider_key": provider_key},
    ).all()
    matches: list[str] = []
    for row in rows:
        config = _json_object(row[1])
        metadata = _json_object(row[3])
        identifiers = {
            str(value).strip()
            for value in (
                config.get("provider_account_id"),
                row[2],
                metadata.get("provider_account_id"),
                metadata.get("team_id"),
                metadata.get("bot_id"),
            )
            if isinstance(value, str) and value.strip()
        }
        if expected_account_id.strip() in identifiers:
            matches.append(str(row[0]))
    return matches[0] if len(matches) == 1 else None


def _communication_resources(bind: Any) -> list[dict[str, Any]]:
    return [
        dict(_mapping(row))
        for row in bind.execute(
            sa.text(
                """
                SELECT rr.id, rr.project_id, rr.external_id, rr.data_json, r.key AS resource_key
                FROM resource_records AS rr
                JOIN resources AS r ON r.id = rr.resource_id
                JOIN plugins AS p ON p.id = r.plugin_id
                WHERE p.slug = 'communications'
                ORDER BY rr.id
                """
            )
        ).all()
    ]


def _cleanup_communication_records(bind: Any) -> None:
    rows = _communication_resources(bind)
    purge_record_ids: set[int] = set()
    test_profiles: list[tuple[int, str, str]] = []
    for row in rows:
        if row["resource_key"] != "communication-profile":
            continue
        data = _json_object(row["data_json"])
        metadata = _json_object(data.get("metadata_json"))
        if metadata.get("created_for") == "slack-ping-ingress-test":
            profile_key = str(data.get("key") or "")
            profile_ref = str(data.get("profile_ref") or row["external_id"])
            test_profiles.append((int(row["project_id"]), profile_key, profile_ref))

    for row in rows:
        data = _json_object(row["data_json"])
        for project_id, profile_key, profile_ref in test_profiles:
            if int(row["project_id"]) == project_id and (
                row["external_id"] == profile_ref
                or _contains_profile_reference(
                    data,
                    profile_key=profile_key,
                    profile_ref=profile_ref,
                )
            ):
                purge_record_ids.add(int(row["id"]))
                break

    while True:
        references = {
            str(row["external_id"])
            for row in rows
            if int(row["id"]) in purge_record_ids and row["external_id"]
        }
        dependent_ids = {
            int(row["id"])
            for row in rows
            if int(row["id"]) not in purge_record_ids
            and _contains_any_reference(_json_object(row["data_json"]), references)
        }
        if not dependent_ids:
            break
        purge_record_ids.update(dependent_ids)

    if purge_record_ids:
        active_request_count = int(
            bind.execute(
                sa.text(
                    """
                    SELECT COUNT(*)
                    FROM agent_requests
                    WHERE source_resource_record_id IN :record_ids
                      AND status NOT IN ('responded', 'resolved', 'ignored', 'failed')
                    """
                ).bindparams(sa.bindparam("record_ids", expanding=True)),
                {"record_ids": sorted(purge_record_ids)},
            ).scalar_one()
        )
        if active_request_count:
            raise RuntimeError(
                "cannot remove the Slack ingress test island while it has active agent requests; "
                "resolve or release those requests before upgrading"
            )
        bind.execute(
            sa.text(
                """
                DELETE FROM agent_requests
                WHERE source_resource_record_id IN :record_ids
                  AND status IN ('responded', 'resolved', 'ignored', 'failed')
                """
            ).bindparams(sa.bindparam("record_ids", expanding=True)),
            {"record_ids": sorted(purge_record_ids)},
        )
        bind.execute(
            sa.text("DELETE FROM resource_records WHERE id IN :record_ids").bindparams(
                sa.bindparam("record_ids", expanding=True)
            ),
            {"record_ids": sorted(purge_record_ids)},
        )

    for row in _communication_resources(bind):
        data = _strip_legacy_aliases(_json_object(row["data_json"]))
        if row["resource_key"] == "communication-profile":
            facets = data.get("provider_facets")
            if isinstance(facets, dict):
                normalized_facets: dict[str, Any] = {}
                for provider_key, raw_facet in facets.items():
                    if not isinstance(raw_facet, dict):
                        continue
                    facet = dict(raw_facet)
                    if provider_key in {"slack-bot", "telegram-bot"}:
                        facet["ingress_enabled"] = bool(facet.get("ingress_enabled", True))
                    if not str(facet.get("credential_ref") or "").strip():
                        account_ref = _attached_account_ref(
                            bind,
                            project_id=int(row["project_id"]),
                            provider_key=str(provider_key),
                            facet=facet,
                        )
                        if account_ref:
                            facet["credential_ref"] = account_ref
                    for field in _DERIVED_INGRESS_FIELDS:
                        facet.pop(field, None)
                    refs = _json_object(facet.get("refs"))
                    refs.pop("ingress_url", None)
                    refs.pop("ingress_endpoint_ref", None)
                    if refs:
                        facet["refs"] = refs
                    else:
                        facet.pop("refs", None)
                    normalized_facets[str(provider_key)] = facet
                data["provider_facets"] = normalized_facets
        bind.execute(
            sa.text("UPDATE resource_records SET data_json = :data_json WHERE id = :record_id"),
            {
                "record_id": int(row["id"]),
                "data_json": json.dumps(data, separators=(",", ":")),
            },
        )

    obsolete = bind.execute(
        sa.text(
            """
            SELECT r.id
            FROM resources AS r
            JOIN plugins AS p ON p.id = r.plugin_id
            WHERE p.slug = 'communications'
              AND r.key = 'communication-bot-profile'
            """
        )
    ).all()
    for row in obsolete:
        bind.execute(
            sa.text("DELETE FROM resource_records WHERE resource_id = :resource_id"),
            {"resource_id": int(row[0])},
        )
        bind.execute(
            sa.text("DELETE FROM resources WHERE id = :resource_id"),
            {"resource_id": int(row[0])},
        )

    endpoints = bind.execute(
        sa.text(
            """
            SELECT rr.id, rr.data_json
            FROM resource_records AS rr
            JOIN resources AS r ON r.id = rr.resource_id
            JOIN plugins AS p ON p.id = r.plugin_id
            WHERE p.slug = 'communications'
              AND r.key = 'ingress-endpoint'
            """
        )
    ).all()
    for row in endpoints:
        data = _json_object(row[1])
        if data.get("driver") != "local-tunnel":
            continue
        data["status"] = "configured"
        data["public_base_url"] = None
        data["last_refreshed_at"] = None
        data["last_synced_at"] = None
        metadata = _json_object(data.get("metadata_json"))
        metadata.pop("last_refresh", None)
        metadata.pop("last_sync", None)
        data["metadata_json"] = metadata
        bind.execute(
            sa.text("UPDATE resource_records SET data_json = :data_json WHERE id = :record_id"),
            {
                "record_id": int(row[0]),
                "data_json": json.dumps(data, separators=(",", ":")),
            },
        )


def _repair_action_call_account_links(bind: Any) -> None:
    bind.execute(
        sa.text(
            """
            UPDATE action_calls
            SET credential_id = (
                SELECT c.id
                FROM credentials AS c
                WHERE c.credential_ref = action_calls.credential_ref
                  AND (
                    action_calls.provider_key IS NULL
                    OR action_calls.provider_key = c.provider_key
                  )
            )
            WHERE credential_id IS NULL
              AND credential_ref IS NOT NULL
              AND trim(credential_ref) <> ''
              AND EXISTS (
                SELECT 1
                FROM credentials AS c
                WHERE c.credential_ref = action_calls.credential_ref
                  AND (
                    action_calls.provider_key IS NULL
                    OR action_calls.provider_key = c.provider_key
                  )
              )
            """
        )
    )
    rows = bind.execute(
        sa.text(
            """
            SELECT id, metadata_json
            FROM action_calls
            WHERE credential_id IS NULL
              AND credential_ref IS NOT NULL
              AND trim(credential_ref) <> ''
            """
        )
    ).all()
    for row in rows:
        metadata = _json_object(row[1])
        metadata["credential_identity_status"] = "removed-before-account-tombstone-retention"
        metadata["credential_identity_note"] = (
            "The historical Account identity was removed by an earlier migration; "
            "the redacted credential_ref is retained without inventing replacement identity."
        )
        bind.execute(
            sa.text("UPDATE action_calls SET metadata_json = :metadata_json WHERE id = :call_id"),
            {
                "call_id": int(row[0]),
                "metadata_json": json.dumps(metadata, separators=(",", ":")),
            },
        )


def upgrade() -> None:
    bind = op.get_bind()
    _cleanup_communication_records(bind)
    _repair_action_call_account_links(bind)


def downgrade() -> None:
    """The cleanup removes unsafe aliases and stale derived data by design."""
