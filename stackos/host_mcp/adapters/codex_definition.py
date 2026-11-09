"""Codex project TOML fields; preserving edits never confer execution authority."""

from __future__ import annotations

from typing import Any

import tomlkit
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from tomlkit.toml_document import TOMLDocument


class AgentFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr = Field(min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9_][a-zA-Z0-9_-]*$")
    description: StrictStr = Field(min_length=1, max_length=10000)
    developer_instructions: StrictStr = Field(min_length=1, max_length=1000000)
    model: StrictStr | None = Field(default=None, min_length=1, max_length=200)
    model_reasoning_effort: StrictStr | None = None
    model_reasoning_summary: StrictStr | None = None
    model_verbosity: StrictStr | None = None
    features: dict[str, Any] | None = None
    skills: dict[str, Any] | None = None


class AgentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr | None = None
    description: StrictStr | None = None
    developer_instructions: StrictStr | None = None
    model: StrictStr | None = None
    model_reasoning_effort: StrictStr | None = None
    model_reasoning_summary: StrictStr | None = None
    model_verbosity: StrictStr | None = None
    features: dict[str, Any] | None = None
    skills: dict[str, Any] | None = None


OPTIONAL_SETTINGS = tuple(
    k
    for k in AgentFields.model_fields
    if k not in {"name", "description", "developer_instructions"}
)
_ENUMS = {
    "model_reasoning_effort": {"none", "minimal", "low", "medium", "high", "xhigh"},
    "model_reasoning_summary": {"auto", "concise", "detailed", "none"},
    "model_verbosity": {"low", "medium", "high"},
}
_FEATURES = {"shell_tool", "apps", "plugins", "memories", "request_permissions_tool"}


def unsupported_settings(fields: AgentFields, raw: dict[str, Any]) -> list[str]:
    # Distinguish literal dotted root keys from supported table leaves.
    unsupported = [
        repr(key) if "." in key else key for key in raw if key not in AgentFields.model_fields
    ]
    for key, allowed in _ENUMS.items():
        value = getattr(fields, key)
        if value is not None and value not in allowed:
            unsupported.append(key)
    for key, value in (fields.features or {}).items():
        if key not in _FEATURES or value is not False:
            unsupported.append(f"features.{key}")
    for key, value in (fields.skills or {}).items():
        if key == "config" and isinstance(value, list):
            # Native skill entries can disable an identified skill; no enable grants.
            if all(
                isinstance(item, dict)
                and set(item) == {"path", "enabled"}
                and isinstance(item["path"], str)
                and bool(item["path"].strip())
                and item["enabled"] is False
                for item in value
            ):
                continue
        elif (key == "bundled" and isinstance(value, dict) and value == {"enabled": False}) or (
            key == "include_instructions" and value is False
        ):
            continue
        unsupported.append(f"skills.{key}")
    return sorted(unsupported)


def parse_definition(
    content: bytes, *, legacy_name: str | None = None, legacy_description: str | None = None
) -> tuple[TOMLDocument, AgentFields, list[str]]:
    document = tomlkit.parse(content.decode("utf-8"))
    values = document.unwrap()
    editable = {k: v for k, v in values.items() if k in AgentFields.model_fields}
    if "name" not in editable and legacy_name:
        editable["name"] = legacy_name
    if "description" not in editable and legacy_description:
        editable["description"] = legacy_description
    fields = AgentFields.model_validate(editable)
    if not fields.description.strip() or not fields.developer_instructions.strip():
        raise ValueError("empty required fields")
    return document, fields, unsupported_settings(fields, values)


def patch_definition(document: TOMLDocument, patch: dict[str, Any]) -> bytes:
    for key, value in patch.items():
        if value is None:
            if key in document:
                del document[key]
        elif isinstance(value, dict) and isinstance(document.get(key), dict):
            # Nested patches preserve unknown siblings and comments, too.
            _patch_table(document[key], value)
        else:
            document[key] = value
    return tomlkit.dumps(document).encode("utf-8")


def _patch_table(table: Any, patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if value is None:
            if key in table:
                del table[key]
        elif isinstance(value, dict) and isinstance(table.get(key), dict):
            _patch_table(table[key], value)
        else:
            table[key] = value
