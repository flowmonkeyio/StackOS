"""Agent-facing tool profile resolution operations."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from stackos.artifacts import redact_secret_text, redact_secrets
from stackos.auth_providers import AccountOut, AuthRepository, AuthStatusOut
from stackos.communications import merged_provider_profile
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.spec import (
    OperationExample,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.base import Page, ValidationError
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.resources import ResourceRecordOut, ResourceRepository

_NO_AUTH_TYPES = {"none", "local"}
_COMMUNICATION_PROFILE = "communication-profile"


class ToolProfileResolveInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "project_id": 1,
                "provider_key": "telegram-bot",
                "tool_profile_key": "support-bot",
            }
        },
    )

    project_id: int
    provider_key: str = Field(min_length=1, max_length=160)
    tool_profile_key: str | None = Field(
        default=None,
        description=("Provider-specific project profile key, such as a communication profile key."),
    )
    credential_ref: str | None = Field(
        default=None,
        description=(
            "Optional exact opaque credential ref to validate against the provider/profile."
        ),
    )
    intent: Literal["execute", "setup", "diagnose"] = "execute"


class ToolProfileProviderOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_key: str
    plugin_slug: str | None = None
    name: str
    auth_type: str
    setup_required: bool


class ToolProfileCredentialOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_ref: str
    project_id: int
    provider_key: str
    display_name: str
    auth_type: str
    auth_method_key: str
    status: str
    setup_required: bool
    account: dict[str, Any] | None = None
    scopes: list[str] = Field(default_factory=list)


class ToolProfileOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    key: str
    ref: str
    record_id: int | None = None
    enabled: bool = True
    credential_ref: str | None = None
    identity: dict[str, Any] = Field(default_factory=dict)
    agent_guidance: dict[str, Any] = Field(default_factory=dict)
    access_policy: dict[str, Any] = Field(default_factory=dict)
    trigger_policy: dict[str, Any] = Field(default_factory=dict)
    context_policy: dict[str, Any] = Field(default_factory=dict)
    response_policy: dict[str, Any] = Field(default_factory=dict)
    refs: dict[str, str] = Field(default_factory=dict)


class ToolProfileResolveOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int
    provider_key: str
    intent: str
    ready: bool
    provider: ToolProfileProviderOut | None = None
    tool_profile: ToolProfileOut | None = None
    credential: ToolProfileCredentialOut | None = None
    missing: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_action: str | None = None


async def tool_profile_resolve(
    inp: ToolProfileResolveInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ToolProfileResolveOut:
    """Resolve the safe execution target tuple for one provider/tool profile."""

    ProjectRepository(ctx.session).get(inp.project_id)
    status = AuthRepository(ctx.session).status(
        project_id=inp.project_id,
        provider_key=inp.provider_key,
    )
    provider = _provider_out(status)
    if provider is None:
        raise ValidationError(
            "provider is not registered",
            data={"provider_key": inp.provider_key},
        )

    missing: list[str] = []
    warnings: list[str] = []
    profile: ToolProfileOut | None = None
    credential_ref = _clean(inp.credential_ref)

    if inp.provider_key == "telegram-bot":
        profile, profile_missing, profile_warnings = _resolve_telegram_profile(
            ResourceRepository(ctx.session).query_records(
                project_id=inp.project_id,
                plugin_slug="communications",
                resource_key=_COMMUNICATION_PROFILE,
                limit=100,
            ),
            requested_key=_clean(inp.tool_profile_key),
        )
        missing.extend(profile_missing)
        warnings.extend(profile_warnings)
        if profile is not None:
            profile_account_ref = _clean(profile.credential_ref)
            if credential_ref is not None and profile_account_ref != credential_ref:
                raise ValidationError(
                    "credential_ref does not match the resolved tool profile",
                    data={
                        "provider_key": inp.provider_key,
                        "tool_profile_key": profile.key,
                        "profile_credential_ref": profile_account_ref,
                        "requested_credential_ref": credential_ref,
                    },
                )
            credential_ref = profile_account_ref
    elif _clean(inp.tool_profile_key) is not None:
        warnings.append(
            "provider has no project-scoped tool profile resolver; using attached Accounts only"
        )

    credential = _resolve_credential(
        status=status,
        project_id=inp.project_id,
        provider_auth_type=provider.auth_type,
        provider_key=inp.provider_key,
        credential_ref=credential_ref,
        warnings=warnings,
        missing=missing,
    )

    if provider.auth_type in _NO_AUTH_TYPES:
        provider.setup_required = False
    else:
        provider.setup_required = (
            credential is None or credential.setup_required or credential.status != "connected"
        )

    if profile is not None and not profile.enabled:
        missing.append("tool_profile_enabled")
        warnings.append("resolved tool profile is disabled")

    ready = (
        not missing
        and (provider.auth_type in _NO_AUTH_TYPES or credential is not None)
        and (credential is None or credential.status == "connected")
        and (profile is None or profile.enabled)
    )
    return ToolProfileResolveOut(
        project_id=inp.project_id,
        provider_key=inp.provider_key,
        intent=inp.intent,
        ready=ready,
        provider=provider,
        tool_profile=profile,
        credential=credential,
        missing=_dedupe(missing),
        warnings=_dedupe(warnings),
        next_action=_next_action(
            project_id=inp.project_id,
            provider_key=inp.provider_key,
            missing=missing,
            profile=profile,
        ),
    )


def _provider_out(status: AuthStatusOut) -> ToolProfileProviderOut | None:
    if not status.providers:
        return None
    provider = status.providers[0]
    return ToolProfileProviderOut(
        provider_key=provider.key,
        plugin_slug=provider.plugin_slug,
        name=provider.name,
        auth_type=provider.auth_type,
        setup_required=provider.auth_type not in _NO_AUTH_TYPES,
    )


def _resolve_telegram_profile(
    page: Page[ResourceRecordOut],
    *,
    requested_key: str | None,
) -> tuple[ToolProfileOut | None, list[str], list[str]]:
    profiles = [
        profile for record in page.items if (profile := _telegram_profile_out(record)) is not None
    ]
    if requested_key is not None:
        for profile in profiles:
            if profile.key == requested_key:
                return profile, [], []
        return (
            None,
            ["tool_profile"],
            [f"telegram communication profile {requested_key!r} was not found"],
        )
    enabled = [profile for profile in profiles if profile.enabled]
    if len(enabled) == 1:
        return enabled[0], [], []
    if not profiles:
        return (
            None,
            ["tool_profile"],
            ["telegram-bot requires a communication profile with a telegram-bot facet"],
        )
    return (
        None,
        ["tool_profile_key"],
        ["multiple Telegram communication profiles exist; pass tool_profile_key"],
    )


def _telegram_profile_out(record: ResourceRecordOut) -> ToolProfileOut | None:
    data = merged_provider_profile(record.data_json or {}, "telegram-bot")
    if not data.get("provider_facets", {}).get("telegram-bot"):
        return None
    key = str(data.get("key") or record.title or "").strip()
    return ToolProfileOut(
        kind=_COMMUNICATION_PROFILE,
        key=key,
        ref=f"communication-profile:{key}" if key else str(record.external_id or ""),
        record_id=record.id,
        enabled=bool(data.get("enabled", True)),
        credential_ref=_clean(
            str(data.get("credential_ref")) if data.get("credential_ref") is not None else None
        ),
        identity=_safe_dict(data.get("identity")),
        agent_guidance=_safe_dict(data.get("agent_guidance")),
        access_policy=_safe_dict(data.get("access_policy")),
        trigger_policy=_safe_dict(data.get("trigger_policy")),
        context_policy=_safe_dict(data.get("context_policy")),
        response_policy=_safe_dict(data.get("response_policy")),
        refs={
            str(k): str(v)
            for k, v in _safe_dict(data.get("refs")).items()
            if str(k) not in {"credential_ref", "bot_token", "webhook_secret_token"}
        },
    )


def _resolve_credential(
    *,
    status: AuthStatusOut,
    project_id: int,
    provider_auth_type: str,
    provider_key: str,
    credential_ref: str | None,
    warnings: list[str],
    missing: list[str],
) -> ToolProfileCredentialOut | None:
    if provider_auth_type in _NO_AUTH_TYPES:
        return None

    accounts = [account for account in status.accounts if account.provider_key == provider_key]
    if credential_ref is not None:
        for account in accounts:
            if account.credential_ref == credential_ref:
                return _credential_out(
                    account,
                    project_id=project_id,
                    missing=missing,
                )
        missing.append("credential_ref")
        warnings.append("credential_ref is not attached to this provider/project")
        return None

    connected = [
        account
        for account in accounts
        if account.status == "connected" and not account.setup_required
    ]
    if len(connected) == 1:
        warnings.append("credential_ref omitted; selected the only connected Account")
        return _credential_out(
            connected[0],
            project_id=project_id,
            missing=missing,
        )

    missing.append("credential")
    if len(connected) > 1:
        warnings.append(f"multiple {provider_key} Accounts are attached; pass credential_ref")
    elif accounts:
        warnings.append(f"no attached {provider_key} Account is connected")
    else:
        warnings.append(f"no {provider_key} Account is attached")
    return None


def _credential_out(
    account: AccountOut,
    *,
    project_id: int,
    missing: list[str],
) -> ToolProfileCredentialOut:
    if account.status != "connected" or account.setup_required:
        missing.append("credential_connected")
    return ToolProfileCredentialOut(
        credential_ref=account.credential_ref,
        project_id=project_id,
        provider_key=account.provider_key,
        display_name=account.display_name,
        auth_type=account.auth_type,
        auth_method_key=account.auth_method_key,
        status=account.status,
        setup_required=account.setup_required,
        account=account.account,
        scopes=account.scopes,
    )


def _next_action(
    *,
    project_id: int,
    provider_key: str,
    missing: list[str],
    profile: ToolProfileOut | None,
) -> str | None:
    missing_set = set(missing)
    if "credential" in missing_set or "credential_connected" in missing_set:
        return f"Connect or repair {provider_key} at /projects/{project_id}/connections"
    if "tool_profile" in missing_set:
        return f"Create a project-scoped {provider_key} tool profile before executing actions"
    if "tool_profile_key" in missing_set:
        return (
            f"Pass tool_profile_key; available profile resolution is ambiguous for {provider_key}"
        )
    if profile is not None and not profile.enabled:
        return f"Enable tool profile {profile.key!r} before executing actions"
    return None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _safe_dict(value: Any) -> dict[str, Any]:
    safe = redact_secrets(_dict(value))
    return _redact_text_values(safe) if isinstance(safe, dict) else {}


def _redact_text_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact_text_values(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_redact_text_values(child) for child in value]
    if isinstance(value, str):
        return redact_secret_text(value)
    return value


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _surfaces() -> OperationSurfaces:
    return OperationSurfaces(
        mcp=OperationSurface(enabled=True),
        rest=OperationSurface(enabled=True, path="/api/v1/operations/toolProfile.resolve/call"),
        cli=OperationSurface(enabled=True, command="ops call toolProfile.resolve"),
    )


def operation_specs() -> list[OperationSpec]:
    return [
        OperationSpec(
            name="toolProfile.resolve",
            summary="Resolve one safe provider/tool profile execution target for agents.",
            input_model=ToolProfileResolveInput,
            output_model=ToolProfileResolveOut,
            handler=tool_profile_resolve,
            surfaces=_surfaces(),
            purpose=(
                "Use this before direct action.run or workflow setup when an agent needs the "
                "safe target tuple for a provider: optional project tool profile, daemon-held "
                "credential ref, provider auth status, and next setup action. It never returns "
                "secret payloads and it does not decide business workflow intent."
            ),
            when_to_use=(
                "Resolve Telegram communication profile + credential before sending messages.",
                "Resolve one attached Account before a direct action.run call.",
                "Diagnose missing setup without listing every provider and profile separately.",
            ),
            prerequisites=(
                "Pass project_id and provider_key.",
                (
                    "For providers with semantic project profiles, pass tool_profile_key when "
                    "more than one profile exists."
                ),
                "Pass only credential_ref values returned by StackOS; never pass secret fields.",
            ),
            returns=(
                "ready=true only when the provider/profile/credential tuple can be used.",
                "A safe credential_ref for daemon-side action execution when auth is required.",
                "A concise next_action and missing fields when setup is incomplete.",
            ),
            examples=(
                OperationExample(
                    title="Resolve Telegram bot target",
                    arguments={
                        "project_id": 1,
                        "provider_key": "telegram-bot",
                        "tool_profile_key": "support-bot",
                    },
                ),
                OperationExample(
                    title="Resolve a specific SMTP Account",
                    arguments={
                        "project_id": 1,
                        "provider_key": "smtp",
                        "credential_ref": "cred_...",
                    },
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        )
    ]


__all__ = [
    "ToolProfileCredentialOut",
    "ToolProfileOut",
    "ToolProfileProviderOut",
    "ToolProfileResolveInput",
    "ToolProfileResolveOut",
    "operation_specs",
]
