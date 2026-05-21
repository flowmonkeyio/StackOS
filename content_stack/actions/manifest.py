"""Executable action manifest parsing for StackOS actions."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from content_stack.artifacts import redact_secret_text
from content_stack.db.models import Action, Plugin, Provider
from content_stack.repositories.base import ValidationError

ACTION_MANIFEST_SCHEMA_VERSION = "stackos.action.v1"

_NO_SECRET_CONFIG_KEYS = frozenset(
    {
        "auth_ref",
        "credential_ref",
        "credential_policy",
        "requires_credential",
    }
)
_SECRET_KEY_PARTS = (
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
)


def _is_secret_config_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in _NO_SECRET_CONFIG_KEYS:
        return False
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def find_action_manifest_secret_paths(value: Any, *, path: str = "$") -> list[str]:
    """Return static action config paths that look like embedded secrets."""
    paths: list[str] = []
    if isinstance(value, dict):
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}"
            if _is_secret_config_key(key):
                paths.append(child_path)
            paths.extend(find_action_manifest_secret_paths(raw_value, path=child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            paths.extend(find_action_manifest_secret_paths(item, path=f"{path}[{index}]"))
    elif isinstance(value, str) and redact_secret_text(value) != value:
        paths.append(path)
    return paths


class ExecutableActionManifest(BaseModel):
    """Database-backed action manifest normalized for execution."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = ACTION_MANIFEST_SCHEMA_VERSION
    action_id: int
    action_key: str
    action_ref: str
    plugin_slug: str
    provider_key: str | None = None
    provider_auth_type: str | None = None
    name: str
    description: str = ""
    capability_key: str | None = None
    risk_level: str
    input_schema_json: dict[str, Any] = Field(default_factory=dict)
    output_schema_json: dict[str, Any] = Field(default_factory=dict)
    config_json: dict[str, Any] = Field(default_factory=dict)
    connector_key: str | None = None
    operation: str
    requires_credential: bool = False
    allows_credential: bool = False
    budget_kind: str | None = None
    enforce_budget: bool = False

    @field_validator("schema_version")
    @classmethod
    def _schema_version(cls, value: str) -> str:
        if value != ACTION_MANIFEST_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {ACTION_MANIFEST_SCHEMA_VERSION}")
        return value


def parse_action_manifest(
    *,
    action: Action,
    plugin: Plugin,
    provider: Provider | None,
) -> ExecutableActionManifest:
    """Parse catalog rows into an executable action manifest."""
    if action.id is None:
        raise ValidationError("action row must be persisted before execution")
    config = dict(action.config_json or {})
    secret_paths = find_action_manifest_secret_paths(config)
    if secret_paths:
        raise ValidationError(
            "action manifest config must not contain secrets",
            data={"paths": secret_paths[:8], "action_key": action.key, "plugin_slug": plugin.slug},
        )
    schema_version = str(config.get("schema_version") or ACTION_MANIFEST_SCHEMA_VERSION)
    connector_key = config.get("connector")
    if connector_key is not None and not isinstance(connector_key, str):
        raise ValidationError("action manifest connector must be a string")
    operation = config.get("operation") or action.key
    if not isinstance(operation, str) or not operation:
        raise ValidationError("action manifest operation must be a non-empty string")
    requires_credential = config.get("requires_credential")
    if requires_credential is None:
        requires_credential = (
            provider is not None and provider.auth_type not in {"none", "local"}
        )
    if not isinstance(requires_credential, bool):
        raise ValidationError("action manifest requires_credential must be a boolean")
    allows_credential = config.get("allows_credential")
    if allows_credential is None:
        allows_credential = requires_credential
    if not isinstance(allows_credential, bool):
        raise ValidationError("action manifest allows_credential must be a boolean")
    if requires_credential and not allows_credential:
        raise ValidationError("action manifest cannot require and disallow credentials")
    budget_kind = config.get("budget_kind")
    if budget_kind is not None and not isinstance(budget_kind, str):
        raise ValidationError("action manifest budget_kind must be a string")
    enforce_budget = config.get("enforce_budget", False)
    if not isinstance(enforce_budget, bool):
        raise ValidationError("action manifest enforce_budget must be a boolean")
    resolved_budget_kind = budget_kind if budget_kind is not None else (
        provider.key if provider else None
    )
    return ExecutableActionManifest(
        schema_version=schema_version,
        action_id=action.id,
        action_key=action.key,
        action_ref=f"{plugin.slug}.{action.key}",
        plugin_slug=plugin.slug,
        provider_key=provider.key if provider is not None else None,
        provider_auth_type=provider.auth_type if provider is not None else None,
        name=action.name,
        description=action.description,
        capability_key=action.capability_key,
        risk_level=action.risk_level,
        input_schema_json=action.input_schema_json,
        output_schema_json=action.output_schema_json,
        config_json=config,
        connector_key=connector_key,
        operation=operation,
        requires_credential=requires_credential,
        allows_credential=allows_credential,
        budget_kind=resolved_budget_kind,
        enforce_budget=enforce_budget,
    )


__all__ = [
    "ACTION_MANIFEST_SCHEMA_VERSION",
    "ExecutableActionManifest",
    "find_action_manifest_secret_paths",
    "parse_action_manifest",
]
