"""Shared Action/ActionVersion lifecycle for already projected provider inventories.

Providers own catalog validation, endpoint eligibility, stable public names and
manifest projection. This helper owns scoped persistence only; it uses the
caller's Session and never commits, opens a store, fetches a catalog or resolves
credentials. Complete sync retires unseen entries; partial sync preserves them.
Retirement retains action IDs, versions and audit history.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Any

from sqlmodel import Session, col, select

from stackos.db.models import Action, ActionVersion, Plugin, Provider
from stackos.generated_inventory import generated_inventory_scope_key, generated_manifest_hash
from stackos.repositories.base import ValidationError


@dataclass(frozen=True)
class GeneratedInventoryIdentity:
    """Declared owner and exact scope dimensions, not a provider execution framework."""

    plugin_slug: str
    provider_key: str
    inventory_source: str
    action_key_prefix: str = "api."
    scope_fields: tuple[str, ...] = ("project_id", "credential_ref", "api_base_url")

    def __post_init__(self) -> None:
        required = {"project_id", "credential_ref", "api_base_url"}
        if not required.issubset(self.scope_fields) or len(set(self.scope_fields)) != len(
            self.scope_fields
        ):
            raise ValidationError(
                "Generated inventory must declare unique project, credential and URL scope fields"
            )


def _logical_scope(
    config: Mapping[str, Any], identity: GeneratedInventoryIdentity
) -> tuple[Any, ...] | None:
    if (
        config.get("inventory_source") != identity.inventory_source
        or config.get("inventory_state") != "active"
    ):
        return None
    values = tuple(config.get(f"inventory_{field}") for field in identity.scope_fields)
    for field, value in zip(identity.scope_fields, values, strict=True):
        if field == "project_id":
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                return None
        elif not isinstance(value, str) or not value:
            return None
    return values


def _retire_action(row: Action, *, identity: GeneratedInventoryIdentity, now: datetime) -> None:
    config = dict(row.config_json or {})
    config.pop("connector", None)
    config["execution_mode"] = "deferred.retired"
    config["deferred_reason"] = (
        f"Generated {identity.provider_key.title()} action was not returned by the latest catalog "
        "sync for this inventory scope."
    )
    config["inventory_state"] = "retired"
    config["inventory_retired_at"] = now.isoformat()
    row.config_json = config
    row.updated_at = now


def _inventory_rows(
    session: Session, *, plugin_id: int, identity: GeneratedInventoryIdentity
) -> Sequence[Action]:
    return session.exec(
        select(Action)
        .join(Provider, col(Action.provider_id) == col(Provider.id))
        .join(Plugin, col(Action.plugin_id) == col(Plugin.id))
        .where(
            col(Action.plugin_id) == plugin_id,
            col(Plugin.slug) == identity.plugin_slug,
            col(Provider.plugin_id) == plugin_id,
            col(Provider.key) == identity.provider_key,
            col(Action.key).startswith(identity.action_key_prefix, autoescape=True),
        )
    ).all()


def _row_scope_key(row: Action) -> str | None:
    value = (row.config_json or {}).get("inventory_scope_key")
    return value if isinstance(value, str) and value else None


def _row_sort_key(row: Action) -> tuple[str, str, int]:
    synced_at = (row.config_json or {}).get("inventory_synced_at")
    return (
        synced_at if isinstance(synced_at, str) else "",
        row.updated_at.isoformat() if row.updated_at is not None else "",
        int(row.id or 0),
    )


def retire_superseded_generated_inventory_scopes(
    *,
    session: Session,
    identity: GeneratedInventoryIdentity,
    plugin_id: int,
    now: datetime,
    keep_logical_scope: tuple[Any, ...] | None = None,
    keep_scope_key: str | None = None,
) -> int:
    """Retire legacy scopes only within the same declared logical identity."""
    grouped: dict[tuple[Any, ...], list[Action]] = defaultdict(list)
    for row in _inventory_rows(session, plugin_id=plugin_id, identity=identity):
        logical_scope = _logical_scope(row.config_json or {}, identity)
        if logical_scope is not None:
            grouped[logical_scope].append(row)
    retired = 0
    for logical_scope, rows in grouped.items():
        scope_keys = {key for row in rows if (key := _row_scope_key(row)) is not None}
        active_scope_key: str | None
        if keep_logical_scope == logical_scope and keep_scope_key:
            active_scope_key = keep_scope_key
        elif len(scope_keys) > 1:
            active_scope_key = _row_scope_key(max(rows, key=_row_sort_key))
        else:
            continue
        if active_scope_key is None:
            continue
        for row in rows:
            if _row_scope_key(row) != active_scope_key:
                _retire_action(row, identity=identity, now=now)
                session.add(row)
                retired += 1
    return retired


def upsert_generated_actions(
    *,
    session: Session,
    identity: GeneratedInventoryIdentity,
    scope: Mapping[str, Any],
    actions: Sequence[tuple[str, Mapping[str, Any]]],
    catalog_hash: str | None,
    prune_missing: bool,
    now: datetime,
) -> dict[str, Any]:
    """Persist one validated projection; callers retain transaction ownership.

    Each pair is (provider operation ID, complete action manifest). Keys are
    provider-projected stable names. All identities/configuration are checked
    before the first write, including duplicate public/internal/operation IDs.
    The existing mutable ``live-catalog`` version is updated in place; captured
    versions and call/run snapshots remain untouched.
    """
    plugin = session.exec(select(Plugin).where(col(Plugin.slug) == identity.plugin_slug)).first()
    if plugin is None or plugin.id is None:
        raise ValidationError(
            f"{identity.provider_key.title()} plugin must be synced before catalog sync"
        )
    provider = session.exec(
        select(Provider).where(
            col(Provider.plugin_id) == plugin.id, col(Provider.key) == identity.provider_key
        )
    ).first()
    if provider is None or provider.id is None:
        raise ValidationError(
            f"{identity.provider_key.title()} provider must be synced before catalog sync"
        )
    if set(scope) != set(identity.scope_fields):
        raise ValidationError("Generated inventory scope must match its declared dimensions")
    scope_config = {
        "inventory_source": identity.inventory_source,
        "inventory_state": "active",
        **{f"inventory_{field}": value for field, value in scope.items()},
    }
    logical_scope = _logical_scope(scope_config, identity)
    if logical_scope is None:
        raise ValidationError("Generated inventory requires a complete valid scope")
    scope_key = generated_inventory_scope_key(scope)
    planned: list[tuple[str, dict[str, Any], Action | None]] = []
    seen: dict[str, set[str]] = {"operation": set(), "action": set(), "public action": set()}
    for operation_id, source_manifest in actions:
        manifest = json.loads(json.dumps(source_manifest))
        config = manifest.get("config")
        action_key = manifest.get("key")
        public_key = config.get("public_action_key") if isinstance(config, dict) else None
        for kind, value in (
            ("operation", operation_id),
            ("action", action_key),
            ("public action", public_key),
        ):
            if not isinstance(value, str) or not value.strip() or value in seen[kind]:
                raise ValidationError(f"Missing or duplicate generated {kind} identity")
            seen[kind].add(value)
        if (
            manifest.get("provider") != identity.provider_key
            or not isinstance(public_key, str)
            or not isinstance(action_key, str)
            or not public_key.startswith(identity.action_key_prefix)
            or not action_key.startswith(f"{identity.action_key_prefix}{scope_key}.")
        ):
            raise ValidationError("Generated action does not match its declared provider/scope")
        for field in (
            "name",
            "description",
            "capability",
            "risk_level",
            "input_schema",
            "output_schema",
        ):
            if field not in manifest:
                raise ValidationError(f"Generated action manifest is missing {field}")
        for field, value in scope_config.items():
            if field in config and config[field] != value:
                raise ValidationError(
                    "Generated action contains incompatible inventory scope metadata"
                )
        if config.get("inventory_scope_key", scope_key) != scope_key:
            raise ValidationError("Generated action contains an incompatible inventory scope key")
        config.update(scope_config)
        config.update(
            inventory_scope_key=scope_key,
            inventory_catalog_hash=catalog_hash,
            inventory_synced_at=now.isoformat(),
        )
        config["inventory_manifest_hash"] = generated_manifest_hash(manifest)
        row = session.exec(
            select(Action).where(col(Action.plugin_id) == plugin.id, col(Action.key) == action_key)
        ).first()
        if row is not None and (
            row.provider_id != provider.id
            or (row.config_json or {}).get("inventory_source") != identity.inventory_source
        ):
            raise ValidationError("Generated action key is owned by a different inventory")
        planned.append((operation_id, manifest, row))

    started = perf_counter()
    created = updated = skipped = pruned = 0
    action_refs: list[str] = []
    operation_ids: list[str] = []
    for operation_id, manifest, row in planned:
        config = manifest["config"]
        action_refs.append(f"{identity.plugin_slug}.{config['public_action_key']}")
        operation_ids.append(operation_id)
        if row is not None and (
            (row.config_json or {}).get("inventory_state") == "active"
            and (row.config_json or {}).get("inventory_manifest_hash")
            == config["inventory_manifest_hash"]
        ):
            skipped += 1
            continue
        if row is None:
            row = Action(
                plugin_id=plugin.id,
                provider_id=provider.id,
                key=manifest["key"],
                name=manifest["name"],
            )
            created += 1
        else:
            updated += 1
            row.updated_at = now
        row.name = manifest["name"]
        row.description = manifest["description"]
        row.capability_key = manifest["capability"]
        row.risk_level = manifest["risk_level"]
        row.input_schema_json = manifest["input_schema"]
        row.output_schema_json = manifest["output_schema"]
        row.config_json = config
        session.add(row)
        session.flush()
        if row.id is None:
            raise RuntimeError("expected persisted generated action id")
        version = session.exec(
            select(ActionVersion).where(
                col(ActionVersion.action_id) == row.id, col(ActionVersion.version) == "live-catalog"
            )
        ).first()
        if version is None:
            version = ActionVersion(
                action_id=row.id, version="live-catalog", manifest_json=manifest
            )
        else:
            version.manifest_json = manifest
        session.add(version)
    if prune_missing:
        for row in _inventory_rows(session, plugin_id=plugin.id, identity=identity):
            if (
                _logical_scope(row.config_json or {}, identity) == logical_scope
                and row.key not in seen["action"]
            ):
                _retire_action(row, identity=identity, now=now)
                session.add(row)
                pruned += 1
    pruned += retire_superseded_generated_inventory_scopes(
        session=session,
        identity=identity,
        plugin_id=plugin.id,
        now=now,
        keep_logical_scope=logical_scope,
        keep_scope_key=scope_key,
    )
    return {
        "synced": len(action_refs),
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "pruned": pruned,
        "retired": pruned,
        "action_refs": action_refs,
        "operation_ids": operation_ids,
        "inventory_scope_key": scope_key,
        "write_ms": int((perf_counter() - started) * 1000),
    }
