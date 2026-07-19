"""Public output contracts for execution contexts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExecutionContextLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    context_id: int
    link_type: str
    link_ref: str
    role: str
    metadata_json: dict[str, Any] | None = None
    created_at: datetime


class ExecutionContextOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    context_ref: str
    name: str
    description: str
    plugin_slug: str | None = None
    provider_key: str | None = None
    action_ref: str | None = None
    credential_ref: str | None = None
    credential_locked: bool
    provider_context_json: dict[str, Any] = Field(default_factory=dict)
    provider_context_locked_fields_json: list[str] = Field(default_factory=list)
    output_policy_json: dict[str, Any] = Field(default_factory=dict)
    request_budget_json: dict[str, Any] = Field(default_factory=dict)
    artifact_namespace: str | None = None
    status: str
    metadata_json: dict[str, Any] | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    links: list[ExecutionContextLinkOut] = Field(default_factory=list)
    artifact_count: int = 0


class ExecutionContextArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    context_id: int
    context_ref: str
    artifact_id: int
    action_call_id: int | None = None
    semantic_name: str | None = None
    action_ref: str | None = None
    input_hash: str | None = None
    metadata_json: dict[str, Any] | None = None
    created_at: datetime
    artifact: dict[str, Any] = Field(default_factory=dict)


class ExecutionContextResolveOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context: ExecutionContextOut
    action_ref: str | None = None
    compatible: bool
    issues: list[dict[str, Any]] = Field(default_factory=list)
    credential_ref: str | None = None
    provider_context_json: dict[str, Any] = Field(default_factory=dict)
    provider_context_schema_json: dict[str, Any] = Field(default_factory=dict)
    provider_context_schema_source: str | None = None
    output_policy_json: dict[str, Any] = Field(default_factory=dict)
    request_budget_json: dict[str, Any] = Field(default_factory=dict)
    artifact_namespace: str | None = None
    next_call: dict[str, Any] = Field(default_factory=dict)


class ExecutionContextDiscoveryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int
    filters_json: dict[str, Any] = Field(default_factory=dict)
    context_refs: list[str] = Field(default_factory=list)
    items: list[ExecutionContextOut] = Field(default_factory=list)
    next_cursor: int | None = None
    total_estimate: int = 0
    next_calls: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "ExecutionContextArtifactOut",
    "ExecutionContextDiscoveryOut",
    "ExecutionContextLinkOut",
    "ExecutionContextOut",
    "ExecutionContextResolveOut",
]
