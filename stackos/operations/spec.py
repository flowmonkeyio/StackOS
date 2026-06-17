"""Protocol-neutral StackOS operation contracts.

Operations are the callable contract above repositories/connectors and below
MCP, REST, and CLI adapters. A single ``OperationSpec`` owns the schema,
handler, access policy, and agent-facing guidance for one StackOS callable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, verb_is_mutating
from stackos.mcp.streaming import ProgressEmitter

OperationHandler = Callable[[Any, MCPContext, ProgressEmitter], Awaitable[Any]]
ResponseMode = Literal["compact", "raw", "ack"]

_RAW_ONLY_RESPONSE_OPERATIONS = frozenset(
    {
        "communication.reply",
        "communication.send",
    }
)

_ACK_UNSAFE_RESPONSE_OPERATIONS = _RAW_ONLY_RESPONSE_OPERATIONS | frozenset(
    {
        "agentRequest.claim",
        "agentRequest.prepareRunPlan",
        "action.execute",
        "action.run",
        "auth.start",
        "auth.test",
        "readiness.check",
        "runPlan.claimStep",
        "runPlan.create",
        "runPlan.start",
        "toolProfile.resolve",
        "workspace.bootstrap",
        "workspace.connect",
        "workspace.resolve",
        "workspace.startSession",
    }
)


@dataclass(frozen=True)
class OperationSurface:
    """One adapter exposure for an operation."""

    enabled: bool = False
    command: str | None = None
    path: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class OperationSurfaces:
    """Surface matrix for one operation."""

    mcp: OperationSurface = field(default_factory=OperationSurface)
    rest: OperationSurface = field(default_factory=OperationSurface)
    cli: OperationSurface = field(default_factory=OperationSurface)

    def is_enabled(self, surface: str) -> bool:
        row = getattr(self, surface, None)
        return isinstance(row, OperationSurface) and row.enabled


@dataclass(frozen=True)
class OperationExample:
    """Agent-friendly example payload."""

    title: str
    arguments: dict[str, Any]
    notes: str | None = None


@dataclass(frozen=True)
class OperationResponsePolicy:
    """Agent-facing response-shaping policy for one operation."""

    default_mode: ResponseMode = "compact"
    allowed_modes: tuple[ResponseMode, ...] = ("compact", "raw")
    ack_safe: bool = False
    raw_only_reason: str | None = None
    compact_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class OperationSpec:
    """Single source of truth for a StackOS callable operation."""

    name: str
    summary: str
    input_model: type[MCPInput]
    output_model: Any
    handler: OperationHandler
    surfaces: OperationSurfaces
    purpose: str
    when_to_use: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    returns: tuple[str, ...] = ()
    examples: tuple[OperationExample, ...] = ()
    mutating: bool | None = None
    grant_policy: str = "tool-grant"
    secret_policy: str = "no-secret-output"
    category: str | None = None
    response_policy: OperationResponsePolicy | None = None

    @property
    def read_only(self) -> bool:
        if self.mutating is not None:
            return not self.mutating
        return not verb_is_mutating(self.name)

    @property
    def mcp_description(self) -> str:
        return self.summary

    @property
    def category_name(self) -> str:
        return self.category or _category_for_operation_name(self.name)

    @property
    def effective_response_policy(self) -> OperationResponsePolicy:
        if self.response_policy is not None:
            return self.response_policy
        return _default_response_policy(self)

    def summary_out(self) -> OperationSummaryOut:
        return OperationSummaryOut(
            name=self.name,
            category=self.category_name,
            summary=self.summary,
            read_only=self.read_only,
            mutating=not self.read_only,
            surfaces=_surfaces_out(self.surfaces),
            grant_policy=self.grant_policy,
            secret_policy=self.secret_policy,
            response_policy=_response_policy_out(self.effective_response_policy),
        )

    def describe_out(self) -> OperationDescribeOut:
        summary = self.summary_out()
        return OperationDescribeOut(
            **summary.model_dump(mode="python"),
            purpose=self.purpose,
            when_to_use=list(self.when_to_use),
            prerequisites=list(self.prerequisites),
            returns=list(self.returns),
            input_schema=_input_schema_for_response_policy(
                _schema_for(self.input_model),
                self.effective_response_policy,
            ),
            output_schema=_schema_for(self.output_model),
            examples=[
                OperationExampleOut(title=item.title, arguments=item.arguments, notes=item.notes)
                for item in self.examples
            ],
        )


class OperationSurfaceOut(BaseModel):
    enabled: bool
    command: str | None = None
    path: str | None = None
    notes: str | None = None


class OperationExampleOut(BaseModel):
    title: str
    arguments: dict[str, Any]
    notes: str | None = None


class OperationResponsePolicyOut(BaseModel):
    default_mode: ResponseMode
    allowed_modes: list[ResponseMode]
    ack_safe: bool
    raw_only_reason: str | None = None
    compact_notes: list[str] = Field(default_factory=list)


class OperationSummaryOut(BaseModel):
    name: str
    category: str
    summary: str
    read_only: bool
    mutating: bool
    surfaces: dict[str, OperationSurfaceOut]
    grant_policy: str
    secret_policy: str
    response_policy: OperationResponsePolicyOut


class OperationDescribeOut(OperationSummaryOut):
    purpose: str
    when_to_use: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    returns: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    examples: list[OperationExampleOut] = Field(default_factory=list)


class OperationGroupOut(BaseModel):
    category: str
    count: int
    operation_names: list[str] = Field(default_factory=list)


class OperationListOut(BaseModel):
    items: list[OperationSummaryOut]
    groups: list[OperationGroupOut] = Field(default_factory=list)


_OPERATION_CATEGORY_PREFIXES: tuple[tuple[str, str], ...] = (
    ("action.", "actions"),
    ("agentPreset.", "agents"),
    ("agentRequest.", "agents"),
    ("skillPreset.", "agents"),
    ("localAgentChat.", "agents"),
    ("auth.", "auth"),
    ("budget.", "auth"),
    ("cost.", "auth"),
    ("toolProfile.", "auth"),
    ("communication", "communications"),
    ("ingressEndpoint.", "communications"),
    ("workspace.", "setup"),
    ("project.", "setup"),
    ("plugin.", "catalog"),
    ("catalog.", "catalog"),
    ("capability.", "catalog"),
    ("provider.", "catalog"),
    ("meta.", "catalog"),
    ("operation.", "operations"),
    ("workflowExtension.", "workflow"),
    ("workflowTemplate.", "workflow"),
    ("runPlan.", "workflow"),
    ("run.", "workflow"),
    ("tracker.", "tracker"),
    ("resource.", "resources"),
    ("artifact.", "resources"),
    ("context.", "resources"),
    ("learning.", "resources"),
    ("experiment.", "resources"),
    ("decision.", "resources"),
    ("schedule.", "system"),
    ("sitemap.", "system"),
)


def _category_for_operation_name(name: str) -> str:
    for prefix, category in _OPERATION_CATEGORY_PREFIXES:
        if name.startswith(prefix):
            return category
    return "system"


def _surfaces_out(surfaces: OperationSurfaces) -> dict[str, OperationSurfaceOut]:
    return {
        key: OperationSurfaceOut(
            enabled=value.enabled,
            command=value.command,
            path=value.path,
            notes=value.notes,
        )
        for key, value in {
            "mcp": surfaces.mcp,
            "rest": surfaces.rest,
            "cli": surfaces.cli,
        }.items()
    }


def _default_response_policy(spec: OperationSpec) -> OperationResponsePolicy:
    if spec.name in _RAW_ONLY_RESPONSE_OPERATIONS:
        return OperationResponsePolicy(
            default_mode="raw",
            allowed_modes=("raw",),
            ack_safe=False,
            raw_only_reason=(
                "Provider/external side-effect operations keep full redacted output so "
                "agents retain provider ids, delivery state, partial failures, and retry "
                "safety context."
            ),
            compact_notes=(
                "Do not compact provider side effects until the action contract defines "
                "a provider-safe compact shape.",
            ),
        )
    ack_safe = not spec.read_only and spec.name not in _ACK_UNSAFE_RESPONSE_OPERATIONS
    allowed: tuple[ResponseMode, ...] = (
        ("compact", "raw", "ack")
        if ack_safe
        else (
            "compact",
            "raw",
        )
    )
    return OperationResponsePolicy(
        default_mode="compact",
        allowed_modes=allowed,
        ack_safe=ack_safe,
        compact_notes=(
            "Compact responses must keep the ids, refs, counts, warnings, and next-step "
            "fields an agent needs for the next safe tool call.",
        ),
    )


def _response_policy_out(policy: OperationResponsePolicy) -> OperationResponsePolicyOut:
    return OperationResponsePolicyOut(
        default_mode=policy.default_mode,
        allowed_modes=list(policy.allowed_modes),
        ack_safe=policy.ack_safe,
        raw_only_reason=policy.raw_only_reason,
        compact_notes=list(policy.compact_notes),
    )


def _input_schema_for_response_policy(
    schema: dict[str, Any],
    policy: OperationResponsePolicy,
) -> dict[str, Any]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return schema
    response_mode = properties.get("response_mode")
    if not isinstance(response_mode, dict):
        return schema
    response_mode["enum"] = _response_mode_schema_values(policy)
    response_mode["default"] = policy.default_mode
    response_mode["description"] = (
        "Agent response shape for this operation. compact returns next-call-sufficient "
        "refs, raw returns the full redacted payload, and ack returns a minimal success "
        "envelope when the operation is ack-safe. standard and verbose alias raw."
    )
    return schema


def _response_mode_schema_values(policy: OperationResponsePolicy) -> list[str]:
    values: list[str] = []
    if "compact" in policy.allowed_modes:
        values.append("compact")
    if "raw" in policy.allowed_modes:
        values.extend(["raw", "standard", "verbose"])
    if "ack" in policy.allowed_modes:
        values.append("ack")
    return values


def _schema_for(model: Any) -> dict[str, Any]:
    if isinstance(model, type) and issubclass(model, BaseModel):
        return model.model_json_schema(mode="serialization")
    return TypeAdapter(model).json_schema(mode="serialization")


__all__ = [
    "OperationDescribeOut",
    "OperationExample",
    "OperationExampleOut",
    "OperationGroupOut",
    "OperationHandler",
    "OperationListOut",
    "OperationResponsePolicy",
    "OperationResponsePolicyOut",
    "OperationSpec",
    "OperationSummaryOut",
    "OperationSurface",
    "OperationSurfaceOut",
    "OperationSurfaces",
    "ResponseMode",
]
