"""Public action repository response models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stackos.action_availability import ActionAvailabilityOut, ActionExposureOut
from stackos.actions.connectors import ActionValidationIssue
from stackos.actions.manifest import ExecutableActionManifest
from stackos.db.models import ActionCallStatus
from stackos.provider_setup import ProviderSetupOut


class ActionCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    run_id: int | None
    run_plan_id: int | None
    run_plan_step_id: int | None
    action_id: int | None
    credential_id: int | None
    action_key: str
    plugin_slug: str
    provider_key: str | None
    connector_key: str | None
    operation: str
    status: ActionCallStatus
    dry_run: bool
    idempotency_key: str | None
    credential_ref: str | None
    request_json: dict[str, Any] | None
    provider_context_json: dict[str, Any] | None
    response_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    cost_cents: int
    duration_ms: int | None
    error: str | None
    created_at: datetime
    completed_at: datetime | None


class ActionCallAuditOut(BaseModel):
    """Public action-call audit row for UI/read APIs.

    Internal DB identifiers such as ``credential_id`` and replay inputs such as
    ``idempotency_key`` stay out of this shape. The opaque ``credential_ref`` is
    safe to display because it is designed for agent-visible references.
    """

    id: int
    project_id: int
    run_id: int | None
    run_plan_id: int | None
    run_plan_step_id: int | None
    action_key: str
    plugin_slug: str
    provider_key: str | None
    connector_key: str | None
    operation: str
    status: ActionCallStatus
    dry_run: bool
    credential_ref: str | None
    request_json: dict[str, Any] | None
    provider_context_json: dict[str, Any] | None
    response_json: dict[str, Any] | None
    metadata_json: dict[str, Any] | None
    cost_cents: int
    duration_ms: int | None
    error: str | None
    created_at: datetime
    completed_at: datetime | None


class ActionDescribeOut(BaseModel):
    manifest: ExecutableActionManifest
    connector_registered: bool
    execution_available: bool
    agent_execute_available: bool = False
    availability: ActionAvailabilityOut
    exposure: ActionExposureOut
    provider_setup: ProviderSetupOut | None = None
    execution_context: dict[str, Any] = Field(default_factory=dict)


class ActionValidationOut(BaseModel):
    valid: bool
    manifest: ExecutableActionManifest
    issues: list[ActionValidationIssue] = Field(default_factory=list)
    connector_registered: bool
    estimated_cost_cents: int | None = None
    credential_ref: str | None = None


class ActionExecutionOut(BaseModel):
    action_call: ActionCallAuditOut
    output_json: dict[str, Any]
    metadata_json: dict[str, Any] | None = None
    cost_cents: int = 0
    dry_run: bool = False
    replayed: bool = False
    credential_ref: str | None = None
    poll_operation: str | None = None
    poll_arguments: dict[str, Any] | None = None
    next_poll_after_ms: int | None = None
