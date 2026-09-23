"""Communication profile setup operations."""

from __future__ import annotations

import re

from stackos.communications import (
    communication_profile_account_uses,
    normalize_communication_profile_facets,
    validate_communication_profile_account_bindings,
    validate_communication_profile_ingress_ownership,
)
from stackos.integrations.telegram_tdlib.update_types import (
    RETAINABLE_TELEGRAM_UPDATE_TYPES,
    TELEGRAM_ACCOUNT_UPDATE_TYPES,
    TELEGRAM_CHAT_SURFACE_REF_PATTERN,
)
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import WriteEnvelope
from stackos.mcp.streaming import ProgressEmitter
from stackos.repositories.base import Page, ValidationError
from stackos.repositories.resources import ResourceRepository

from .schemas import (
    CommunicationProfileAccountUsageInput,
    CommunicationProfileAccountUsageOut,
    CommunicationProfileAccountUseOut,
    CommunicationProfileGetInput,
    CommunicationProfileListInput,
    CommunicationProfileOut,
    CommunicationProfileUpsertInput,
)
from .utils import (
    _communication_profile_out,
    _communication_profile_ref,
    _record_by_resource_external_id,
    _require_project,
    _validate_identity,
    _validate_no_setup_secrets,
    _validate_profile_key,
)


async def communication_profile_account_usage(
    inp: CommunicationProfileAccountUsageInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> CommunicationProfileAccountUsageOut:
    if inp.project_id is not None:
        _require_project(ctx.session, inp.project_id)
    return CommunicationProfileAccountUsageOut(
        project_id=inp.project_id,
        uses=[
            CommunicationProfileAccountUseOut.model_validate(item)
            for item in communication_profile_account_uses(
                ctx.session,
                project_id=inp.project_id,
            )
        ],
    )


async def communication_profile_upsert(
    inp: CommunicationProfileUpsertInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[CommunicationProfileOut]:
    _require_project(ctx.session, inp.project_id)
    _validate_profile_key(inp.key)
    _validate_identity(inp.identity)
    _validate_no_setup_secrets(
        "communicationProfile.upsert",
        {
            "provider_facets": inp.provider_facets,
            "metadata_json": inp.metadata_json,
        },
    )
    profile_ref = _communication_profile_ref(inp.key)
    provider_facets = normalize_communication_profile_facets(inp.provider_facets)
    _validate_telegram_retention_selection(provider_facets, inp.visibility_policy)
    validate_communication_profile_account_bindings(
        ctx.session,
        project_id=inp.project_id,
        profile_ref=profile_ref,
        provider_facets=provider_facets,
    )
    validate_communication_profile_ingress_ownership(
        ctx.session,
        project_id=inp.project_id,
        profile_ref=profile_ref,
        provider_facets=provider_facets,
        profile_enabled=inp.enabled,
    )
    data_json = {
        "key": inp.key.strip(),
        "profile_ref": profile_ref,
        "enabled": inp.enabled,
        "identity": inp.identity,
        "agent_guidance": inp.agent_guidance,
        "provider_facets": provider_facets,
        "access_policy": inp.access_policy,
        "visibility_policy": inp.visibility_policy,
        "trigger_policy": inp.trigger_policy,
        "context_policy": inp.context_policy,
        "response_policy": inp.response_policy,
        "send_policy": inp.send_policy,
        "handoff_policy": inp.handoff_policy,
        "approval_policy": inp.approval_policy,
        "metadata_json": inp.metadata_json,
    }
    env = ResourceRepository(ctx.session).upsert_record(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="communication-profile",
        external_id=_communication_profile_ref(inp.key),
        title=str(inp.identity.get("display_name") or inp.key.strip()),
        data_json=data_json,
        provenance_json={"source": "communicationProfile.upsert"},
    )
    return WriteEnvelope(
        data=_communication_profile_out(
            ctx.session,
            env.data.id,
            env.data.project_id,
            env.data.data_json,
        ),
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


def _validate_telegram_retention_selection(
    provider_facets: dict[str, dict[str, object]], visibility_policy: dict[str, object]
) -> None:
    if "telegram" not in provider_facets:
        return
    for mode_field in ("surface_mode", "dm_mode", "group_mode", "channel_mode"):
        mode = visibility_policy.get(mode_field)
        if mode is not None and mode not in {"allowlist", "all", "denylist", "disabled"}:
            raise ValidationError(
                "Telegram retention visibility mode is invalid",
                data={
                    "field": f"visibility_policy.{mode_field}",
                    "allowed_modes": ["allowlist", "all", "denylist", "disabled"],
                },
            )
    selected_types = visibility_policy.get("allowed_update_types")
    if selected_types is not None and (
        not isinstance(selected_types, list)
        or any(value not in RETAINABLE_TELEGRAM_UPDATE_TYPES for value in selected_types)
    ):
        raise ValidationError(
            "Telegram retention update types must use supported native TDLib names",
            data={
                "field": "visibility_policy.allowed_update_types",
                "allowed_update_types": list(RETAINABLE_TELEGRAM_UPDATE_TYPES),
                "next_action": "Select exact update type names from the allowed_update_types list.",
            },
        )
    account_types = [
        value for value in (selected_types or []) if value in TELEGRAM_ACCOUNT_UPDATE_TYPES
    ]
    if account_types and visibility_policy.get("surface_mode") != "all":
        raise ValidationError(
            "Account-scoped Telegram updates need an explicit all-surface selection",
            data={
                "field": "visibility_policy.surface_mode",
                "account_scoped_update_types": account_types,
                "required_value": "all",
                "next_action": (
                    "Set surface_mode=all to retain these account-scoped types; this also "
                    "admits selected update types from every chat."
                ),
            },
        )
    selected_surfaces = visibility_policy.get("allowed_surface_refs")
    if selected_surfaces is not None and (
        not isinstance(selected_surfaces, list)
        or any(
            not _valid_telegram_surface_selection(
                value, allow_other_providers=len(provider_facets) > 1
            )
            for value in selected_surfaces
        )
    ):
        raise ValidationError(
            "Telegram retention surfaces must use chat refs returned by Telegram navigation",
            data={
                "field": "visibility_policy.allowed_surface_refs",
                "format": "telegram-chat:<signed numeric TDLib chat id>",
                "next_action": "Copy surface_ref from telegram.chat.list or telegram.chat.resolve.",
            },
        )


def _valid_telegram_surface_selection(value: object, *, allow_other_providers: bool) -> bool:
    if not isinstance(value, str) or re.fullmatch(r"-?[0-9]+", value) is not None:
        return False
    if not value.startswith("telegram-chat:"):
        return allow_other_providers and re.fullmatch(r"[a-z][a-z0-9-]*:.+", value) is not None
    if re.fullmatch(TELEGRAM_CHAT_SURFACE_REF_PATTERN, value) is None:
        return False
    numeric_id = value.removeprefix("telegram-chat:")
    return len(numeric_id.lstrip("-")) <= 16 and abs(int(numeric_id)) < 2**53


async def communication_profile_get(
    inp: CommunicationProfileGetInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> CommunicationProfileOut:
    _require_project(ctx.session, inp.project_id)
    _validate_profile_key(inp.key)
    row = _record_by_resource_external_id(
        ctx.session,
        project_id=inp.project_id,
        resource_key="communication-profile",
        external_id=_communication_profile_ref(inp.key),
    )
    if row is None:
        raise ValidationError("communication profile was not found")
    return _communication_profile_out(ctx.session, row.id, row.project_id, row.data_json or {})


async def communication_profile_list(
    inp: CommunicationProfileListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> Page[CommunicationProfileOut]:
    _require_project(ctx.session, inp.project_id)
    records = ResourceRepository(ctx.session).query_records(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="communication-profile",
        limit=inp.limit,
        after_id=inp.after_id,
    )
    return Page(
        items=[
            _communication_profile_out(
                ctx.session,
                record.id,
                record.project_id,
                record.data_json or {},
            )
            for record in records.items
        ],
        next_cursor=records.next_cursor,
        total_estimate=records.total_estimate,
    )
