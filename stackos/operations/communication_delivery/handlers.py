"""Public communication delivery operation handlers."""

from __future__ import annotations

from stackos.mcp.context import MCPContext
from stackos.mcp.contract import WriteEnvelope
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.communication_platform import _default_action_ref
from stackos.repositories.agent_requests import AgentRequestRepository

from .errors import _reject
from .execution import _execute_delivery
from .payloads import _build_provider_payload, _reply_delivery
from .policy import _ensure_reply_policy, _ensure_target_policy
from .resolution import (
    _origin_target,
    _request_origin,
    _require_target,
    _resolve_actor,
    _source_context,
    _surface_data,
)
from .schemas import (
    CommunicationContextInput,
    CommunicationReplyInput,
    CommunicationSendInput,
    CommunicationSendOut,
)
from .utils import (
    _derive_idempotency_key,
    _normalize_content,
    _require_project,
    _resolve_project_id,
)


async def communication_send(
    inp: CommunicationSendInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[CommunicationSendOut]:
    project_id = _resolve_project_id(inp.project_id, ctx)
    _require_project(ctx.session, project_id)
    content = _normalize_content(
        text=inp.text,
        content=inp.content,
        attachments=inp.attachments,
        controls=inp.controls,
    )
    source = _source_context(ctx.session, project_id=project_id, context=inp.context)
    target = _require_target(ctx.session, project_id=project_id, to=inp.to)
    actor = _resolve_actor(
        ctx.session,
        project_id=project_id,
        provider_key=target.provider_key,
        target=target,
        requested_from=inp.from_ref,
        source_actor_ref=source.get("profile_ref") or source.get("profile_key"),
    )
    _ensure_target_policy(
        policy=target.send_policy,
        target=target,
        actor_ref=actor["profile_ref"],
        source_surface_ref=source.get("source_surface_ref"),
        invoker_ref=source.get("invoker_ref"),
        resolved={
            "operation": "communication.send",
            "to": inp.to,
            "from": actor["profile_ref"],
            "provider": target.provider_key,
            "surface_ref": target.surface_ref,
        },
    )
    surface = _surface_data(ctx.session, project_id=project_id, surface_ref=target.surface_ref)
    action_ref = target.action_ref or _default_action_ref(target.provider_key)
    if action_ref is None:
        action_ref = ""
    idempotency_key = inp.idempotency_key or _derive_idempotency_key(
        project_id=project_id,
        operation="communication.send",
        action_ref=action_ref,
        actor_ref=actor["profile_ref"],
        destination_ref=target.target_ref,
        content=content,
        source_request_id=source.get("source_request_id"),
        intent_id=inp.intent_id,
        request_id=ctx.request_id,
    )
    payload = _build_provider_payload(
        session=ctx.session,
        project_id=project_id,
        provider_key=target.provider_key,
        action_ref=action_ref or None,
        actor=actor,
        target=target,
        content=content,
        delivery=inp.delivery,
        context=inp.context,
        source=source,
        surface=surface,
        operation="communication.send",
        idempotency_key=idempotency_key,
    )
    return await _execute_delivery(
        ctx,
        project_id=project_id,
        operation="communication.send",
        action_ref=payload["action_ref"],
        input_json=payload["input_json"],
        credential_ref=actor["credential_ref"],
        idempotency_key=idempotency_key,
        dry_run=inp.dry_run,
        metadata_json={
            "operation": "communication.send",
            "target_ref": target.target_ref,
            "actor_ref": actor["profile_ref"],
            "source_request_id": source.get("source_request_id"),
            "intent_summary": inp.intent_summary,
        },
        resolved={
            "operation": "communication.send",
            "to": inp.to,
            "from": actor["profile_ref"],
            "target_ref": target.target_ref,
            "provider_key": target.provider_key,
            "surface_ref": target.surface_ref,
        },
        target_ref=target.target_ref,
        actor_ref=actor["profile_ref"],
        surface_ref=target.surface_ref,
        fallback=inp.fallback,
    )


async def communication_reply(
    inp: CommunicationReplyInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[CommunicationSendOut]:
    project_id = _resolve_project_id(inp.project_id, ctx)
    _require_project(ctx.session, project_id)
    request = AgentRequestRepository(ctx.session).get(
        project_id=project_id,
        request_id=inp.request_id,
    )
    source = _request_origin(ctx.session, project_id=project_id, request=request)
    content = _normalize_content(
        text=inp.text,
        content=inp.content,
        attachments=inp.attachments,
        controls=inp.controls,
    )
    provider_key = str(source.get("provider_key") or request.source_provider or "")
    if provider_key not in {"slack-bot", "telegram-bot"}:
        _reject(
            code="COMM_UNSUPPORTED_PROVIDER",
            category="provider",
            message=f"Request origin provider {provider_key or '<missing>'} is not reply-capable.",
            resolved={
                "operation": "communication.reply",
                "request_id": inp.request_id,
                "provider": provider_key or None,
            },
            failed_paths=[
                {
                    "path": "/request_id",
                    "requested": "reply.origin",
                    "target_supports": ["slack-bot", "telegram-bot"],
                    "target_does_not_support": [provider_key or "missing_provider"],
                }
            ],
            repair_options=[
                {
                    "id": "choose_explicit_target",
                    "description": "Use communication.send with an explicit named target.",
                }
            ],
        )
    target = _origin_target(source, project_id=project_id, provider_key=provider_key)
    actor = _resolve_actor(
        ctx.session,
        project_id=project_id,
        provider_key=provider_key,
        target=target,
        requested_from=inp.from_ref or source.get("profile_ref") or source.get("profile_key"),
        source_actor_ref=None,
    )
    _ensure_reply_policy(
        operation="communication.reply",
        provider_key=provider_key,
        actor=actor,
        source=source,
        target=target,
    )
    payload = _build_provider_payload(
        session=ctx.session,
        project_id=project_id,
        provider_key=provider_key,
        action_ref=target.action_ref or _default_action_ref(provider_key),
        actor=actor,
        target=target,
        content=content,
        delivery=_reply_delivery(inp.delivery),
        context=CommunicationContextInput(
            source_request_id=inp.request_id,
            reply_to=source.get("message_ref"),
            thread_ref=source.get("thread_ref"),
            source_surface_ref=source.get("source_surface_ref"),
            invoker_ref=source.get("invoker_ref"),
        ),
        source=source,
        surface=_surface_data(
            ctx.session,
            project_id=project_id,
            surface_ref=str(source.get("source_surface_ref") or ""),
        ),
        operation="communication.reply",
    )
    return await _execute_delivery(
        ctx,
        project_id=project_id,
        operation="communication.reply",
        action_ref=payload["action_ref"],
        input_json=payload["input_json"],
        credential_ref=actor["credential_ref"],
        idempotency_key=inp.idempotency_key
        or _derive_idempotency_key(
            project_id=project_id,
            operation="communication.reply",
            action_ref=payload["action_ref"],
            actor_ref=actor["profile_ref"],
            destination_ref=str(source.get("source_surface_ref") or ""),
            content=content,
            source_request_id=inp.request_id,
            intent_id=inp.intent_id,
            request_id=ctx.request_id,
        ),
        dry_run=inp.dry_run,
        metadata_json={
            "operation": "communication.reply",
            "actor_ref": actor["profile_ref"],
            "source_request_id": inp.request_id,
            "intent_summary": inp.intent_summary,
        },
        resolved={
            "operation": "communication.reply",
            "request_id": inp.request_id,
            "from": actor["profile_ref"],
            "provider_key": provider_key,
            "surface_ref": source.get("source_surface_ref"),
        },
        target_ref=None,
        actor_ref=actor["profile_ref"],
        surface_ref=source.get("source_surface_ref"),
        fallback=inp.fallback,
    )
