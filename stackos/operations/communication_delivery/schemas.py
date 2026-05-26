"""Input and output schemas for communication delivery operations."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from stackos.mcp.contract import MCPInput

from .constants import _DEFAULT_FALLBACK_MODE


class CommunicationAttachmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["image", "document", "file"]
    artifact_ref: str | None = None
    url: str | None = None
    file_id: str | None = None
    caption: str | None = None
    filename: str | None = None
    mime_type: str | None = None


class CommunicationControlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["button"] = "button"
    label: str
    action: str | None = None
    value: str | None = None
    callback_data: str | None = None
    url: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    style: Literal["default", "primary", "danger"] = "default"


class CommunicationContentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    subject: str | None = None
    html: str | None = None
    format: Literal["auto", "plain", "markdown", "mrkdwn", "html"] = "auto"
    attachments: list[CommunicationAttachmentInput] = Field(default_factory=list)
    controls: list[CommunicationControlInput] = Field(default_factory=list)


class CommunicationContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_request_id: int | None = None
    reply_to: str | None = None
    thread: str | None = None
    thread_ref: str | None = None
    source_surface_ref: str | None = None
    invoker_ref: str | None = None


class CommunicationDeliveryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visibility: Literal["channel", "private", "origin"] = "channel"
    reply_mode: Literal["default", "same_thread", "new_thread", "message_reply", "none"] = "default"
    disable_notification: bool | None = None
    reply_broadcast: bool | None = None


class CommunicationFallbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["reject"] = _DEFAULT_FALLBACK_MODE


class CommunicationSendInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        json_schema_extra={
            "example": {
                "project_id": 1,
                "from": "ops-bot",
                "to": "slack-roadmap",
                "text": "Done. The fix shipped.",
                "context": {"source_request_id": 5},
            }
        },
    )

    project_id: int | None = None
    to: str
    from_ref: str | None = Field(default=None, alias="from")
    text: str | None = None
    content: CommunicationContentInput | None = None
    attachments: list[CommunicationAttachmentInput] = Field(default_factory=list)
    controls: list[CommunicationControlInput] = Field(default_factory=list)
    context: CommunicationContextInput = Field(default_factory=CommunicationContextInput)
    delivery: CommunicationDeliveryInput = Field(default_factory=CommunicationDeliveryInput)
    fallback: CommunicationFallbackInput = Field(default_factory=CommunicationFallbackInput)
    intent_id: str | None = None
    intent_summary: str | None = None
    dry_run: bool = False


class CommunicationReplyInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        json_schema_extra={
            "example": {
                "project_id": 1,
                "request_id": 42,
                "text": "I checked it. Routing is fixed.",
            }
        },
    )

    project_id: int | None = None
    request_id: int
    from_ref: str | None = Field(default=None, alias="from")
    text: str | None = None
    content: CommunicationContentInput | None = None
    attachments: list[CommunicationAttachmentInput] = Field(default_factory=list)
    controls: list[CommunicationControlInput] = Field(default_factory=list)
    delivery: CommunicationDeliveryInput = Field(default_factory=CommunicationDeliveryInput)
    fallback: CommunicationFallbackInput = Field(default_factory=CommunicationFallbackInput)
    intent_id: str | None = None
    intent_summary: str | None = None
    dry_run: bool = False


class CommunicationSendOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    status: str
    action_call_id: int | None = None
    action_ref: str
    provider_key: str
    target_ref: str | None = None
    actor_ref: str | None = None
    surface_ref: str | None = None
    thread_ref: str | None = None
    message_ref: str | None = None
    dry_run: bool = False
    effects: list[str] = Field(default_factory=list)
    resolved: dict[str, Any] = Field(default_factory=dict)
