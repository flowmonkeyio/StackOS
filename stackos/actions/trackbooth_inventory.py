"""Generated Trackbooth action inventory persistence and lifecycle."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from sqlmodel import col, select

from stackos.actions.connectors import ActionConnectorRequest, ActionValidationIssue
from stackos.actions.provider_utils import issue
from stackos.actions.trackbooth_assets import _is_blocked_endpoint
from stackos.actions.trackbooth_contract import (
    RUNTIME_ACTION_KEY_PREFIX as _RUNTIME_ACTION_KEY_PREFIX,
)
from stackos.actions.trackbooth_contract import (
    RUNTIME_INVENTORY_SOURCE as _RUNTIME_INVENTORY_SOURCE,
)
from stackos.actions.trackbooth_contract import (
    TRACKBOOTH_PLUGIN_SLUG as _TRACKBOOTH_PLUGIN_SLUG,
)
from stackos.actions.trackbooth_contract import (
    TRACKBOOTH_PROVIDER_KEY as _TRACKBOOTH_PROVIDER_KEY,
)
from stackos.actions.trackbooth_contract import (
    JsonObject,
)
from stackos.actions.trackbooth_schema import (
    _operation_action_slug,
    _runtime_action_manifest,
)
from stackos.db.models import Action, ActionVersion, Plugin, Provider
from stackos.repositories.base import ValidationError


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _requested_operation_ids(payload: Mapping[str, Any]) -> set[str]:
    operation_ids = payload.get("operation_ids")
    if not isinstance(operation_ids, list):
        return set()
    return {item.strip() for item in operation_ids if isinstance(item, str) and item.strip()}


def _sync_limit(payload: Mapping[str, Any]) -> int | None:
    raw = payload.get("limit")
    if isinstance(raw, int) and not isinstance(raw, bool) and 1 <= raw <= 1000:
        return raw
    return None


def _select_sync_items(
    items: Sequence[Mapping[str, Any]],
    *,
    requested_ids: set[str],
    limit: int | None,
) -> list[JsonObject]:
    selected: list[JsonObject] = []
    for item in items:
        operation_id = str(item.get("operation_id") or "").strip()
        if not operation_id:
            continue
        if requested_ids and operation_id not in requested_ids:
            continue
        selected.append(dict(item))
        if limit is not None and len(selected) >= limit:
            break
    return selected


def _runtime_inventory_scope(
    *,
    request: ActionConnectorRequest,
    base_url: str,
) -> JsonObject:
    if request.credential is None:
        raise ValidationError("Trackbooth catalog sync requires a credential")
    return {
        "project_id": request.project_id,
        "credential_ref": request.credential.credential_ref,
        "api_base_url": base_url,
    }


def _runtime_inventory_scope_key(scope: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {
            "project_id": scope.get("project_id"),
            "credential_ref": scope.get("credential_ref"),
            "api_base_url": scope.get("api_base_url"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"inv_{_short_hash(payload, length=12)}"


def _runtime_action_key(
    *,
    scope_key: str,
    detail: Mapping[str, Any],
    suffix: str | None = None,
) -> str:
    prefix = f"{_RUNTIME_ACTION_KEY_PREFIX}{scope_key}."
    max_slug_length = max(1, 160 - len(prefix))
    slug = _operation_action_slug(detail)
    if suffix:
        suffix_text = f"_{suffix}"
        slug = f"{slug[: max(1, max_slug_length - len(suffix_text))]}{suffix_text}"
    else:
        slug = slug[:max_slug_length]
    return f"{prefix}{slug}"


def _runtime_public_action_key(
    *,
    detail: Mapping[str, Any],
    suffix: str | None = None,
) -> str:
    prefix = _RUNTIME_ACTION_KEY_PREFIX
    max_slug_length = max(1, 160 - len(prefix))
    slug = _operation_action_slug(detail)
    if suffix:
        suffix_text = f"_{suffix}"
        slug = f"{slug[: max(1, max_slug_length - len(suffix_text))]}{suffix_text}"
    else:
        slug = slug[:max_slug_length]
    return f"{prefix}{slug}"


def _short_hash(value: str, *, length: int = 10) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _retire_runtime_action(row: Action, *, now: datetime) -> None:
    config = dict(row.config_json or {})
    if config.get("inventory_source") != _RUNTIME_INVENTORY_SOURCE:
        return
    config.pop("connector", None)
    config["execution_mode"] = "deferred.retired"
    config["deferred_reason"] = (
        "Generated Trackbooth action was not returned by the latest catalog sync "
        "for this inventory scope."
    )
    config["inventory_state"] = "retired"
    config["inventory_retired_at"] = now.isoformat()
    row.config_json = config
    row.updated_at = now


def _runtime_action_logical_scope(config: Mapping[str, Any]) -> tuple[int, str, str] | None:
    if config.get("inventory_source") != _RUNTIME_INVENTORY_SOURCE:
        return None
    if config.get("inventory_state") != "active":
        return None
    project_id = config.get("inventory_project_id")
    credential_ref = config.get("inventory_credential_ref")
    api_base_url = config.get("inventory_api_base_url")
    if (
        not isinstance(project_id, int)
        or not isinstance(credential_ref, str)
        or not credential_ref
        or not isinstance(api_base_url, str)
        or not api_base_url
    ):
        return None
    return project_id, credential_ref, api_base_url


def _runtime_row_scope_key(row: Action) -> str | None:
    config = row.config_json if isinstance(row.config_json, Mapping) else {}
    scope_key = config.get("inventory_scope_key")
    return scope_key if isinstance(scope_key, str) and scope_key else None


def _runtime_row_sort_key(row: Action) -> tuple[str, str, int]:
    config = row.config_json if isinstance(row.config_json, Mapping) else {}
    synced_at = config.get("inventory_synced_at")
    synced_text = synced_at if isinstance(synced_at, str) else ""
    updated_text = row.updated_at.isoformat() if row.updated_at is not None else ""
    return synced_text, updated_text, int(row.id or 0)


def retire_superseded_trackbooth_inventory_scopes(
    *,
    session: Any,
    plugin_id: int,
    now: datetime,
    keep_logical_scope: tuple[int, str, str] | None = None,
    keep_scope_key: str | None = None,
) -> int:
    """Retire older generated scopes for the same logical Trackbooth inventory."""
    rows = session.exec(
        select(Action).where(
            col(Action.plugin_id) == plugin_id,
            col(Action.key).like(f"{_RUNTIME_ACTION_KEY_PREFIX}%"),
        )
    ).all()
    grouped: dict[tuple[int, str, str], list[Action]] = defaultdict(list)
    for row in rows:
        config = row.config_json if isinstance(row.config_json, Mapping) else {}
        logical_scope = _runtime_action_logical_scope(config)
        if logical_scope is None:
            continue
        grouped[logical_scope].append(row)

    retired = 0
    for logical_scope, scoped_rows in grouped.items():
        scope_keys = {
            scope_key
            for row in scoped_rows
            if (scope_key := _runtime_row_scope_key(row)) is not None
        }
        if keep_logical_scope == logical_scope and keep_scope_key:
            for row in scoped_rows:
                if _runtime_row_scope_key(row) == keep_scope_key:
                    continue
                _retire_runtime_action(row, now=now)
                session.add(row)
                retired += 1
            continue
        if len(scope_keys) <= 1:
            continue
        active_scope_key: str | None
        if keep_logical_scope == logical_scope and keep_scope_key in scope_keys:
            active_scope_key = keep_scope_key
        else:
            latest_row = max(scoped_rows, key=_runtime_row_sort_key)
            active_scope_key = _runtime_row_scope_key(latest_row)
        if active_scope_key is None:
            continue
        for row in scoped_rows:
            if _runtime_row_scope_key(row) == active_scope_key:
                continue
            _retire_runtime_action(row, now=now)
            session.add(row)
            retired += 1
    return retired


def _runtime_catalog_hash_unchanged(
    *,
    session: Any,
    plugin_id: int,
    logical_scope: tuple[int, str, str] | None,
    catalog_hash: str,
    action_keys: set[str],
) -> bool:
    if logical_scope is None or not action_keys:
        return False
    rows = session.exec(
        select(Action).where(
            col(Action.plugin_id) == plugin_id,
            col(Action.key).like(f"{_RUNTIME_ACTION_KEY_PREFIX}%"),
        )
    ).all()
    active_rows = [
        row
        for row in rows
        if _runtime_action_logical_scope(
            row.config_json if isinstance(row.config_json, Mapping) else {}
        )
        == logical_scope
    ]
    if {str(row.key) for row in active_rows} != action_keys:
        return False
    for row in active_rows:
        config = row.config_json if isinstance(row.config_json, Mapping) else {}
        if config.get("inventory_catalog_hash") != catalog_hash:
            return False
    return True


def _upsert_runtime_actions(
    *,
    session: Any,
    request: ActionConnectorRequest,
    details: Sequence[Mapping[str, Any]],
    base_url: str,
    catalog_hash: str | None,
    prune_missing: bool,
) -> JsonObject:
    plugin = session.exec(select(Plugin).where(col(Plugin.slug) == _TRACKBOOTH_PLUGIN_SLUG)).first()
    if plugin is None or plugin.id is None:
        raise ValidationError("Trackbooth plugin must be synced before catalog sync")
    provider = session.exec(
        select(Provider).where(
            col(Provider.plugin_id) == plugin.id,
            col(Provider.key) == _TRACKBOOTH_PROVIDER_KEY,
        )
    ).first()
    if provider is None or provider.id is None:
        raise ValidationError("Trackbooth provider must be synced before catalog sync")

    now = _utcnow()
    scope = _runtime_inventory_scope(
        request=request,
        base_url=base_url,
    )
    scope_key = _runtime_inventory_scope_key(scope)
    logical_scope = _runtime_action_logical_scope(
        {
            "inventory_source": _RUNTIME_INVENTORY_SOURCE,
            "inventory_state": "active",
            "inventory_project_id": scope["project_id"],
            "inventory_credential_ref": scope["credential_ref"],
            "inventory_api_base_url": scope["api_base_url"],
        }
    )
    write_started = perf_counter()
    created = 0
    updated = 0
    skipped = 0
    pruned = 0
    action_keys: set[str] = set()
    planned_actions: list[JsonObject] = []
    public_action_key_operations: dict[str, str] = {}
    blocked_operation_ids: list[str] = []
    for detail in details:
        operation_id = str(detail.get("operation_id") or "").strip()
        method = str(detail.get("method") or "").upper()
        path = str(detail.get("path") or "").strip()
        if not operation_id or not method or not path:
            continue
        if _is_blocked_endpoint(detail):
            blocked_operation_ids.append(operation_id)
            continue
        suffix: str | None = None
        public_action_key = _runtime_public_action_key(detail=detail)
        prior_operation_id = public_action_key_operations.get(public_action_key)
        if prior_operation_id is not None and prior_operation_id != operation_id:
            suffix = _short_hash(operation_id)
            public_action_key = _runtime_public_action_key(detail=detail, suffix=suffix)
        public_action_key_operations[public_action_key] = operation_id
        action_key = _runtime_action_key(scope_key=scope_key, detail=detail, suffix=suffix)
        action_keys.add(action_key)
        planned_actions.append(
            {
                "detail": detail,
                "operation_id": operation_id,
                "public_action_key": public_action_key,
                "action_key": action_key,
            }
        )

    if (
        prune_missing
        and catalog_hash
        and _runtime_catalog_hash_unchanged(
            session=session,
            plugin_id=plugin.id,
            logical_scope=logical_scope,
            catalog_hash=catalog_hash,
            action_keys=action_keys,
        )
    ):
        session.commit()
        return {
            "synced": len(planned_actions),
            "created": 0,
            "updated": 0,
            "skipped": len(planned_actions),
            "pruned": 0,
            "retired": 0,
            "action_refs": [
                f"{_TRACKBOOTH_PLUGIN_SLUG}.{item['public_action_key']}" for item in planned_actions
            ],
            "operation_ids": [str(item["operation_id"]) for item in planned_actions],
            "blocked_operation_ids": blocked_operation_ids,
            "inventory_scope_key": scope_key,
            "write_ms": int((perf_counter() - write_started) * 1000),
        }

    action_refs: list[str] = []
    operation_ids: list[str] = []
    for item in planned_actions:
        detail = item["detail"]
        operation_id = str(item["operation_id"])
        public_action_key = str(item["public_action_key"])
        action_key = str(item["action_key"])
        row = session.exec(
            select(Action).where(col(Action.plugin_id) == plugin.id, col(Action.key) == action_key)
        ).first()
        manifest_json = _runtime_action_manifest(
            action_key=action_key,
            public_action_key=public_action_key,
            detail=detail,
            base_url=base_url,
            inventory_scope=scope,
            inventory_scope_key=scope_key,
            catalog_hash=catalog_hash,
            synced_at=now,
        )
        manifest_json["config"]["inventory_manifest_hash"] = _runtime_manifest_hash(manifest_json)
        if row is None:
            row = Action(
                plugin_id=plugin.id,
                provider_id=provider.id,
                key=action_key,
                name=manifest_json["name"],
                description=manifest_json["description"],
                capability_key=manifest_json["capability"],
                risk_level=manifest_json["risk_level"],
                input_schema_json=manifest_json["input_schema"],
                output_schema_json=manifest_json["output_schema"],
                config_json=manifest_json["config"],
            )
            created += 1
        elif _runtime_action_row_unchanged(row, manifest_json):
            skipped += 1
            action_refs.append(f"{_TRACKBOOTH_PLUGIN_SLUG}.{public_action_key}")
            operation_ids.append(operation_id)
            continue
        else:
            row.provider_id = provider.id
            row.name = manifest_json["name"]
            row.description = manifest_json["description"]
            row.capability_key = manifest_json["capability"]
            row.risk_level = manifest_json["risk_level"]
            row.input_schema_json = manifest_json["input_schema"]
            row.output_schema_json = manifest_json["output_schema"]
            row.config_json = manifest_json["config"]
            row.updated_at = now
            updated += 1
        session.add(row)
        session.flush()
        if row.id is None:
            raise RuntimeError("expected persisted Trackbooth action id")
        version = session.exec(
            select(ActionVersion).where(
                col(ActionVersion.action_id) == row.id,
                col(ActionVersion.version) == "live-catalog",
            )
        ).first()
        if version is None:
            version = ActionVersion(
                action_id=row.id,
                version="live-catalog",
                manifest_json=manifest_json,
            )
        else:
            version.manifest_json = manifest_json
        session.add(version)
        action_refs.append(f"{_TRACKBOOTH_PLUGIN_SLUG}.{public_action_key}")
        operation_ids.append(operation_id)
    if prune_missing:
        stale_rows = session.exec(
            select(Action).where(
                col(Action.plugin_id) == plugin.id,
                col(Action.key).like(f"{_RUNTIME_ACTION_KEY_PREFIX}%"),
            )
        ).all()
        for row in stale_rows:
            config = row.config_json if isinstance(row.config_json, Mapping) else {}
            if _runtime_action_logical_scope(config) != logical_scope:
                continue
            if row.key in action_keys:
                continue
            _retire_runtime_action(row, now=now)
            session.add(row)
            pruned += 1
    pruned += retire_superseded_trackbooth_inventory_scopes(
        session=session,
        plugin_id=plugin.id,
        now=now,
        keep_logical_scope=logical_scope,
        keep_scope_key=scope_key,
    )
    session.commit()
    return {
        "synced": len(action_refs),
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "pruned": pruned,
        "retired": pruned,
        "action_refs": action_refs,
        "operation_ids": operation_ids,
        "blocked_operation_ids": blocked_operation_ids,
        "inventory_scope_key": scope_key,
        "write_ms": int((perf_counter() - write_started) * 1000),
    }


def _runtime_manifest_hash(manifest_json: Mapping[str, Any]) -> str:
    stable = json.loads(json.dumps(manifest_json, default=str))
    config = stable.get("config")
    if isinstance(config, dict):
        config.pop("inventory_synced_at", None)
        config.pop("inventory_manifest_hash", None)
    raw = json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _runtime_action_row_unchanged(row: Action, manifest_json: Mapping[str, Any]) -> bool:
    config = row.config_json if isinstance(row.config_json, Mapping) else {}
    if config.get("inventory_source") != _RUNTIME_INVENTORY_SOURCE:
        return False
    if config.get("inventory_state") != "active":
        return False
    manifest_config = manifest_json.get("config")
    if not isinstance(manifest_config, Mapping):
        return False
    manifest_checksum = manifest_config.get("inventory_endpoint_checksum")
    if isinstance(manifest_checksum, str) and manifest_checksum:
        return config.get("inventory_endpoint_checksum") == manifest_checksum
    manifest_hash = manifest_config.get("inventory_manifest_hash")
    return isinstance(manifest_hash, str) and config.get("inventory_manifest_hash") == manifest_hash


def _runtime_inventory_input_issues(
    request: ActionConnectorRequest,
) -> list[ActionValidationIssue]:
    config = request.config_json
    if config.get("inventory_source") != _RUNTIME_INVENTORY_SOURCE:
        return []
    if config.get("inventory_state") == "retired":
        return [
            issue(
                "$.action",
                "generated Trackbooth action is retired; rerun trackbooth.catalog.sync",
                "retired_action",
            )
        ]
    issues: list[ActionValidationIssue] = []
    expected_project_id = config.get("inventory_project_id")
    if (
        isinstance(expected_project_id, int)
        and request.project_id
        and expected_project_id != request.project_id
    ):
        issues.append(
            issue(
                "$.project_id",
                "generated Trackbooth action belongs to a different StackOS project",
                "scope_mismatch",
            )
        )
    return issues


def retire_removed_trackbooth_actions(
    *,
    session: Any,
    plugin_id: int,
    now: datetime,
) -> None:
    """Mark removed Trackbooth action rows as non-executable."""
    for action_key in ("rest.read", "rest.write"):
        row = session.exec(
            select(Action).where(col(Action.plugin_id) == plugin_id, col(Action.key) == action_key)
        ).first()
        if row is None:
            continue
        _remove_trackbooth_action_row(
            row,
            now=now,
            reason=(
                "Trackbooth operation execution is exposed through generated actions from "
                "trackbooth.catalog.sync."
            ),
        )
        session.add(row)
    for row in session.exec(
        select(Action).where(
            col(Action.plugin_id) == plugin_id,
            col(Action.key).like(f"{_RUNTIME_ACTION_KEY_PREFIX}ctx_%"),
        )
    ).all():
        _remove_trackbooth_action_row(
            row,
            now=now,
            reason=(
                "Generated Trackbooth action used a removed internal inventory namespace. "
                "Run trackbooth.catalog.sync to use stable public action refs."
            ),
        )
        session.add(row)
    retire_superseded_trackbooth_inventory_scopes(
        session=session,
        plugin_id=plugin_id,
        now=now,
    )


def _remove_trackbooth_action_row(row: Action, *, now: datetime, reason: str) -> None:
    config = dict(row.config_json or {})
    if config.get("action_removed") is True and config.get("execution_mode") == "deferred.removed":
        return
    config.pop("connector", None)
    config["execution_mode"] = "deferred.removed"
    config["deferred_reason"] = reason
    config["action_removed"] = True
    config["trackbooth_removed_action"] = True
    config["inventory_state"] = "retired"
    config["inventory_retired_at"] = now.isoformat()
    row.config_json = config
    row.updated_at = now
