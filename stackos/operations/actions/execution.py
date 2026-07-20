"""Action execution handlers and response shaping."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlmodel import col, select

from stackos.actions import ActionExecutionOut, ActionRepository
from stackos.actions.repository.background import BACKGROUND_ACTION_TASKS
from stackos.db.models import ActionCallStatus, ApprovalRequest, ApprovalRequestStatus
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import WriteEnvelope
from stackos.mcp.permissions import active_run_plan_step
from stackos.mcp.streaming import ProgressEmitter
from stackos.repositories.base import ConflictError

from .schemas import (
    ActionCallGetInput,
    ActionCallGetOut,
    ActionExecuteInput,
    ActionRunInput,
    ActionRunOut,
)


async def action_call_get(
    inp: ActionCallGetInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ActionCallGetOut:
    project_id = inp.project_id if inp.project_id is not None else ctx.project_id
    if project_id is None:
        from stackos.repositories.base import ValidationError

        raise ValidationError(
            "project_id is required unless the agent bridge resolved the workspace project"
        )

    call = ActionRepository(ctx.session).get_call(
        project_id=project_id,
        action_call_id=inp.action_call_id,
    )
    running = call.status == ActionCallStatus.RUNNING
    output_json = call.response_json
    progress = BACKGROUND_ACTION_TASKS.progress(call.id) if running else None
    return ActionCallGetOut(
        action_call_id=call.id,
        status=call.status,
        action_ref=f"{call.plugin_slug}.{call.action_key}",
        provider_key=call.provider_key,
        operation=call.operation,
        progress=_public_action_progress(progress),
        output_json=output_json,
        error=call.error,
        outcome_unknown=_terminal_diagnosis_flag(output_json, "outcome_unknown"),
        retry_safe=_terminal_diagnosis_flag(output_json, "retry_safe"),
        created_at=call.created_at,
        completed_at=call.completed_at,
        poll_operation="actionCall.get" if running else None,
        poll_arguments={"action_call_id": call.id} if running else None,
        next_poll_after_ms=500 if running else None,
    )


def _public_action_progress(progress: dict[str, Any] | None) -> dict[str, Any] | None:
    if progress is None:
        return None
    return {
        key: progress[key]
        for key in (
            "phase",
            "operation",
            "bytes_transferred",
            "completed_count",
            "skipped_count",
            "failed_count",
        )
        if key in progress
    }


def _terminal_diagnosis_flag(output: dict[str, Any] | None, key: str) -> bool | None:
    if not isinstance(output, dict):
        return None
    direct = output.get(key)
    if isinstance(direct, bool):
        return direct
    provider_error = output.get("provider_error")
    if isinstance(provider_error, dict):
        nested = provider_error.get(key)
        if isinstance(nested, bool):
            return nested
    return None


async def action_execute(
    inp: ActionExecuteInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[ActionExecutionOut]:
    plan, step = active_run_plan_step(ctx, "action.execute")
    action_ref = inp.action_ref
    if action_ref is None and inp.plugin_slug and inp.action_key:
        action_ref = f"{inp.plugin_slug}.{inp.action_key}"
    if not inp.dry_run:
        _ensure_action_contract_approval(
            ctx,
            plan_id=plan.id,
            grant_snapshot=plan.grant_snapshot_json,
            action_ref=action_ref,
        )
    idempotency_key = inp.idempotency_key or _derive_workflow_idempotency_key(
        project_id=inp.project_id,
        run_id=ctx.run_id,
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
        step_id=step.step_id,
        action_ref=action_ref,
        input_json=inp.input_json,
        context_ref=inp.context_ref,
        provider_context_json=inp.provider_context_json,
        credential_ref=inp.credential_ref,
        dry_run=inp.dry_run,
    )
    settings = ctx.extras.get("settings")
    asset_dir = getattr(settings, "generated_assets_dir", None)
    env = await ActionRepository(ctx.session, asset_dir=asset_dir).execute(
        project_id=inp.project_id,
        action_ref=inp.action_ref,
        plugin_slug=inp.plugin_slug,
        action_key=inp.action_key,
        input_json=inp.input_json,
        context_ref=inp.context_ref,
        provider_context_json=inp.provider_context_json,
        output_policy_json=inp.output_policy_json,
        default_external_file_output=_default_external_file_output(ctx),
        credential_ref=inp.credential_ref,
        run_id=ctx.run_id,
        run_plan_id=plan.id,
        run_plan_step_id=step.id,
        idempotency_key=idempotency_key,
        dry_run=inp.dry_run,
        metadata_json={
            **(inp.metadata_json or {}),
            "dedupe_source": "caller" if inp.idempotency_key else "workflow-step",
        },
    )
    return WriteEnvelope[ActionExecutionOut](
        data=env.data,
        run_id=env.run_id,
        project_id=env.project_id,
    )


def _ensure_action_contract_approval(
    ctx: MCPContext,
    *,
    plan_id: int | None,
    grant_snapshot: dict[str, Any] | None,
    action_ref: str | None,
) -> None:
    if plan_id is None or action_ref is None:
        return
    if not isinstance(grant_snapshot, dict):
        return
    # Resolve through the facade so the historical private patch seam remains effective.
    from stackos.operations import actions as action_operations

    approval_ref = action_operations._approval_ref_for_action(grant_snapshot, action_ref)
    if approval_ref is None:
        return
    approval = ctx.session.exec(
        select(ApprovalRequest).where(
            col(ApprovalRequest.run_plan_id) == plan_id,
            col(ApprovalRequest.approval_key) == approval_ref,
        )
    ).first()
    if approval is None or approval.status != ApprovalRequestStatus.APPROVED:
        raise ConflictError(
            "action execution requires approval",
            data={
                "run_plan_id": plan_id,
                "action_ref": action_ref,
                "approval_ref": approval_ref,
                "approval_status": str(approval.status) if approval is not None else "missing",
            },
        )


def _approval_ref_for_action(
    grant_snapshot: dict[str, Any],
    action_ref: str,
) -> str | None:
    resolved_by_key: dict[str, str] = {}
    for item in grant_snapshot.get("resolved_action_contracts") or []:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        resolved = item.get("action_ref")
        if isinstance(key, str) and isinstance(resolved, str):
            resolved_by_key[key] = resolved
    template_plugin_slug = grant_snapshot.get("template_plugin_slug")
    plugin_slug = template_plugin_slug if isinstance(template_plugin_slug, str) else None
    for item in grant_snapshot.get("action_contracts") or []:
        if not isinstance(item, dict):
            continue
        approval_ref = item.get("approval_ref")
        if not isinstance(approval_ref, str) or not approval_ref:
            continue
        key = item.get("key")
        candidates = []
        if isinstance(key, str):
            candidates.append(resolved_by_key.get(key))
        raw_action = item.get("action")
        if isinstance(raw_action, str):
            candidates.append(raw_action)
            if "." not in raw_action and plugin_slug:
                candidates.append(f"{plugin_slug}.{raw_action}")
        if action_ref in {candidate for candidate in candidates if candidate}:
            return approval_ref
    return None


async def action_run(
    inp: ActionRunInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[ActionRunOut]:
    project_id = inp.project_id if inp.project_id is not None else ctx.project_id
    if project_id is None:
        from stackos.repositories.base import ValidationError

        raise ValidationError(
            "project_id is required unless the agent bridge resolved the workspace project"
        )

    repo = ActionRepository(ctx.session)
    described = repo.describe(
        project_id=project_id,
        action_ref=inp.action_ref,
        plugin_slug=inp.plugin_slug,
        action_key=inp.action_key,
    )
    _check_direct_action_policy(
        risk_level=described.manifest.risk_level,
        config_json=described.manifest.config_json,
        dry_run=inp.dry_run,
        confirm_direct=inp.confirm_direct,
        intent_summary=inp.intent_summary,
    )
    idempotency_key = inp.idempotency_key
    idempotency_key_source = "caller" if idempotency_key else None
    if not inp.dry_run and described.manifest.risk_level != "read" and idempotency_key is None:
        idempotency_key = _derive_direct_idempotency_key(
            project_id=project_id,
            action_ref=described.manifest.action_ref,
            input_json=inp.input_json,
            context_ref=inp.context_ref,
            provider_context_json=inp.provider_context_json,
            credential_ref=inp.credential_ref,
            intent_id=inp.intent_id,
            intent_summary=inp.intent_summary,
            request_id=ctx.request_id,
        )
        idempotency_key_source = "intent_id" if inp.intent_id else "request"
    settings = ctx.extras.get("settings")
    asset_dir = getattr(settings, "generated_assets_dir", None)
    metadata = {
        **(inp.metadata_json or {}),
        "direct_action": True,
    }
    if idempotency_key_source:
        metadata["dedupe_source"] = idempotency_key_source
    if inp.intent_summary:
        metadata["intent_summary"] = inp.intent_summary
    env = await ActionRepository(ctx.session, asset_dir=asset_dir).execute(
        project_id=project_id,
        action_ref=described.manifest.action_ref,
        input_json=inp.input_json,
        context_ref=inp.context_ref,
        provider_context_json=inp.provider_context_json,
        output_policy_json=inp.output_policy_json,
        default_external_file_output=_default_external_file_output(ctx),
        credential_ref=inp.credential_ref,
        run_id=ctx.run_id,
        idempotency_key=idempotency_key,
        dry_run=inp.dry_run,
        metadata_json=metadata,
    )
    out = _action_run_out(env.data, verbose=True)
    return WriteEnvelope[ActionRunOut](
        data=out,
        run_id=env.run_id,
        project_id=env.project_id,
    )


def _check_direct_action_policy(
    *,
    risk_level: str,
    config_json: dict[str, Any],
    dry_run: bool,
    confirm_direct: bool,
    intent_summary: str | None,
) -> None:
    from stackos.repositories.base import ValidationError

    direct_config = config_json.get("direct_run")
    if direct_config is False or direct_config == "workflow-only":
        raise ValidationError("action is configured for workflow execution only")
    if dry_run or risk_level == "read":
        return
    if not confirm_direct or not (intent_summary or "").strip():
        raise ValidationError(
            "direct non-read actions require confirm_direct=true and intent_summary",
            data={"risk_level": risk_level},
        )


def _default_external_file_output(ctx: MCPContext) -> bool:
    return ctx.extras.get("client_surface") in {"mcp", "rest"}


def _derive_direct_idempotency_key(
    *,
    project_id: int,
    action_ref: str,
    input_json: dict[str, Any] | None,
    context_ref: str | None,
    provider_context_json: dict[str, Any] | None,
    credential_ref: str | None,
    intent_id: str | None,
    intent_summary: str | None,
    request_id: str,
) -> str:
    source = {
        "scope": "direct-action",
        "project_id": project_id,
        "action_ref": action_ref,
        "context_ref": context_ref,
        "credential_ref": credential_ref,
        "input_json": input_json or {},
        "provider_context_json": provider_context_json or {},
        "intent_id": (intent_id or "").strip() or None,
        "intent_summary": (intent_summary or "").strip(),
        "request_id": None if intent_id else request_id,
    }
    return f"direct:{_stable_digest(source)}"


def _derive_workflow_idempotency_key(
    *,
    project_id: int,
    run_id: int | None,
    run_plan_id: int | None,
    run_plan_step_id: int | None,
    step_id: str,
    action_ref: str | None,
    input_json: dict[str, Any] | None,
    context_ref: str | None,
    provider_context_json: dict[str, Any] | None,
    credential_ref: str | None,
    dry_run: bool,
) -> str:
    source = {
        "scope": "workflow-step-action",
        "project_id": project_id,
        "run_id": run_id,
        "run_plan_id": run_plan_id,
        "run_plan_step_id": run_plan_step_id,
        "step_id": step_id,
        "action_ref": action_ref,
        "context_ref": context_ref,
        "credential_ref": credential_ref,
        "input_json": input_json or {},
        "provider_context_json": provider_context_json or {},
        "dry_run": dry_run,
    }
    return f"workflow:{_stable_digest(source)}"


def _stable_digest(value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _action_run_out(execution: ActionExecutionOut, *, verbose: bool) -> ActionRunOut:
    call = execution.action_call
    action_ref = f"{call.plugin_slug}.{call.action_key}"
    from stackos.operations import actions as action_operations

    return ActionRunOut(
        status=call.status.value if hasattr(call.status, "value") else str(call.status),
        action_ref=action_ref,
        action_call_id=call.id,
        provider_key=call.provider_key,
        operation=call.operation,
        credential_ref=execution.credential_ref,
        cost_cents=execution.cost_cents,
        dry_run=execution.dry_run,
        compact=action_operations._compact_action_output(
            provider_key=call.provider_key,
            operation=call.operation,
            output_json=execution.output_json,
        ),
        action_call=call.model_dump(mode="json") if verbose else None,
        output_json=execution.output_json if verbose else None,
        metadata_json=execution.metadata_json if verbose else None,
        poll_operation=execution.poll_operation,
        poll_arguments=execution.poll_arguments,
        next_poll_after_ms=execution.next_poll_after_ms,
    )


def _compact_action_output(
    *,
    provider_key: str | None,
    operation: str,
    output_json: dict[str, Any],
) -> dict[str, Any]:
    if output_json.get("output_mode") == "file" and isinstance(output_json.get("file"), dict):
        file = output_json["file"]
        compact_file = {
            "output_mode": "file",
            "path": file.get("path"),
            "content_type": file.get("content_type"),
            "schema_version": file.get("schema_version"),
            "schema_ref": file.get("schema_ref"),
            "schema_operation": file.get("schema_operation"),
            "semantic_name": file.get("semantic_name"),
            "bytes": file.get("bytes"),
            "sha256": file.get("sha256"),
        }
        return {key: value for key, value in compact_file.items() if value is not None}
    if provider_key == "telegram-bot":
        return _compact_telegram_output(operation, output_json)
    compact: dict[str, Any] = {}
    for key, value in output_json.items():
        if isinstance(value, str | int | float | bool) or value is None:
            compact[key] = _compact_scalar(value)
    if "status_code" in output_json:
        compact["status_code"] = output_json["status_code"]
    return compact or {"keys": sorted(output_json)}


def _compact_scalar(value: str | int | float | bool | None) -> str | int | float | bool | None:
    if isinstance(value, str) and len(value) > 500:
        return f"{value[:500]}..."
    return value


def _compact_telegram_output(operation: str, output_json: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {"operation": operation}
    for key in (
        "artifact_ref",
        "artifact_id",
        "filename",
        "mime_type",
        "size_bytes",
        "source_file_id",
        "source_message_ref",
    ):
        value = output_json.get(key)
        if isinstance(value, str | int | float | bool) or value is None:
            compact[key] = _compact_scalar(value)
    status_code = output_json.get("status_code")
    if isinstance(status_code, int):
        compact["status_code"] = status_code
    body = output_json.get("body")
    if not isinstance(body, dict):
        return compact
    if isinstance(body.get("ok"), bool):
        compact["provider_ok"] = body["ok"]
    result = body.get("result")
    if operation == "updates.poll" and isinstance(result, list):
        updates = [
            _compact_telegram_update(update) for update in result if isinstance(update, dict)
        ]
        compact["updates_count"] = len(updates)
        compact["updates"] = updates
        update_ids = [
            item["update_id"] for item in updates if isinstance(item.get("update_id"), int)
        ]
        if update_ids:
            compact["next_offset"] = max(update_ids) + 1
        return compact
    if isinstance(result, dict):
        message_id = result.get("message_id")
        if isinstance(message_id, int):
            compact["message_id"] = message_id
        chat = result.get("chat")
        if isinstance(chat, dict):
            chat_id = chat.get("id")
            if isinstance(chat_id, int):
                compact["chat_ref"] = f"telegram-chat:{chat_id}"
            if isinstance(chat.get("type"), str):
                compact["chat_type"] = chat["type"]
        if isinstance(result.get("text"), str):
            compact["text_preview"] = result["text"][:200]
    return compact


def _compact_telegram_update(update: dict[str, Any]) -> dict[str, Any]:
    item: dict[str, Any] = {}
    update_id = update.get("update_id")
    if isinstance(update_id, int):
        item["update_id"] = update_id
    if isinstance(update.get("callback_query"), dict):
        callback = update["callback_query"]
        item["kind"] = "callback_query"
        if isinstance(callback.get("id"), str):
            item["callback_query_id"] = callback["id"]
        if isinstance(callback.get("data"), str):
            item["callback_data"] = callback["data"]
        _add_telegram_user_ref(item, callback.get("from"))
        message = callback.get("message")
        if isinstance(message, dict):
            _add_telegram_chat_ref(item, message.get("chat"))
            if isinstance(message.get("message_id"), int):
                item["source_message_id"] = message["message_id"]
        return item
    for key, kind in (
        ("message", "message"),
        ("edited_message", "edited_message"),
        ("channel_post", "channel_post"),
        ("edited_channel_post", "edited_channel_post"),
    ):
        message = update.get(key)
        if not isinstance(message, dict):
            continue
        item["kind"] = kind
        if isinstance(message.get("message_id"), int):
            item["message_id"] = message["message_id"]
        _add_telegram_user_ref(item, message.get("from"))
        _add_telegram_chat_ref(item, message.get("chat"))
        if isinstance(message.get("text"), str):
            item["text_preview"] = message["text"][:200]
        return item
    item["kind"] = "unknown"
    return item


def _add_telegram_user_ref(out: dict[str, Any], raw: Any) -> None:
    if not isinstance(raw, dict):
        return
    user_id = raw.get("id")
    if isinstance(user_id, int):
        out["user_ref"] = f"telegram-user:{user_id}"
    if isinstance(raw.get("username"), str):
        out["username"] = raw["username"]


def _add_telegram_chat_ref(out: dict[str, Any], raw: Any) -> None:
    if not isinstance(raw, dict):
        return
    chat_id = raw.get("id")
    if isinstance(chat_id, int):
        out["chat_ref"] = f"telegram-chat:{chat_id}"
    if isinstance(raw.get("type"), str):
        out["chat_type"] = raw["type"]


__all__ = [
    "_approval_ref_for_action",
    "_compact_action_output",
    "action_call_get",
    "action_execute",
    "action_run",
]
