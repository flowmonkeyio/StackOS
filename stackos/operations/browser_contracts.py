"""Session lifecycle contracts for the native gstack browser."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict

from stackos.mcp.contract import MCPInput


class BrowserRuntimeStatusInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})
    project_id: int | None = None


class BrowserProfileCreateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "profile_key": "personal-brand"}},
    )
    project_id: int
    profile_key: str
    name: str | None = None
    metadata_json: dict[str, Any] | None = None


class BrowserProfileListInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})
    project_id: int


class BrowserSessionStartInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"project_id": 1, "profile_key": "personal-brand", "session_key": "main"}
        },
    )
    project_id: int
    profile_key: str = "default"
    profile_ref: str | None = None
    session_key: str = "default"
    name: str | None = None
    metadata_json: dict[str, Any] | None = None


class BrowserSessionRefInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"project_id": 1, "session_ref": "browser-session:project-1:default:main"}
        },
    )
    project_id: int
    session_ref: str


class BrowserSessionListInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})
    project_id: int
