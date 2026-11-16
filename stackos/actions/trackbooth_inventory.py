"""Generated Trackbooth action inventory persistence and lifecycle."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlmodel import col, select

from stackos.actions.connectors import ActionConnectorRequest, ActionValidationIssue
from stackos.actions.generated_inventory import (
    GeneratedInventoryIdentity,
    retire_superseded_generated_inventory_scopes,
    upsert_generated_actions,
)
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
from stackos.db.models import Action
from stackos.generated_inventory import generated_inventory_scope_key
from stackos.repositories.base import ValidationError

_TRACKBOOTH_INVENTORY = GeneratedInventoryIdentity(
    plugin_slug=_TRACKBOOTH_PLUGIN_SLUG,
    provider_key=_TRACKBOOTH_PROVIDER_KEY,
    inventory_source=_RUNTIME_INVENTORY_SOURCE,
    action_key_prefix=_RUNTIME_ACTION_KEY_PREFIX,
)


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
    return generated_inventory_scope_key(
        {field: scope.get(field) for field in _TRACKBOOTH_INVENTORY.scope_fields}
    )


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


def retire_superseded_trackbooth_inventory_scopes(
    *,
    session: Any,
    plugin_id: int,
    now: datetime,
    keep_logical_scope: tuple[int, str, str] | None = None,
    keep_scope_key: str | None = None,
) -> int:
    """Retain the public Trackbooth cleanup entry point and legacy scope identity."""
    return retire_superseded_generated_inventory_scopes(
        session=session,
        identity=_TRACKBOOTH_INVENTORY,
        plugin_id=plugin_id,
        now=now,
        keep_logical_scope=keep_logical_scope,
        keep_scope_key=keep_scope_key,
    )


def _upsert_runtime_actions(
    *,
    session: Any,
    request: ActionConnectorRequest,
    details: Sequence[Mapping[str, Any]],
    base_url: str,
    catalog_hash: str | None,
    prune_missing: bool,
) -> JsonObject:
    now = _utcnow()
    scope = _runtime_inventory_scope(request=request, base_url=base_url)
    scope_key = _runtime_inventory_scope_key(scope)
    planned_actions: list[tuple[str, JsonObject]] = []
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
        planned_actions.append(
            (
                operation_id,
                _runtime_action_manifest(
                    action_key=action_key,
                    public_action_key=public_action_key,
                    detail=detail,
                    base_url=base_url,
                    inventory_scope=scope,
                    inventory_scope_key=scope_key,
                    catalog_hash=catalog_hash,
                    synced_at=now,
                ),
            )
        )
    result = upsert_generated_actions(
        session=session,
        identity=_TRACKBOOTH_INVENTORY,
        scope=scope,
        actions=planned_actions,
        catalog_hash=catalog_hash,
        prune_missing=prune_missing,
        now=now,
    )
    session.commit()
    return {**result, "blocked_operation_ids": blocked_operation_ids}


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
