"""Contracts for scoped action and workflow readiness checks."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from stackos.mcp.contract import MCPInput
from stackos.provider_setup import ProviderSetupOut

ReadinessScope = Literal["action", "workflow"]
ReadinessResponseMode = Literal["compact", "raw", "standard", "verbose"]


class ReadinessCheckInput(MCPInput):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"project_id": 1, "action_ref": "utils.sitemap.fetch"},
                {"project_id": 1, "workflow_key": "engineering.tracked-delivery"},
            ]
        },
    )

    project_id: int | None = None
    action_ref: str | None = None
    plugin_slug: str | None = None
    action_key: str | None = None
    workflow_key: str | None = None
    repo_root: str | None = None
    source: str | None = None
    response_mode: ReadinessResponseMode = Field(
        default="compact",
        description=(
            "compact is the normal agent shape; raw/standard/verbose include more contract detail."
        ),
    )


class ReadinessMissingItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    code: str
    message: str
    required_for: str = "execution"
    action_ref: str | None = None
    action_refs: list[str] = Field(default_factory=list)
    workflow_key: str | None = None
    provider_key: str | None = None
    credential_refs: list[str] = Field(default_factory=list)
    budget_kind: str | None = None
    next_tool: str | None = None
    ui_url: str | None = None
    setup: ProviderSetupOut | None = None


class ReadinessActionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_ref: str
    contract_key: str | None = None
    name: str | None = None
    provider_key: str | None = None
    capability_key: str | None = None
    risk_level: str | None = None
    executable: bool
    availability_status: str
    availability_reasons: list[str] = Field(default_factory=list)
    credential_state: str | None = None
    budget_state: str | None = None
    optional: bool = False
    route_group: str | None = None
    route_key: str | None = None
    setup: ProviderSetupOut | None = None
    missing: list[ReadinessMissingItemOut] = Field(default_factory=list)


class ReadinessRouteOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_group: str
    route_key: str
    executable: bool
    structurally_ready: bool
    action_refs: list[str] = Field(default_factory=list)
    missing: list[ReadinessMissingItemOut] = Field(default_factory=list)


class ReadinessRouteGroupOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_group: str
    required: bool
    execution_ready: bool
    structurally_ready: bool
    available_route_keys: list[str] = Field(default_factory=list)
    routes: list[ReadinessRouteOut] = Field(default_factory=list)


class ReadinessWorkflowOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_key: str
    name: str
    plugin_slug: str | None = None
    action_contract_count: int = 0
    scoped_action_count: int = 0
    required_agent_roles: list[str] = Field(default_factory=list)
    recommended_agent_roles: list[str] = Field(default_factory=list)
    skill_refs: list[str] = Field(default_factory=list)
    prerequisite_count: int = 0
    prerequisites: list[str] = Field(default_factory=list)
    safe_stopping_points: list[str] = Field(default_factory=list)


class ReadinessNextStepOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    reason: str
    arguments: dict[str, object] = Field(default_factory=dict)
    ui_url: str | None = None
    setup: ProviderSetupOut | None = None


class ReadinessCheckOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: ReadinessScope
    project_id: int
    structurally_ready: bool
    context_status: Literal["ready", "not_evaluated", "missing"]
    required_providers_ready: bool
    execution_ready: bool
    missing: list[ReadinessMissingItemOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_steps: list[ReadinessNextStepOut] = Field(default_factory=list)
    action: ReadinessActionOut | None = None
    workflow: ReadinessWorkflowOut | None = None
    actions: list[ReadinessActionOut] = Field(default_factory=list)
    route_groups: list[ReadinessRouteGroupOut] = Field(default_factory=list)
