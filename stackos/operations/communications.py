"""Communication setup operations shared by REST, CLI, and MCP."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.communication_delivery import (
    operation_specs as communication_delivery_operation_specs,
)
from stackos.operations.communication_platform import (
    operation_specs as communication_platform_operation_specs,
)
from stackos.operations.spec import (
    OperationExample,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.agent_requests import AgentRequestOut, AgentRequestRepository
from stackos.repositories.base import ValidationError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.resources import ResourceRepository


class LocalAgentChatCreateMessageInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "thread_key": "support",
                "message_key": "msg-20260523-001",
                "sender_ref": "local-user:operator",
                "sender_display_name": "Operator",
                "text": "Check the latest media buying results and suggest next actions.",
                "create_request": True,
            }
        },
    )

    project_id: int
    thread_key: str = Field(min_length=1, max_length=120)
    message_key: str = Field(min_length=1, max_length=160)
    direction: Literal["inbound", "outbound"] = "inbound"
    sender_ref: str = Field(min_length=1, max_length=160)
    sender_display_name: str | None = Field(default=None, max_length=160)
    text: str | None = Field(default=None, max_length=20_000)
    content_blocks: list[dict[str, Any]] = Field(default_factory=list)
    create_request: bool = True
    request_title: str | None = Field(default=None, max_length=240)
    priority: int = 0
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class LocalAgentChatMessageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int
    thread_record_id: int
    message_record_id: int
    agent_request: AgentRequestOut | None = None
    thread_ref: str
    message_ref: str


async def local_agent_chat_create_message(
    inp: LocalAgentChatCreateMessageInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[LocalAgentChatMessageOut]:
    _require_project(ctx.session, inp.project_id)
    _validate_local_key(inp.thread_key, "thread_key")
    _validate_local_key(inp.message_key, "message_key")
    body_preview = _local_body_preview(inp.text, inp.content_blocks)
    if not body_preview and not inp.content_blocks:
        raise ValidationError("text or content_blocks is required")
    thread_ref = f"local-agent-chat:thread:{inp.thread_key.strip()}"
    message_ref = f"local-agent-chat:message:{inp.thread_key.strip()}:{inp.message_key.strip()}"
    resources = ResourceRepository(ctx.session)
    thread = resources.upsert_record(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="communication-thread",
        external_id=thread_ref,
        title=f"Local chat {inp.thread_key.strip()}",
        data_json={
            "provider_key": "local-agent-chat",
            "thread_ref": thread_ref,
            "thread_key": inp.thread_key.strip(),
            "channel_type": "local-agent-chat",
        },
        provenance_json={"source": "localAgentChat.createMessage"},
    ).data
    message = resources.upsert_record(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="communication-message",
        external_id=message_ref,
        title=inp.request_title or "Local agent chat message",
        data_json={
            "provider_key": "local-agent-chat",
            "direction": inp.direction,
            "thread_ref": thread_ref,
            "message_ref": message_ref,
            "sender_ref": inp.sender_ref,
            "sender_display_name": inp.sender_display_name,
            "content_type": "blocks" if inp.content_blocks else "text",
            "text_preview": body_preview,
            "content_blocks": inp.content_blocks,
            "attention_status": "unread" if inp.direction == "inbound" else "sent",
            "metadata_json": inp.metadata_json,
        },
        provenance_json={"source": "localAgentChat.createMessage"},
    ).data
    agent_request = None
    if inp.direction == "inbound" and inp.create_request:
        agent_request = (
            AgentRequestRepository(ctx.session)
            .create(
                project_id=inp.project_id,
                request_key=f"local-agent-chat:{inp.thread_key.strip()}:{inp.message_key.strip()}",
                title=inp.request_title or "Local agent chat message",
                body_preview=body_preview,
                source_provider="local-agent-chat",
                source_kind="local_chat_message",
                source_resource_key="communication-message",
                source_resource_record_id=message.id,
                source_message_ref=message_ref,
                priority=inp.priority,
                metadata_json={
                    "thread_ref": thread_ref,
                    "message_ref": message_ref,
                    "thread_key": inp.thread_key.strip(),
                    "message_key": inp.message_key.strip(),
                    "sender_ref": inp.sender_ref,
                    "sender_display_name": inp.sender_display_name,
                    "content_blocks": inp.content_blocks,
                    "metadata_json": inp.metadata_json,
                },
            )
            .data
        )
    out = LocalAgentChatMessageOut(
        project_id=inp.project_id,
        thread_record_id=int(thread.id or 0),
        message_record_id=int(message.id or 0),
        agent_request=agent_request,
        thread_ref=thread_ref,
        message_ref=message_ref,
    )
    return WriteEnvelope(data=out, run_id=ctx.run_id, project_id=inp.project_id)


def _require_project(session: Session, project_id: int) -> None:
    ProjectRepository(session).get(project_id)


def _validate_local_key(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", value.strip()):
        raise ValidationError(f"{label} must be 1-160 chars of letters, numbers, _, ., :, or -")


def _local_body_preview(text: str | None, content_blocks: list[dict[str, Any]]) -> str:
    if text and text.strip():
        return text.strip()[:500]
    for block in content_blocks:
        for key in ("text", "caption", "title", "summary"):
            value = block.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:500]
    return ""


def _surfaces(name: str, command: str) -> OperationSurfaces:
    return OperationSurfaces(
        mcp=OperationSurface(enabled=True),
        rest=OperationSurface(enabled=True, path=f"/api/v1/operations/{name}/call"),
        cli=OperationSurface(enabled=True, command=command),
    )


def operation_specs() -> list[OperationSpec]:
    return [
        OperationSpec(
            name="localAgentChat.createMessage",
            summary="Store a local agent chat message and optionally create agent work.",
            input_model=LocalAgentChatCreateMessageInput,
            output_model=WriteEnvelope[LocalAgentChatMessageOut],
            handler=local_agent_chat_create_message,
            surfaces=_surfaces(
                "localAgentChat.createMessage",
                "ops call localAgentChat.createMessage",
            ),
            purpose=(
                "Use this for local StackOS chat surfaces. It stores a message/thread "
                "resource and, for inbound human messages, can create a generic "
                "agent_request. It never invokes a model or decides workflow intent."
            ),
            prerequisites=(
                "Pass a deterministic thread_key and message_key so repeated calls are idempotent.",
                "Pass explicit text or content_blocks; do not include secrets.",
                "The caller chooses whether to create an agent request.",
                "For an agent response in the same local chat thread, pass "
                "direction=outbound, the same thread_key, a new message_key, "
                "and create_request=false.",
            ),
            returns=(
                "A WriteEnvelope with thread/message refs and created AgentRequestOut "
                "when requested.",
                "Outbound local-chat messages are stored as communication-message "
                "resources and do not create agent requests.",
            ),
            examples=(
                OperationExample(
                    title="Create local chat request",
                    arguments={
                        "project_id": 1,
                        "thread_key": "support",
                        "message_key": "msg-001",
                        "sender_ref": "local-user:operator",
                        "sender_display_name": "Operator",
                        "text": "Review the latest campaign numbers.",
                        "create_request": True,
                    },
                ),
                OperationExample(
                    title="Store local chat agent response",
                    arguments={
                        "project_id": 1,
                        "thread_key": "support",
                        "message_key": "msg-002",
                        "direction": "outbound",
                        "sender_ref": "agent:codex",
                        "text": "I reviewed the campaign numbers and opened a run plan.",
                        "create_request": False,
                    },
                ),
            ),
            grant_policy="direct-work-queue-write",
        ),
        *communication_delivery_operation_specs(),
        *communication_platform_operation_specs(),
    ]


__all__ = [
    "LocalAgentChatCreateMessageInput",
    "LocalAgentChatMessageOut",
    "operation_specs",
]
