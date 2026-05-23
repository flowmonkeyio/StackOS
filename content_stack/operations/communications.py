"""Communication setup operations shared by REST, CLI, and MCP."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, select

from content_stack.db.models import IntegrationCredential, Plugin, Resource, ResourceRecord
from content_stack.mcp.context import MCPContext
from content_stack.mcp.contract import MCPInput, WriteEnvelope
from content_stack.mcp.streaming import ProgressEmitter
from content_stack.operations.spec import (
    OperationExample,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from content_stack.repositories.agent_requests import AgentRequestOut, AgentRequestRepository
from content_stack.repositories.base import Page, ValidationError
from content_stack.repositories.projects import ProjectRepository
from content_stack.repositories.resources import ResourceRepository

_PROFILE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_ACCESS_MODES = {"all", "allowlist", "denylist", "disabled"}
_INGRESS_MODES = {"local-webhook", "webhook", "disabled"}
_ALLOWED_UPDATES = {
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "callback_query",
    "my_chat_member",
    "chat_member",
}


class TelegramAccessPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dm_mode: Literal["all", "allowlist", "denylist", "disabled"] = "allowlist"
    group_mode: Literal["all", "allowlist", "denylist", "disabled"] = "allowlist"
    user_mode: Literal["all", "allowlist", "denylist", "disabled"] = "allowlist"
    allowed_chat_refs: list[str] = Field(default_factory=list)
    denied_chat_refs: list[str] = Field(default_factory=list)
    allowed_user_refs: list[str] = Field(default_factory=list)
    denied_user_refs: list[str] = Field(default_factory=list)


class TelegramCommandSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str
    description: str = ""
    guidance: str = ""
    enabled: bool = True
    aliases: list[str] = Field(default_factory=list)
    arguments_schema: dict[str, Any] = Field(default_factory=dict)
    required_context: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)


class TelegramTriggerPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dm_trigger: Literal["always", "never"] = "always"
    group_trigger: Literal["mention_or_command", "always", "never"] = "mention_or_command"
    commands: list[TelegramCommandSpec] = Field(default_factory=list)
    mention_patterns: list[str] = Field(default_factory=list)
    reply_to_bot_triggers: bool = True


class TelegramBotIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=120)
    purpose: str = ""
    voice: str = ""


class TelegramAgentGuidance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_instructions: str = ""
    boundaries: str = ""
    escalation: str = ""


class TelegramVisibilityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store_non_trigger_messages: bool = True


class TelegramResponsePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply_in_same_chat: bool = True
    origin_required: bool = True
    reply_to_source_message: bool = False
    same_thread: bool = False


class TelegramBotProfileUpsertInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "key": "support-bot",
                "auth_profile_key": "support",
                "bot_username": "support_bot",
                "identity": {
                    "display_name": "Support Bot",
                    "purpose": "Handle customer support requests from approved Telegram users.",
                    "voice": "Calm, concise, and operational.",
                },
                "agent_guidance": {
                    "default_instructions": (
                        "Triage the request, inspect project context, and reply only when "
                        "the next action is clear."
                    ),
                    "boundaries": (
                        "Do not promise refunds, legal advice, or account changes without "
                        "an approved run plan."
                    ),
                },
                "access_policy": {
                    "dm_mode": "allowlist",
                    "group_mode": "allowlist",
                    "user_mode": "allowlist",
                    "allowed_chat_refs": ["telegram-chat:100"],
                    "allowed_user_refs": ["telegram-user:555"],
                },
            }
        },
    )

    project_id: int
    key: str
    auth_profile_key: str = "default"
    enabled: bool = True
    bot_username: str | None = None
    ingress_mode: Literal["local-webhook", "webhook", "disabled"] = "local-webhook"
    allowed_updates: list[str] = Field(default_factory=lambda: ["message", "callback_query"])
    identity: TelegramBotIdentity
    agent_guidance: TelegramAgentGuidance = Field(default_factory=TelegramAgentGuidance)
    access_policy: TelegramAccessPolicy = Field(default_factory=TelegramAccessPolicy)
    trigger_policy: TelegramTriggerPolicy = Field(default_factory=TelegramTriggerPolicy)
    visibility_policy: TelegramVisibilityPolicy = Field(default_factory=TelegramVisibilityPolicy)
    context_policy: dict[str, Any] = Field(default_factory=lambda: {"include_last_messages": 50})
    response_policy: TelegramResponsePolicy = Field(default_factory=TelegramResponsePolicy)
    refs: dict[str, str] = Field(default_factory=dict)
    reply_to_message_refs: dict[str, int] = Field(default_factory=dict)
    thread_refs: dict[str, int] = Field(default_factory=dict)
    direct_messages_topic_refs: dict[str, int] = Field(default_factory=dict)
    webhook_base_url: str | None = None
    allowed_webhook_hosts: list[str] = Field(default_factory=list)


class TelegramBotProfileGetInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "key": "support-bot"}},
    )

    project_id: int
    key: str


class TelegramBotProfileListInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"project_id": 1, "limit": 25}},
    )

    project_id: int
    limit: int | None = None
    after_id: int | None = None


class TelegramBotProfileOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: int
    project_id: int
    external_id: str
    key: str
    provider_key: str
    auth_profile_key: str
    enabled: bool
    bot_username: str | None = None
    ingress_mode: str
    allowed_updates: list[str]
    identity: dict[str, Any]
    agent_guidance: dict[str, Any]
    access_policy: dict[str, Any]
    trigger_policy: dict[str, Any]
    visibility_policy: dict[str, Any]
    context_policy: dict[str, Any]
    response_policy: dict[str, Any]
    refs: dict[str, str]
    reply_to_message_refs: dict[str, int] = Field(default_factory=dict)
    thread_refs: dict[str, int] = Field(default_factory=dict)
    direct_messages_topic_refs: dict[str, int] = Field(default_factory=dict)
    webhook_base_url: str | None = None
    allowed_webhook_hosts: list[str] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None


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


async def communication_bot_profile_upsert(
    inp: TelegramBotProfileUpsertInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[TelegramBotProfileOut]:
    _require_project(ctx.session, inp.project_id)
    _validate_profile_key(inp.key)
    _require_project_telegram_credential(
        ctx.session,
        project_id=inp.project_id,
        auth_profile_key=inp.auth_profile_key,
    )
    _validate_access_policy(inp.access_policy)
    _validate_allowed_updates(inp.allowed_updates)
    _validate_trigger_policy(inp.trigger_policy)
    data_json = _profile_data(inp)
    env = ResourceRepository(ctx.session).upsert_record(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="communication-bot-profile",
        external_id=_external_id(inp.key),
        title=inp.key,
        data_json=data_json,
        provenance_json={"source": "communicationBotProfile.upsert"},
    )
    return WriteEnvelope(
        data=_out_from_record(env.data.id, env.data.project_id, env.data.external_id, data_json),
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def communication_bot_profile_get(
    inp: TelegramBotProfileGetInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> TelegramBotProfileOut:
    _require_project(ctx.session, inp.project_id)
    _validate_profile_key(inp.key)
    row = _record_by_external_id(ctx.session, project_id=inp.project_id, key=inp.key)
    if row is None:
        raise ValidationError("Telegram bot profile was not found")
    return _out_from_record(row.id, row.project_id, row.external_id, row.data_json or {})


async def communication_bot_profile_list(
    inp: TelegramBotProfileListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> Page[TelegramBotProfileOut]:
    _require_project(ctx.session, inp.project_id)
    records = ResourceRepository(ctx.session).query_records(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="communication-bot-profile",
        limit=inp.limit,
        after_id=inp.after_id,
    )
    return Page(
        items=[
            _out_from_record(
                record.id,
                record.project_id,
                record.external_id,
                record.data_json or {},
            )
            for record in records.items
        ],
        next_cursor=records.next_cursor,
        total_estimate=records.total_estimate,
    )


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


def _validate_profile_key(value: str) -> None:
    if not _PROFILE_KEY_RE.fullmatch(value.strip()):
        raise ValidationError(
            "Telegram bot profile key must be 1-80 chars of letters, numbers, _, or -"
        )


def _validate_local_key(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", value.strip()):
        raise ValidationError(f"{label} must be 1-160 chars of letters, numbers, _, ., :, or -")


def _require_project_telegram_credential(
    session: Session,
    *,
    project_id: int,
    auth_profile_key: str,
) -> None:
    row = session.exec(
        select(IntegrationCredential).where(
            col(IntegrationCredential.project_id) == project_id,
            col(IntegrationCredential.kind) == "telegram-bot",
            col(IntegrationCredential.profile_key) == auth_profile_key,
        )
    ).first()
    if row is None:
        raise ValidationError(
            "Telegram bot profile requires a project-scoped telegram-bot credential",
            data={"auth_profile_key": auth_profile_key},
        )


def _validate_access_policy(policy: TelegramAccessPolicy) -> None:
    for key in ("dm_mode", "group_mode", "user_mode"):
        mode = getattr(policy, key)
        if mode not in _ACCESS_MODES:
            raise ValidationError(f"access_policy.{key} is invalid")
        if mode == "allowlist":
            has_chat_allowlist = bool(policy.allowed_chat_refs)
            has_user_allowlist = bool(policy.allowed_user_refs)
            if key == "dm_mode" and not (has_chat_allowlist or has_user_allowlist):
                raise ValidationError(
                    "access_policy.dm_mode=allowlist requires allowed chats or users"
                )
            if key == "group_mode" and not has_chat_allowlist:
                raise ValidationError("access_policy.group_mode=allowlist requires allowed chats")
            if key == "user_mode" and not has_user_allowlist:
                raise ValidationError("access_policy.user_mode=allowlist requires allowed users")


def _validate_allowed_updates(values: list[str]) -> None:
    if not values:
        raise ValidationError("allowed_updates is required")
    extra = sorted(set(values) - _ALLOWED_UPDATES)
    if extra:
        raise ValidationError(
            "allowed_updates contains unsupported values",
            data={"updates": extra},
        )


def _validate_trigger_policy(policy: TelegramTriggerPolicy) -> None:
    seen: set[str] = set()
    for spec in policy.commands:
        command = _normalize_command(spec.command)
        if not command:
            raise ValidationError("trigger_policy.commands[].command is required")
        if not re.fullmatch(r"/[A-Za-z0-9_]{1,32}", command):
            raise ValidationError(
                "trigger_policy.commands[].command must look like /support or /status"
            )
        if command in seen:
            raise ValidationError(
                "trigger_policy.commands contains duplicate commands",
                data={"command": command},
            )
        seen.add(command)
        for alias in spec.aliases:
            normalized = _normalize_command(alias)
            if not re.fullmatch(r"/[A-Za-z0-9_]{1,32}", normalized):
                raise ValidationError(
                    "trigger_policy.commands[].aliases must look like /support or /status"
                )


def _normalize_command(value: str) -> str:
    command = value.strip()
    if not command:
        return ""
    return command if command.startswith("/") else f"/{command}"


def _local_body_preview(text: str | None, content_blocks: list[dict[str, Any]]) -> str:
    if text and text.strip():
        return text.strip()[:500]
    for block in content_blocks:
        for key in ("text", "caption", "title", "summary"):
            value = block.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:500]
    return ""


def _profile_data(inp: TelegramBotProfileUpsertInput) -> dict[str, Any]:
    data: dict[str, Any] = {
        "key": inp.key.strip(),
        "provider_key": "telegram-bot",
        "auth_profile_key": inp.auth_profile_key.strip(),
        "enabled": inp.enabled,
        "ingress_mode": inp.ingress_mode,
        "allowed_updates": inp.allowed_updates,
        "identity": inp.identity.model_dump(mode="python"),
        "agent_guidance": inp.agent_guidance.model_dump(mode="python"),
        "access_policy": inp.access_policy.model_dump(mode="python"),
        "trigger_policy": {
            **inp.trigger_policy.model_dump(mode="python"),
            "commands": [
                {
                    **command.model_dump(mode="python"),
                    "command": _normalize_command(command.command),
                }
                for command in inp.trigger_policy.commands
            ],
        },
        "visibility_policy": inp.visibility_policy.model_dump(mode="python"),
        "context_policy": inp.context_policy,
        "response_policy": inp.response_policy.model_dump(mode="python"),
        "refs": inp.refs,
        "reply_to_message_refs": inp.reply_to_message_refs,
        "thread_refs": inp.thread_refs,
        "direct_messages_topic_refs": inp.direct_messages_topic_refs,
        "allowed_webhook_hosts": inp.allowed_webhook_hosts,
    }
    if inp.bot_username and inp.bot_username.strip():
        data["bot_username"] = inp.bot_username.strip().lstrip("@")
    if inp.webhook_base_url and inp.webhook_base_url.strip():
        data["webhook_base_url"] = inp.webhook_base_url.strip()
    return data


def _record_by_external_id(
    session: Session,
    *,
    project_id: int,
    key: str,
) -> ResourceRecord | None:
    ResourceRepository(session).list_resources(
        plugin_slug="communications",
        project_id=project_id,
    )
    return session.exec(
        select(ResourceRecord)
        .join(Resource, col(ResourceRecord.resource_id) == col(Resource.id))
        .join(Plugin, col(Resource.plugin_id) == col(Plugin.id))
        .where(
            col(ResourceRecord.project_id) == project_id,
            col(ResourceRecord.external_id) == _external_id(key),
            col(Resource.key) == "communication-bot-profile",
            col(Plugin.slug) == "communications",
        )
    ).first()


def _external_id(key: str) -> str:
    return f"telegram-bot-profile:{key.strip()}"


def _out_from_record(
    record_id: int | None,
    project_id: int,
    external_id: str | None,
    data: dict[str, Any],
) -> TelegramBotProfileOut:
    return TelegramBotProfileOut(
        record_id=int(record_id or 0),
        project_id=project_id,
        external_id=str(external_id or _external_id(str(data.get("key") or ""))),
        key=str(data.get("key") or ""),
        provider_key=str(data.get("provider_key") or "telegram-bot"),
        auth_profile_key=str(data.get("auth_profile_key") or ""),
        enabled=bool(data.get("enabled", True)),
        bot_username=(
            data.get("bot_username") if isinstance(data.get("bot_username"), str) else None
        ),
        ingress_mode=str(data.get("ingress_mode") or "local-webhook"),
        allowed_updates=[str(item) for item in data.get("allowed_updates") or []],
        identity=dict(data.get("identity") or {}),
        agent_guidance=dict(data.get("agent_guidance") or {}),
        access_policy=dict(data.get("access_policy") or {}),
        trigger_policy=dict(data.get("trigger_policy") or {}),
        visibility_policy=dict(data.get("visibility_policy") or {}),
        context_policy=dict(data.get("context_policy") or {}),
        response_policy=dict(data.get("response_policy") or {}),
        refs={str(key): str(value) for key, value in dict(data.get("refs") or {}).items()},
        reply_to_message_refs={
            str(key): int(value)
            for key, value in dict(data.get("reply_to_message_refs") or {}).items()
        },
        thread_refs={
            str(key): int(value) for key, value in dict(data.get("thread_refs") or {}).items()
        },
        direct_messages_topic_refs={
            str(key): int(value)
            for key, value in dict(data.get("direct_messages_topic_refs") or {}).items()
        },
        webhook_base_url=(
            data.get("webhook_base_url") if isinstance(data.get("webhook_base_url"), str) else None
        ),
        allowed_webhook_hosts=[str(item) for item in data.get("allowed_webhook_hosts") or []],
    )


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
            ),
            returns=(
                "A WriteEnvelope with thread/message refs and created AgentRequestOut "
                "when requested.",
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
            ),
            grant_policy="direct-work-queue-write",
        ),
        OperationSpec(
            name="communicationBotProfile.list",
            summary="List project-scoped Telegram bot profiles.",
            input_model=TelegramBotProfileListInput,
            output_model=Page[TelegramBotProfileOut],
            handler=communication_bot_profile_list,
            surfaces=_surfaces(
                "communicationBotProfile.list",
                "ops call communicationBotProfile.list",
            ),
            purpose=(
                "Use this during setup or diagnostics to discover the Telegram bot profiles "
                "available to a project. It returns safe profile policy/configuration only."
            ),
            prerequisites=("Pass project_id.", "Use limit/after_id for bounded pagination."),
            returns=("A Page of TelegramBotProfileOut records with no credential payloads.",),
            examples=(OperationExample(title="List bot profiles", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="communicationBotProfile.get",
            summary="Get one project-scoped Telegram bot profile by key.",
            input_model=TelegramBotProfileGetInput,
            output_model=TelegramBotProfileOut,
            handler=communication_bot_profile_get,
            surfaces=_surfaces(
                "communicationBotProfile.get",
                "ops call communicationBotProfile.get",
            ),
            purpose=(
                "Use this before setting a webhook or executing Telegram actions so an agent "
                "can inspect bot identity, agent guidance, access, trigger, context, and "
                "response policy."
            ),
            prerequisites=("Pass project_id and key.",),
            returns=("One safe TelegramBotProfileOut record.",),
            examples=(
                OperationExample(
                    title="Get support bot profile",
                    arguments={"project_id": 1, "key": "support-bot"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="communicationBotProfile.upsert",
            summary="Create or update a project-scoped Telegram bot profile.",
            input_model=TelegramBotProfileUpsertInput,
            output_model=WriteEnvelope[TelegramBotProfileOut],
            handler=communication_bot_profile_upsert,
            surfaces=_surfaces(
                "communicationBotProfile.upsert",
                "ops call communicationBotProfile.upsert",
            ),
            purpose=(
                "Use this setup operation after storing a telegram-bot credential. It binds "
                "bot identity, agent guidance, and static policy to a project credential "
                "profile without exposing bot tokens to agents."
            ),
            prerequisites=(
                "Store a project-scoped telegram-bot credential first with auth_profile_key.",
                "Use explicit access modes; allowlist modes require matching allowlists.",
                (
                    "Keep identity, guidance, context, and response policy declarative. "
                    "The agent still decides work."
                ),
            ),
            returns=("A WriteEnvelope with the safe TelegramBotProfileOut record.",),
            examples=(
                OperationExample(
                    title="Create a support bot profile",
                    arguments={
                        "project_id": 1,
                        "key": "support-bot",
                        "auth_profile_key": "support",
                        "bot_username": "support_bot",
                        "identity": {
                            "display_name": "Support Bot",
                            "purpose": "Handle support requests from approved Telegram users.",
                            "voice": "Concise and calm.",
                        },
                        "agent_guidance": {
                            "default_instructions": (
                                "Triage the request and use project tools before replying."
                            ),
                            "boundaries": (
                                "Escalate billing or legal requests instead of improvising."
                            ),
                        },
                        "access_policy": {
                            "dm_mode": "allowlist",
                            "group_mode": "allowlist",
                            "user_mode": "allowlist",
                            "allowed_chat_refs": ["telegram-chat:100"],
                            "allowed_user_refs": ["telegram-user:555"],
                        },
                    },
                ),
            ),
            grant_policy="direct-setup-write",
        ),
    ]


__all__ = [
    "LocalAgentChatCreateMessageInput",
    "LocalAgentChatMessageOut",
    "TelegramAccessPolicy",
    "TelegramAgentGuidance",
    "TelegramBotIdentity",
    "TelegramBotProfileGetInput",
    "TelegramBotProfileListInput",
    "TelegramBotProfileOut",
    "TelegramBotProfileUpsertInput",
    "TelegramCommandSpec",
    "operation_specs",
]
