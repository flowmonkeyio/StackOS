"""StackOS action operation registrations."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import col, select

from stackos.action_availability import ActionAvailabilityOut, ActionExposureOut
from stackos.actions import (
    ActionDescribeOut,
    ActionExecutionOut,
    ActionRepository,
    ActionValidationOut,
)
from stackos.db.models import ApprovalRequest, ApprovalRequestStatus
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.permissions import active_run_plan_step
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.spec import (
    OperationExample,
    OperationResponsePolicy,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.base import ConflictError
from stackos.repositories.plugins import ActionOut, PluginRepository

ACTION_FILE_OUTPUT_RESPONSE_POLICY = OperationResponsePolicy(
    default_mode="compact",
    allowed_modes=("compact", "raw"),
    ack_safe=False,
    compact_notes=(
        "Compact action responses must keep action_call_id, action_ref, provider, status, "
        "cost, file path, checksum, and warnings.",
        "Raw provider output is stored in plain response files by default for external "
        "provider actions.",
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
            "inline, file_if_large, and always_file. External provider actions default to "
            "plain file-backed output when omitted. Pass path as an absolute output "
            "directory; StackOS generates the response filename."
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
            "inline, file_if_large, and always_file. External provider actions default to "
            "plain file-backed output when omitted. Pass path as an absolute output "
            "directory; StackOS generates the response filename."
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


async def action_list(
    inp: ActionListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ActionListOut:
    project_id = inp.project_id if inp.project_id is not None else ctx.project_id
    rows = PluginRepository(ctx.session).list_actions(
        plugin_slug=inp.plugin_slug,
        project_id=project_id,
    )
    scored: list[tuple[ActionOut, float]] = []
    for row in rows:
        score = _action_query_score(row, inp.query)
        if score <= 0:
            continue
        if _matches_action_filters(
            row,
            provider_key=inp.provider_key,
            capability_key=inp.capability_key,
            executable=inp.executable,
        ):
            scored.append((row, score))
    if (inp.query or "").strip():
        scored.sort(key=lambda item: (-item[1], item[0].plugin_slug, item[0].key))
    matched = [row for row, _score in scored]
    filtered = [
        row
        for row in matched
        if inp.include_unavailable_integrations or row.exposure.visible_by_default
    ]
    return ActionListOut(
        items=[_action_list_item(row, row.availability) for row in filtered],
        count=len(filtered),
        hidden_count=len(matched) - len(filtered),
        filters={
            key: value
            for key, value in {
                "project_id": project_id,
                "plugin_slug": inp.plugin_slug,
                "provider_key": inp.provider_key,
                "capability_key": inp.capability_key,
                "query": inp.query,
                "executable": inp.executable,
                "include_unavailable_integrations": inp.include_unavailable_integrations,
            }.items()
            if value is not None
        },
    )


def _matches_action_filters(
    row: ActionOut,
    *,
    provider_key: str | None,
    capability_key: str | None,
    executable: bool | None,
) -> bool:
    if provider_key is not None and row.provider_key != provider_key:
        return False
    if capability_key is not None and row.capability_key != capability_key:
        return False
    return executable is None or row.availability.executable == executable


_SEARCH_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _action_query_score(row: ActionOut, query: str | None) -> float:
    normalized_query = _normalize_search_text(query or "")
    if not normalized_query:
        return 1.0
    haystack = _action_search_haystack(row)
    if normalized_query in haystack:
        return 100.0 + len(normalized_query) / 1000
    query_tokens = _search_tokens(normalized_query)
    if not query_tokens:
        return 1.0
    haystack_tokens = set(_search_tokens(haystack))
    if not haystack_tokens:
        return 0.0
    matched = [token for token in query_tokens if token in haystack_tokens]
    if not matched:
        return 0.0
    ratio = len(matched) / len(query_tokens)
    required_ratio = 1.0 if len(query_tokens) <= 2 else 0.5
    if ratio < required_ratio:
        return 0.0
    return ratio * 10 + len(matched) / 100


def _action_search_haystack(row: ActionOut) -> str:
    values: list[str] = [
        row.action_ref,
        row.key,
        row.name,
        row.description,
        row.operation,
        row.provider_key or "",
        row.capability_key or "",
        row.risk_level,
    ]
    values.extend(_json_search_values(row.config_json))
    values.extend(_json_search_values(row.input_schema_json))
    values.extend(_json_search_values(row.output_schema_json))
    return _normalize_search_text(" ".join(value for value in values if value))


def _json_search_values(value: Any, *, limit: int = 600) -> list[str]:
    out: list[str] = []

    def visit(item: Any) -> None:
        if len(out) >= limit:
            return
        if isinstance(item, dict):
            for key, nested in item.items():
                if isinstance(key, str):
                    out.append(key)
                visit(nested)
                if len(out) >= limit:
                    return
        elif isinstance(item, list):
            for nested in item:
                visit(nested)
                if len(out) >= limit:
                    return
        elif isinstance(item, str):
            out.append(item)
        elif isinstance(item, int | float | bool):
            out.append(str(item))

    visit(value)
    return out


def _normalize_search_text(value: str) -> str:
    return " ".join(_SEARCH_TOKEN_RE.findall(value.replace("_", " ").replace("-", " ").lower()))


def _search_tokens(value: str) -> list[str]:
    raw_tokens = _SEARCH_TOKEN_RE.findall(value)
    tokens: list[str] = []
    for token in raw_tokens:
        variants = _search_token_variants(token)
        for variant in variants:
            if variant not in tokens:
                tokens.append(variant)
    return tokens


def _search_token_variants(token: str) -> list[str]:
    variants = [token]
    if len(token) > 3 and token.endswith("s"):
        variants.append(token[:-1])
    synonyms = {
        "metric": ["metrics"],
        "metrics": ["metric"],
        "partner": ["partners", "partnership", "partnerships"],
        "partners": ["partner", "partnership", "partnerships"],
        "partnership": ["partner", "partners", "relationships", "relationship"],
        "partnerships": ["partner", "partners", "relationships", "relationship"],
        "relation": ["relationship", "relationships"],
        "relationship": ["relation", "relationships", "partnership", "partnerships"],
        "relationships": ["relation", "relationship", "partnership", "partnerships"],
        "report": ["reporting", "reports"],
        "reports": ["report", "reporting"],
        "reporting": ["report", "reports"],
    }
    variants.extend(synonyms.get(token, []))
    return variants


def _action_list_item(row: ActionOut, availability: ActionAvailabilityOut) -> ActionListItemOut:
    return ActionListItemOut(
        action_ref=row.action_ref,
        plugin_slug=row.plugin_slug,
        key=row.key,
        name=row.name,
        description=row.description,
        provider_key=row.provider_key,
        capability_key=row.capability_key,
        risk_level=row.risk_level,
        operation=row.operation,
        connector_key=row.connector_key,
        requires_credential=row.requires_credential,
        allows_credential=row.allows_credential,
        budget_kind=row.budget_kind,
        executable=availability.executable,
        availability_status=availability.status,
        availability_reasons=list(availability.reasons),
        credential_state=availability.credential_state,
        budget_state=availability.budget_state,
        exposure=row.exposure,
    )


async def action_describe(
    inp: ActionDescribeInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ActionDescribeOut:
    project_id = inp.project_id if inp.project_id is not None else ctx.project_id
    return ActionRepository(ctx.session).describe(
        action_ref=inp.action_ref,
        plugin_slug=inp.plugin_slug,
        action_key=inp.action_key,
        project_id=project_id,
    )


async def action_validate(
    inp: ActionValidateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ActionValidationOut:
    project_id = inp.project_id if inp.project_id is not None else ctx.project_id
    return ActionRepository(ctx.session).validate(
        project_id=project_id,
        action_ref=inp.action_ref,
        plugin_slug=inp.plugin_slug,
        action_key=inp.action_key,
        input_json=inp.input_json,
        context_ref=inp.context_ref,
        provider_context_json=inp.provider_context_json,
        credential_ref=inp.credential_ref,
    )


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
        default_external_file_output=True,
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
    approval_ref = _approval_ref_for_action(grant_snapshot, action_ref)
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
        default_external_file_output=True,
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
    return ActionRunOut(
        status=call.status.value if hasattr(call.status, "value") else str(call.status),
        action_ref=action_ref,
        action_call_id=call.id,
        provider_key=call.provider_key,
        operation=call.operation,
        credential_ref=execution.credential_ref,
        cost_cents=execution.cost_cents,
        dry_run=execution.dry_run,
        compact=_compact_action_output(
            provider_key=call.provider_key,
            operation=call.operation,
            output_json=execution.output_json,
        ),
        action_call=call.model_dump(mode="json") if verbose else None,
        output_json=execution.output_json if verbose else None,
        metadata_json=execution.metadata_json if verbose else None,
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


def operation_specs() -> list[OperationSpec]:
    return [
        OperationSpec(
            name="action.list",
            summary="List or search action contracts with compact availability state.",
            input_model=ActionListInput,
            output_model=ActionListOut,
            handler=action_list,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/action.list/call",
                ),
                cli=OperationSurface(enabled=True, command="actions list"),
            ),
            purpose=(
                "Use this when an agent needs to discover currently usable action refs without "
                "walking plugin manifests, broad catalog payloads, or disconnected provider noise."
            ),
            when_to_use=(
                (
                    "An agent knows a plugin, provider, capability, or search term "
                    "and needs candidate actions."
                ),
                "A caller needs project-aware executable/blocked state for many actions at once.",
                (
                    "A setup/admin caller needs hidden disconnected or non-executable "
                    "external-provider actions and can pass "
                    "include_unavailable_integrations=true deliberately."
                ),
            ),
            prerequisites=(
                (
                    "Pass project_id when project-specific credential, budget, and plugin "
                    "availability matters."
                ),
                (
                    "Use action.describe for the exact schema and connector details before "
                    "executing an action."
                ),
            ),
            returns=(
                (
                    "Compact action summaries with action_ref, provider/capability, risk, "
                    "operation, and availability."
                ),
                (
                    "Availability state includes executable, credential_state, budget_state, "
                    "and model-readable reasons."
                ),
                (
                    "Exposure state says whether the action is visible in normal discovery "
                    "or hidden until a provider integration is connected or the action "
                    "becomes executable."
                ),
            ),
            examples=(
                OperationExample(
                    title="Find ready sitemap actions",
                    arguments={"project_id": 1, "query": "sitemap", "executable": True},
                ),
                OperationExample(
                    title="List communication Slack bot actions",
                    arguments={"plugin_slug": "communications", "provider_key": "slack-bot"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="action.describe",
            summary=(
                "Describe one action manifest, connector availability, auth state, "
                "and budget state."
            ),
            input_model=ActionDescribeInput,
            output_model=ActionDescribeOut,
            handler=action_describe,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/action.describe/call",
                ),
                cli=OperationSurface(enabled=True, command="actions describe"),
            ),
            purpose=(
                "Use this before a run to inspect the exact action contract and whether "
                "the current project is configured to execute it."
            ),
            when_to_use=(
                "The agent needs schema, provider, connector, credential, or budget status.",
                "A human or script wants to check why an action is not executable yet.",
            ),
            prerequisites=(
                "Pass either action_ref or plugin_slug plus action_key.",
                "Pass project_id when project-specific availability is needed.",
            ),
            returns=(
                "Static manifest details.",
                "Connector registration and executable availability.",
                "Safe credential refs and setup reasons; never plaintext secrets.",
            ),
            examples=(
                OperationExample(
                    title="Describe OpenAI image generation",
                    arguments={"project_id": 1, "action_ref": "utils.image.generate"},
                ),
            ),
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="action.validate",
            summary="Validate one explicit action payload without executing the connector.",
            input_model=ActionValidateInput,
            output_model=ActionValidationOut,
            handler=action_validate,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/action.validate/call",
                ),
                cli=OperationSurface(enabled=True, command="actions validate"),
            ),
            purpose=(
                "Use this to check a concrete payload against the action schema, "
                "credential policy, and connector validator before execution."
            ),
            when_to_use=(
                "The agent has chosen an action and built a candidate input payload.",
                "A script wants a dry validation gate before creating a run plan.",
            ),
            prerequisites=(
                "Pass either action_ref or plugin_slug plus action_key.",
                "Pass input_json with the exact payload the action would receive.",
                "Pass context_ref when reusable execution defaults should supply credential "
                "or provider context; pass credential_ref only for a deliberate direct override.",
            ),
            returns=(
                "valid=true when schema, credential policy, and connector validation pass.",
                "Structured issues with paths and machine-readable codes when validation fails.",
            ),
            examples=(
                OperationExample(
                    title="Validate sitemap fetch payload",
                    arguments={
                        "project_id": 1,
                        "action_ref": "utils.sitemap.fetch",
                        "input_json": {"urls": ["https://example.com/sitemap.xml"]},
                        "context_ref": "ctx_provider_analysis",
                    },
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="action.execute",
            summary=(
                "Execute one action inside an explicitly granted run-plan step and return "
                "compact file-backed output metadata by default."
            ),
            input_model=ActionExecuteInput,
            output_model=WriteEnvelope[ActionExecutionOut],
            handler=action_execute,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/action.execute/call",
                ),
                cli=OperationSurface(enabled=True, command="actions execute"),
            ),
            purpose=(
                "Use this only after a run plan has started and the active claimed step "
                "grants the exact action ref. StackOS resolves credentials inside the daemon."
            ),
            when_to_use=(
                "A run-plan step is currently running and names the exact action_ref.",
                "The frozen run-plan grant snapshot includes action.execute for that ref.",
            ),
            prerequisites=(
                "Pass project_id and run_token from runPlan.start.",
                "Exactly one run-plan step must be running.",
                "The requested action_ref must match the step and mcp_tool_grants refs.",
                "Pass context_ref when the active task/run has a reusable execution context; "
                "pass only opaque credential_ref values for deliberate low-level overrides.",
                "External provider action output is file-backed by default; inspect the "
                "returned file path before rerunning the provider call. Call schema.get "
                "with schema_ref only when the response-file envelope schema is needed.",
            ),
            returns=(
                "A WriteEnvelope containing the public ActionExecutionOut.",
                "A redacted audit row linked to run_id, run_plan_id, and run_plan_step_id.",
                "For external provider actions, compact file path, schema_ref, "
                "schema_operation, and metadata for the sanitized request+response envelope "
                "by default.",
            ),
            examples=(
                OperationExample(
                    title="Execute no-auth sitemap fetch from a run-plan step",
                    arguments={
                        "project_id": 1,
                        "run_token": "run-plan-token",
                        "action_ref": "utils.sitemap.fetch",
                        "input_json": {"urls": ["https://example.com/sitemap.xml"]},
                    },
                ),
            ),
            grant_policy="run-plan-step-action-ref",
            response_policy=ACTION_FILE_OUTPUT_RESPONSE_POLICY,
        ),
        OperationSpec(
            name="action.run",
            summary=(
                "Run one explicit action directly with compact file-backed output metadata "
                "and audit."
            ),
            input_model=ActionRunInput,
            output_model=WriteEnvelope[ActionRunOut],
            handler=action_run,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/action.run/call",
                ),
                cli=OperationSurface(enabled=True, command="actions run"),
            ),
            purpose=(
                "Use this for a single explicit tool action when no multi-step workflow "
                "is needed. StackOS still validates inputs, resolves daemon-held "
                "credentials, enforces provider/profile policy, and records action audit."
            ),
            when_to_use=(
                "The user asked for one concrete action, such as sending one message.",
                "The work does not need a template, multi-step plan, artifacts, or learning loop.",
            ),
            prerequisites=(
                "The current workspace must resolve to a project, or pass project_id.",
                "Pass context_ref when the project/task has reusable execution defaults; "
                "pass only opaque credential_ref values for deliberate low-level overrides.",
                "For non-read actions, pass confirm_direct=true and intent_summary; "
                "pass intent_id or idempotency_key when stable retries matter. "
                "If omitted, StackOS derives a request-scoped idempotency key.",
                "External provider action output is file-backed by default; inspect the "
                "returned file path before rerunning the provider call. Call schema.get "
                "with schema_ref only when the response-file envelope schema is needed.",
            ),
            returns=(
                "A redacted action-call audit id linked to the project.",
                "Compact output metadata with file path, schema_ref, schema_operation, "
                "checksum, and summaries. Raw provider response data lives in the "
                "file-backed envelope.",
            ),
            examples=(
                OperationExample(
                    title="Send one Telegram message directly",
                    arguments={
                        "action_ref": "communications.telegram-bot.message.send",
                        "confirm_direct": True,
                        "intent_summary": "User asked to send one status message.",
                        "idempotency_key": "telegram-send-status-1",
                        "input_json": {
                            "profile_key": "support",
                            "chat_ref": "telegram-chat:123",
                            "text": "Done.",
                        },
                    },
                ),
            ),
            grant_policy="direct-action-policy",
            response_policy=ACTION_FILE_OUTPUT_RESPONSE_POLICY,
        ),
    ]


__all__ = [
    "ActionDescribeInput",
    "ActionExecuteInput",
    "ActionListInput",
    "ActionListItemOut",
    "ActionListOut",
    "ActionRunInput",
    "ActionRunOut",
    "ActionValidateInput",
    "action_describe",
    "action_execute",
    "action_list",
    "action_run",
    "action_validate",
    "operation_specs",
]
