"""Session lifecycle and native CLI transport contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from stackos.mcp.contract import MCPInput
from stackos.operations.spec import OperationResponsePolicy

BROWSER_RAW_POLICY = OperationResponsePolicy(
    default_mode="raw",
    allowed_modes=("raw",),
    ack_safe=False,
    raw_only_reason="Preserve selected-session handoff and native command streams and exit status.",
)


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


class BrowserCliRunInput(BrowserSessionRefInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "session_ref": "browser-session:project-1:default:main",
                "argv": ["status"],
            }
        },
    )
    # Validation precedes the generic dispatcher and MCP idempotency cache.
    idempotency_key: None = Field(default=None, description="Native commands cannot be replayed.")
    expected_etag: None = Field(default=None, description="Native CLI execution has no ETag.")
    argv: list[str] = Field(description="Exact ordered upstream CLI arguments.")
    stdin: str | None = Field(default=None, description="Exact UTF-8 stdin; no newline is added.")


class BrowserCliRunOut(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    encoding: Literal["utf-8", "base64"]
