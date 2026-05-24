"""Agent-facing tool profile resolution operations."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from stackos.artifacts import redact_secret_text, redact_secrets
from stackos.auth_providers import AuthRepository, AuthStatusOut, CredentialConnectionOut
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
    auth_profile_key: str | None = Field(
        default=None,
        description=(
            "Credential profile key. When omitted, StackOS uses the resolved tool profile's "
            "auth_profile_key or the provider default."
        ),
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
    project_id: int | None
    provider_key: str
    auth_type: str
    auth_method_key: str
    profile_key: str
    label: str | None = None
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
    auth_profile_key: str | None = None
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
    auth_profile_key = _clean(inp.auth_profile_key)

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
            profile_auth = _clean(profile.auth_profile_key)
            if auth_profile_key is not None and profile_auth != auth_profile_key:
                raise ValidationError(
                    "auth_profile_key does not match the resolved tool profile",
                    data={
                        "provider_key": inp.provider_key,
                        "tool_profile_key": profile.key,
                        "profile_auth_profile_key": profile_auth,
                        "requested_auth_profile_key": auth_profile_key,
                    },
                )
            auth_profile_key = profile_auth
    elif _clean(inp.tool_profile_key) is not None:
        warnings.append(
            "provider has no project-scoped tool profile resolver; using credential profile only"
        )

    credential = _resolve_credential(
        status=status,
        provider_auth_type=provider.auth_type,
        provider_key=inp.provider_key,
        auth_profile_key=auth_profile_key,
        credential_ref=_clean(inp.credential_ref),
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
        auth_profile_key=str(data.get("auth_profile_key") or "default"),
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
    provider_auth_type: str,
    provider_key: str,
    auth_profile_key: str | None,
    credential_ref: str | None,
    warnings: list[str],
    missing: list[str],
) -> ToolProfileCredentialOut | None:
    if provider_auth_type in _NO_AUTH_TYPES:
        return None

    connections = [
        connection for connection in status.connections if connection.provider_key == provider_key
    ]
    if credential_ref is not None:
        for connection in connections:
            if connection.credential_ref == credential_ref:
                if auth_profile_key is not None and connection.profile_key != auth_profile_key:
                    raise ValidationError(
                        "credential_ref does not match the requested auth profile",
                        data={
                            "provider_key": provider_key,
                            "credential_ref": credential_ref,
                            "credential_profile_key": connection.profile_key,
                            "requested_auth_profile_key": auth_profile_key,
                        },
                    )
                return _credential_out(connection, missing=missing)
        missing.append("credential_ref")
        warnings.append("credential_ref was not found for this provider/project")
        return None

    selected_profile_key = auth_profile_key or "default"
    for connection in connections:
        if connection.profile_key == selected_profile_key:
            return _credential_out(connection, missing=missing)

    connected = [
        connection
        for connection in connections
        if connection.status == "connected" and not connection.setup_required
    ]
    if auth_profile_key is None and len(connected) == 1:
        warnings.append("auth_profile_key omitted; selected the only connected credential")
        return _credential_out(connected[0], missing=missing)

    missing.append("credential")
    if connections:
        warnings.append(
            f"no credential profile {selected_profile_key!r} is connected for {provider_key}"
        )
    else:
        warnings.append(f"no {provider_key} credential is connected")
    return None


def _credential_out(
    connection: CredentialConnectionOut,
    *,
    missing: list[str],
) -> ToolProfileCredentialOut:
    if connection.status != "connected" or connection.setup_required:
        missing.append("credential_connected")
    return ToolProfileCredentialOut(
        credential_ref=connection.credential_ref,
        project_id=connection.project_id,
        provider_key=connection.provider_key,
        auth_type=connection.auth_type,
        auth_method_key=connection.auth_method_key,
        profile_key=connection.profile_key,
        label=connection.label,
        status=connection.status,
        setup_required=connection.setup_required,
        account=connection.account,
        scopes=connection.scopes,
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
                "Resolve one provider credential profile before a direct action.run call.",
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
                    title="Resolve SMTP credential profile",
                    arguments={
                        "project_id": 1,
                        "provider_key": "smtp",
                        "auth_profile_key": "primary",
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
