"""Input, output, and response-policy contracts for browser operations."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stackos.browser.runtime import BROWSER_PROVIDER
from stackos.mcp.contract import MCPInput
from stackos.operations.spec import OperationResponsePolicy

BROWSER_SIDE_EFFECT_POLICY = OperationResponsePolicy(
    default_mode="raw",
    allowed_modes=("raw",),
    ack_safe=False,
    raw_only_reason=(
        "Browser operations are live external side effects. Return the full redacted receipt, "
        "session refs, artifact refs, result value, and retry context."
    ),
)


class BrowserRuntimeStatusInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1}},
    )

    project_id: int | None = None


class BrowserProfileCreateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "profile_key": "personal-brand",
                "name": "Personal Brand Browser",
            }
        },
    )

    project_id: int
    profile_key: str
    name: str | None = None
    allowed_origins_json: list[str] | None = None
    launch_options_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None


class BrowserProfileListInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int


class BrowserSessionStartInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "profile_key": "personal-brand",
                "session_key": "linkedin",
                "headless": False,
            }
        },
    )

    project_id: int
    profile_key: str = "default"
    profile_ref: str | None = None
    session_key: str = "default"
    name: str | None = None
    headless: bool = False
    launch_options_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None


class BrowserSessionRefInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "session_ref": "browser-session:project-1:default:default",
            }
        },
    )

    project_id: int
    session_ref: str


class BrowserPageSnapshotInput(BrowserSessionRefInput):
    page_ref: str | None = None


class BrowserSessionListInput(MCPInput):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"example": {"project_id": 1}})

    project_id: int


class BrowserPageCallInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "session_ref": "browser-session:project-1:default:default",
                "method": "goto",
                "arguments": {"url": "https://example.com"},
            }
        },
    )

    project_id: int
    session_ref: str
    page_ref: str | None = None
    method: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    args: list[Any] | None = None
    kwargs: dict[str, Any] | None = None


class BrowserContextCallInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "session_ref": "browser-session:project-1:default:default",
                "method": "cookies",
                "arguments": {},
            }
        },
    )

    project_id: int
    session_ref: str
    method: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    args: list[Any] | None = None
    kwargs: dict[str, Any] | None = None


class BrowserHandleCallInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "session_ref": "browser-session:project-1:default:default",
                "handle_ref": "browser-session:project-1:default:default:handle-1",
                "method": "click",
            }
        },
    )

    project_id: int
    session_ref: str
    handle_ref: str
    method: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    args: list[Any] | None = None
    kwargs: dict[str, Any] | None = None


class BrowserScriptRunInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "session_ref": "browser-session:project-1:default:default",
                "script": "() => document.title",
            }
        },
    )

    project_id: int
    session_ref: str
    page_ref: str | None = None
    script: str
    arg: Any | None = None


class BrowserScriptInjectInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "session_ref": "browser-session:project-1:default:default",
                "script": "window.__stackosInjected = true;",
            }
        },
    )

    project_id: int
    session_ref: str
    page_ref: str | None = None
    script: str


class BrowserScreenshotInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "session_ref": "browser-session:project-1:default:default",
                "full_page": True,
            }
        },
    )

    project_id: int
    session_ref: str
    page_ref: str | None = None
    full_page: bool = True
    name: str | None = None


class BrowserMethodManifestOut(BaseModel):
    provider: str = BROWSER_PROVIDER
    parity_model: str = "full-control-public-api"
    methods: list[dict[str, Any]]
    notes: list[str]


__all__ = [
    "BROWSER_SIDE_EFFECT_POLICY",
    "BrowserContextCallInput",
    "BrowserHandleCallInput",
    "BrowserMethodManifestOut",
    "BrowserPageCallInput",
    "BrowserPageSnapshotInput",
    "BrowserProfileCreateInput",
    "BrowserProfileListInput",
    "BrowserRuntimeStatusInput",
    "BrowserScreenshotInput",
    "BrowserScriptInjectInput",
    "BrowserScriptRunInput",
    "BrowserSessionListInput",
    "BrowserSessionRefInput",
    "BrowserSessionStartInput",
]
