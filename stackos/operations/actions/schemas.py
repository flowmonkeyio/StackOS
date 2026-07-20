"""Action operation input, output, and response-policy contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stackos.action_availability import ActionExposureOut
from stackos.db.models import ActionCallStatus
from stackos.mcp.contract import MCPInput
from stackos.operations.spec import OperationResponsePolicy

ACTION_FILE_OUTPUT_RESPONSE_POLICY = OperationResponsePolicy(
    default_mode="compact",
    allowed_modes=("compact", "raw"),
    ack_safe=False,
    compact_notes=(
        "Compact action responses must keep action_call_id, action_ref, provider, status, "
        "cost, file path, checksum, and warnings.",
        "MCP and REST calls store external provider output in plain response files by "
        "default. CLI calls default to raw inline responses unless an explicit output "
        "policy says otherwise.",
    ),
)

ACTION_CALL_POLL_RESPONSE_POLICY = OperationResponsePolicy(
    default_mode="compact",
    allowed_modes=("compact", "raw"),
    ack_safe=False,
    compact_notes=(
        "Compact polling responses keep action_call_id, status, progress, timestamps, "
        "terminal output or failure details, and next-poll guidance.",
    ),
)


class ActionListInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "plugin_slug": "utils",
                "query": "sitemap",
            }
        },
    )

    project_id: int | None = None
    plugin_slug: str | None = None
    provider_key: str | None = None
    capability_key: str | None = None
    query: str | None = None
    executable: bool | None = None
    include_unavailable_integrations: bool = Field(
        default=False,
        description=(
            "When false, normal agent discovery hides disconnected, deferred, project-local, "
            "missing-connector, and otherwise non-executable external-provider actions. "
            "Set true for setup, readiness debugging, or catalog inventory."
        ),
    )


class ActionListItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_ref: str
    plugin_slug: str
    key: str
    name: str
    description: str
    provider_key: str | None = None
    capability_key: str | None = None
    risk_level: str
    operation: str
    connector_key: str | None = None
    requires_credential: bool
    allows_credential: bool
    budget_kind: str | None = None
    executable: bool
    availability_status: str
    availability_reasons: list[str] = Field(default_factory=list)
    credential_state: str
    budget_state: str
    exposure: ActionExposureOut


class ActionListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ActionListItemOut]
    count: int
    hidden_count: int = 0
    filters: dict[str, Any] = Field(default_factory=dict)


class ActionDescribeInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "action_ref": "utils.image.generate"}},
    )

    project_id: int | None = None
    action_ref: str | None = None
    plugin_slug: str | None = None
    action_key: str | None = None


class ActionValidateInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "action_ref": "utils.image.generate",
                "input_json": {"prompt": "Product photo"},
                "context_ref": "ctx_provider_analysis",
            }
        },
    )

    project_id: int | None = None
    action_ref: str | None = None
    plugin_slug: str | None = None
    action_key: str | None = None
    input_json: dict[str, Any] | None = None
    context_ref: str | None = Field(
        default=None,
        description=(
            "Optional reusable execution context ref. StackOS resolves credential and "
            "provider_context_json defaults inside the daemon."
        ),
    )
    provider_context_json: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional provider execution context. StackOS validates and maps this to "
            "connector-specific scope/auth context; it is not sent as endpoint body, query, "
            "or path payload."
        ),
    )
    credential_ref: str | None = None


class ActionExecuteInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "action_ref": "utils.image.generate",
                "input_json": {"prompt": "Product photo"},
                "context_ref": "ctx_provider_analysis",
            }
        },
    )

    project_id: int
    action_ref: str | None = None
    plugin_slug: str | None = None
    action_key: str | None = None
    input_json: dict[str, Any] | None = None
    context_ref: str | None = Field(
        default=None,
        description=(
            "Optional reusable execution context ref. StackOS resolves credential and "
            "provider_context_json defaults inside the daemon."
        ),
    )
    provider_context_json: dict[str, Any] | None = Field(
        default=None,
        description="Optional provider execution context, separate from endpoint input_json.",
    )
    output_policy_json: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional output storage policy for this one call. Supported mode values are "
            "inline, file_if_large, and always_file. MCP and REST external provider calls "
            "default to plain file-backed output when omitted; CLI calls default inline. "
            "Pass path as an absolute output directory; StackOS generates the response "
            "filename."
        ),
    )
    credential_ref: str | None = None
    idempotency_key: str | None = None
    dry_run: bool = False
    metadata_json: dict[str, Any] | None = None


class ActionRunInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "action_ref": "communications.telegram-bot.message.send",
                "intent_summary": "User asked to send one Telegram test message.",
                "confirm_direct": True,
                "idempotency_key": "telegram-send-1",
                "input_json": {
                    "profile_key": "support",
                    "chat_ref": "telegram-chat:123",
                    "text": "Done.",
                },
                "context_ref": "ctx_provider_messaging",
            }
        },
    )

    project_id: int | None = None
    action_ref: str | None = None
    plugin_slug: str | None = None
    action_key: str | None = None
    input_json: dict[str, Any] | None = None
    context_ref: str | None = Field(
        default=None,
        description=(
            "Optional reusable execution context ref. StackOS resolves credential and "
            "provider_context_json defaults inside the daemon."
        ),
    )
    provider_context_json: dict[str, Any] | None = Field(
        default=None,
        description="Optional provider execution context, separate from endpoint input_json.",
    )
    output_policy_json: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional output storage policy for this one call. Supported mode values are "
            "inline, file_if_large, and always_file. MCP and REST external provider calls "
            "default to plain file-backed output when omitted; CLI calls default inline. "
            "Pass path as an absolute output directory; StackOS generates the response "
            "filename."
        ),
    )
    credential_ref: str | None = None
    idempotency_key: str | None = None
    intent_id: str | None = None
    dry_run: bool = False
    metadata_json: dict[str, Any] | None = None
    intent_summary: str | None = None
    confirm_direct: bool = False
    verbose: bool = Field(
        default=False,
        description=(
            "Deprecated compatibility flag. Use response_mode when selecting compact "
            "file-output metadata or the raw public audit shape."
        ),
    )


class ActionCallGetInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "action_call_id": 42}},
    )

    project_id: int | None = None
    action_call_id: int


class ActionCallGetOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_call_id: int
    status: ActionCallStatus
    action_ref: str
    provider_key: str | None = None
    operation: str
    progress: dict[str, Any] | None = None
    output_json: dict[str, Any] | None = None
    error: str | None = None
    outcome_unknown: bool | None = None
    retry_safe: bool | None = None
    created_at: datetime
    completed_at: datetime | None = None
    poll_operation: str | None = None
    poll_arguments: dict[str, Any] | None = None
    next_poll_after_ms: int | None = None


class ActionRunOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    action_ref: str
    action_call_id: int
    provider_key: str | None = None
    operation: str
    credential_ref: str | None = None
    cost_cents: int = 0
    dry_run: bool = False
    compact: dict[str, Any] = Field(default_factory=dict)
    action_call: dict[str, Any] | None = None
    output_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    poll_operation: str | None = None
    poll_arguments: dict[str, Any] | None = None
    next_poll_after_ms: int | None = None


__all__ = [
    "ACTION_CALL_POLL_RESPONSE_POLICY",
    "ACTION_FILE_OUTPUT_RESPONSE_POLICY",
    "ActionCallGetInput",
    "ActionCallGetOut",
    "ActionDescribeInput",
    "ActionExecuteInput",
    "ActionListInput",
    "ActionListItemOut",
    "ActionListOut",
    "ActionRunInput",
    "ActionRunOut",
    "ActionValidateInput",
]
