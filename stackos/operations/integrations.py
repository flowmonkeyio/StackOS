"""Project-scoped integration inventory operations."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, select

from stackos.action_availability import ActionExposureNextActionOut, integration_setup_url
from stackos.db.models import Credential
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations._helpers import operation_spec
from stackos.operations.spec import OperationExample, OperationSpec
from stackos.repositories.plugins import ActionOut, PluginRepository, ProviderOut


class IntegrationListInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"project_id": 1, "plugin_slug": "communications"},
        },
    )

    project_id: int | None = None
    plugin_slug: str | None = None
    provider_key: str | None = None
    include_unavailable: bool = Field(
        default=True,
        description=(
            "When true, include disconnected providers and their hidden actions. Set false "
            "for a compact current-ready integration view."
        ),
    )
    include_actions: bool = Field(
        default=False,
        description="Include compact per-action exposure summaries for each integration.",
    )


class IntegrationActionSummaryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_ref: str
    name: str
    operation: str
    risk_level: str
    executable: bool
    availability_status: str
    exposure_state: str
    visible_by_default: bool
    hidden_reason: str | None = None


class IntegrationListItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_slug: str
    provider_key: str
    name: str
    description: str
    auth_type: str
    state: str
    connected: bool
    credential_count: int = 0
    connected_credential_count: int = 0
    total_action_count: int = 0
    exposed_action_count: int = 0
    executable_action_count: int = 0
    hidden_action_count: int = 0
    required_action_count: int = 0
    optional_action_count: int = 0
    next_action: ActionExposureNextActionOut | None = None
    actions: list[IntegrationActionSummaryOut] = Field(default_factory=list)


class IntegrationListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int
    items: list[IntegrationListItemOut]
    count: int
    connected_count: int = 0
    ready_count: int = 0
    exposed_action_count: int = 0
    executable_action_count: int = 0
    hidden_action_count: int = 0
    filters: dict[str, Any] = Field(default_factory=dict)


async def integration_list(
    inp: IntegrationListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> IntegrationListOut:
    from stackos.repositories.base import ValidationError

    project_id = inp.project_id if inp.project_id is not None else ctx.project_id
    if project_id is None:
        raise ValidationError(
            "project_id is required unless the agent bridge resolved the workspace project"
        )

    repo = PluginRepository(ctx.session)
    providers = repo.list_providers(plugin_slug=inp.plugin_slug, project_id=project_id)
    if inp.provider_key is not None:
        providers = [provider for provider in providers if provider.key == inp.provider_key]
    actions = repo.list_actions(plugin_slug=inp.plugin_slug, project_id=project_id)
    action_groups = _actions_by_provider(actions)
    credential_groups = _credentials_by_provider(ctx.session, project_id=project_id)

    items: list[IntegrationListItemOut] = []
    for provider in providers:
        provider_actions = action_groups.get((provider.plugin_slug, provider.key), [])
        item = _integration_item(
            provider,
            actions=provider_actions,
            credentials=credential_groups.get(provider.key, []),
            project_id=project_id,
            include_actions=inp.include_actions,
        )
        if inp.include_unavailable or item.connected or item.exposed_action_count > 0:
            items.append(item)

    return IntegrationListOut(
        project_id=project_id,
        items=items,
        count=len(items),
        connected_count=sum(1 for item in items if item.connected),
        ready_count=sum(
            1 for item in items if item.state in {"connected", "ready_no_connection_required"}
        ),
        exposed_action_count=sum(item.exposed_action_count for item in items),
        executable_action_count=sum(item.executable_action_count for item in items),
        hidden_action_count=sum(item.hidden_action_count for item in items),
        filters={
            key: value
            for key, value in {
                "project_id": project_id,
                "plugin_slug": inp.plugin_slug,
                "provider_key": inp.provider_key,
                "include_unavailable": inp.include_unavailable,
                "include_actions": inp.include_actions,
            }.items()
            if value is not None
        },
    )


def _actions_by_provider(actions: list[ActionOut]) -> dict[tuple[str, str], list[ActionOut]]:
    grouped: dict[tuple[str, str], list[ActionOut]] = defaultdict(list)
    for action in actions:
        if action.provider_key is None:
            continue
        grouped[(action.plugin_slug, action.provider_key)].append(action)
    return dict(grouped)


def _credentials_by_provider(session: Session, *, project_id: int) -> dict[str, list[Credential]]:
    rows = session.exec(
        select(Credential)
        .where(
            col(Credential.revoked_at).is_(None),
            (col(Credential.project_id) == project_id) | col(Credential.project_id).is_(None),
        )
        .order_by(
            col(Credential.provider_key).asc(),
            col(Credential.project_id).desc(),
            col(Credential.created_at).desc(),
        )
    ).all()
    grouped: dict[str, list[Credential]] = defaultdict(list)
    for row in rows:
        grouped[row.provider_key].append(row)
    return dict(grouped)


def _integration_item(
    provider: ProviderOut,
    *,
    actions: list[ActionOut],
    credentials: list[Credential],
    project_id: int,
    include_actions: bool,
) -> IntegrationListItemOut:
    connected_credentials = [row for row in credentials if row.status == "connected"]
    exposed_actions = [row for row in actions if row.exposure.visible_by_default]
    executable_actions = [row for row in actions if row.availability.executable]
    hidden_actions = [row for row in actions if not row.exposure.visible_by_default]
    required_actions = [row for row in actions if row.exposure.requires_integration]
    optional_actions = [row for row in actions if not row.exposure.requires_integration]

    state = _integration_state(
        provider=provider,
        connected=bool(connected_credentials),
        actions=actions,
        exposed_actions=exposed_actions,
        executable_actions=executable_actions,
        hidden_actions=hidden_actions,
    )
    next_action = None
    if state in {"not_connected", "partially_available"}:
        next_action = ActionExposureNextActionOut(
            tool="auth.status",
            reason=f"Connect provider {provider.key!r} before its required actions appear.",
            arguments={"project_id": project_id, "provider_key": provider.key},
            ui_url=integration_setup_url(project_id, provider.key),
        )
    elif state == "provider_disabled":
        next_action = ActionExposureNextActionOut(
            tool="provider.describe",
            reason=f"Provider {provider.key!r} is disabled in the plugin manifest.",
            arguments={
                "project_id": project_id,
                "plugin_slug": provider.plugin_slug,
                "provider_key": provider.key,
            },
        )

    return IntegrationListItemOut(
        plugin_slug=provider.plugin_slug,
        provider_key=provider.key,
        name=provider.name,
        description=provider.description,
        auth_type=provider.auth_type,
        state=state,
        connected=bool(connected_credentials),
        credential_count=len(credentials),
        connected_credential_count=len(connected_credentials),
        total_action_count=len(actions),
        exposed_action_count=len(exposed_actions),
        executable_action_count=len(executable_actions),
        hidden_action_count=len(hidden_actions),
        required_action_count=len(required_actions),
        optional_action_count=len(optional_actions),
        next_action=next_action,
        actions=[_action_summary(row) for row in actions] if include_actions else [],
    )


def _integration_state(
    *,
    provider: ProviderOut,
    connected: bool,
    actions: list[ActionOut],
    exposed_actions: list[ActionOut],
    executable_actions: list[ActionOut],
    hidden_actions: list[ActionOut],
) -> str:
    if isinstance(provider.config_json, dict) and provider.config_json.get("enabled") is False:
        return "provider_disabled"
    if connected:
        if exposed_actions and not executable_actions:
            return "connected_blocked"
        return "connected"
    if hidden_actions and exposed_actions:
        return "partially_available"
    if hidden_actions:
        return "not_connected"
    if actions and exposed_actions:
        return "ready_no_connection_required"
    return "available"


def _action_summary(row: ActionOut) -> IntegrationActionSummaryOut:
    return IntegrationActionSummaryOut(
        action_ref=row.action_ref,
        name=row.name,
        operation=row.operation,
        risk_level=row.risk_level,
        executable=row.availability.executable,
        availability_status=row.availability.status,
        exposure_state=row.exposure.state,
        visible_by_default=row.exposure.visible_by_default,
        hidden_reason=row.exposure.hidden_reason,
    )


def operation_specs() -> list[OperationSpec]:
    return [
        operation_spec(
            name="integration.list",
            summary="List project integration readiness without broad credential noise.",
            input_model=IntegrationListInput,
            output_model=IntegrationListOut,
            handler=integration_list,
            purpose=(
                "Use this when an agent needs a compact project integration inventory: "
                "connected providers, hidden action counts, executable action counts, "
                "and next setup action."
            ),
            when_to_use=(
                "First-run setup needs to know which provider integrations exist.",
                "An agent wants scoped integration readiness before choosing provider actions.",
                "A setup UI needs hidden disconnected-action counts without listing every action.",
            ),
            prerequisites=(
                "Pass project_id unless the StackOS bridge has resolved the workspace project.",
                (
                    "Use action.list for usable action discovery; use readiness.check for "
                    "one workflow/action."
                ),
            ),
            returns=(
                (
                    "Provider/plugin integration rows with connected, hidden, exposed, "
                    "and executable counts."
                ),
                "Safe next_action repair hints and local setup URLs; never plaintext secrets.",
            ),
            examples=(
                OperationExample(
                    title="List all project integrations",
                    arguments={"project_id": 1},
                ),
                OperationExample(
                    title="Inspect communication integrations with action summaries",
                    arguments={
                        "project_id": 1,
                        "plugin_slug": "communications",
                        "include_actions": True,
                    },
                ),
                OperationExample(
                    title="Only current ready integrations",
                    arguments={"project_id": 1, "include_unavailable": False},
                ),
            ),
            grant_policy="direct-read",
            mutating=False,
            category="setup",
        )
    ]


__all__ = [
    "IntegrationActionSummaryOut",
    "IntegrationListInput",
    "IntegrationListItemOut",
    "IntegrationListOut",
    "integration_list",
    "operation_specs",
]
