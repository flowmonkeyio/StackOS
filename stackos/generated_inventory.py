"""Generic metadata helpers for generated action inventory rows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

_CURRENT_INVENTORY_SCOPE_PREFIX = "inv_"
_RETIRED_INTERNAL_SCOPE_PREFIX = "ctx_"
_GENERATED_SCOPE_PREFIXES = (
    _CURRENT_INVENTORY_SCOPE_PREFIX,
    _RETIRED_INTERNAL_SCOPE_PREFIX,
)


def generated_inventory_scope_key(scope: Mapping[str, Any]) -> str:
    """Hash the complete declared scope, preserving the original Trackbooth identity."""
    payload = json.dumps(dict(scope), sort_keys=True, separators=(",", ":"))
    return f"inv_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]}"


def generated_manifest_hash(manifest_json: Mapping[str, Any]) -> str:
    """Hash the complete local projection, excluding sync time and the hash itself."""
    stable = json.loads(json.dumps(manifest_json, default=str))
    config = stable.get("config")
    if isinstance(config, dict):
        config.pop("inventory_synced_at", None)
        config.pop("inventory_manifest_hash", None)
    raw = json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generated_action_public_key(config_json: Mapping[str, Any] | None) -> str | None:
    """Return an agent-facing stable action key from generated inventory metadata."""
    if not isinstance(config_json, Mapping):
        return None
    public_action_key = config_json.get("public_action_key")
    if not isinstance(public_action_key, str) or not public_action_key.strip():
        return None
    return public_action_key.strip()


def generated_action_audit_key(action_key: str | None) -> str | None:
    """Return the public-looking key for generated action audit rows.

    Historical audit rows can contain internal generated inventory keys such as
    ``api.ctx_<scope>.operation`` or ``api.inv_<scope>.operation``. Those scope
    ids are implementation details; public audit output should preserve the
    logical operation key while keeping the raw DB row unchanged for forensics.
    """
    if not isinstance(action_key, str) or not action_key:
        return action_key
    parts = action_key.split(".")
    if len(parts) >= 3 and parts[0] == "api" and _looks_like_generated_scope(parts[1]):
        return ".".join([parts[0], *parts[2:]])
    return action_key


def generated_action_public_audit_metadata(
    metadata_json: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Scrub internal generated inventory scope ids from public audit metadata."""
    if not isinstance(metadata_json, Mapping):
        return None
    return _scrub_generated_inventory_scopes(metadata_json)


def generated_action_visible_for_project(
    *,
    config_json: Mapping[str, Any] | None,
    project_id: int | None,
    action_key: str | None = None,
) -> bool:
    """Return whether a generated/removed action row belongs in project discovery."""
    if _looks_like_retired_generated_key(action_key):
        return False
    if not isinstance(config_json, Mapping):
        return True
    if config_json.get("action_removed") is True:
        return False
    if config_json.get("inventory_source") is None:
        return True
    if config_json.get("inventory_state") != "active":
        return False
    if generated_action_public_key(config_json) is None:
        return False
    scope_key = config_json.get("inventory_scope_key")
    if not isinstance(scope_key, str) or not scope_key.startswith(_CURRENT_INVENTORY_SCOPE_PREFIX):
        return False
    if project_id is None:
        return False
    inventory_project_id = config_json.get("inventory_project_id")
    return isinstance(inventory_project_id, int) and inventory_project_id == project_id


def _looks_like_retired_generated_key(action_key: str | None) -> bool:
    if not isinstance(action_key, str) or not action_key:
        return False
    return any(part.startswith(_RETIRED_INTERNAL_SCOPE_PREFIX) for part in action_key.split("."))


def _looks_like_generated_scope(value: str) -> bool:
    return any(value.startswith(prefix) for prefix in _GENERATED_SCOPE_PREFIXES)


def _scrub_generated_inventory_scopes(value: Any) -> Any:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, child in value.items():
            if (
                key == "inventory_scope_key"
                and isinstance(child, str)
                and _looks_like_generated_scope(child)
            ):
                out[key] = "[generated-inventory-scope]"
                continue
            out[str(key)] = _scrub_generated_inventory_scopes(child)
        return out
    if isinstance(value, list):
        return [_scrub_generated_inventory_scopes(item) for item in value]
    return value


__all__ = [
    "generated_action_audit_key",
    "generated_action_public_audit_metadata",
    "generated_action_public_key",
    "generated_action_visible_for_project",
    "generated_inventory_scope_key",
    "generated_manifest_hash",
]
