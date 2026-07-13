"""Workflow template schema for StackOS.

Templates are reusable setup and instruction contracts. They are deliberately
not executable run plans: concrete provider choices, credential refs, payloads,
and action execution state belong to later run-plan objects.
"""

from __future__ import annotations

import re
from typing import Any, Literal

import yaml
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from stackos.artifacts import redact_secret_text

WORKFLOW_TEMPLATE_SCHEMA_VERSION = "stackos.workflow-template.v1"
MAX_TEMPLATE_CONTEXT_ITEMS = 50
ALLOWED_CONTEXT_SOURCES = frozenset(
    {
        "runs",
        "events",
        "index",
        "snapshots",
        "learnings",
        "experiments",
        "decisions",
        "agent_requests",
        "action_calls",
        "metrics",
        "resources",
        "artifacts",
    }
)

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(?:[-.][a-z0-9_]+)*$")
_SKILL_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*(?::[A-Za-z][A-Za-z0-9_.-]*)?$")
_SECRET_KEY_PARTS = (
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
)
_FORBIDDEN_RUNTIME_KEY_PARTS = (
    "ad_account_id",
    "business_decision",
    "campaign_id",
    "creative_id",
    "final_action_payload",
    "payload",
    "provider_object_id",
    "selected_variant",
    "winner",
    "winning_variant",
)
_FORBIDDEN_RUNTIME_TEXT_RE = re.compile(
    r"(?i)\b("
    r"business\s+decision|campaign[_ -]?id|provider\s+object\s+id|"
    r"winner\s+is|winning\s+variant|selected\s+variant|final\s+action\s+payload"
    r")\b"
)


def _validate_key(value: str) -> str:
    if not _KEY_RE.match(value):
        raise ValueError("must be a lowercase snake/kebab/dotted identifier")
    return value


def _validate_ref(value: str) -> str:
    if not _KEY_RE.match(value):
        raise ValueError("must be a lowercase snake/kebab/dotted reference")
    return value


def _is_secret_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def _secret_paths(value: Any, *, path: str = "$") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}"
            if _is_secret_key(key):
                paths.append(child_path)
            paths.extend(_secret_paths(raw_value, path=child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            paths.extend(_secret_paths(item, path=f"{path}[{index}]"))
    elif isinstance(value, str) and redact_secret_text(value) != value:
        paths.append(path)
    return paths


def _runtime_payload_paths(value: Any, *, path: str = "$") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            normalized = key.lower().replace("-", "_")
            child_path = f"{path}.{key}"
            if any(part in normalized for part in _FORBIDDEN_RUNTIME_KEY_PARTS):
                paths.append(child_path)
            paths.extend(_runtime_payload_paths(raw_value, path=child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            paths.extend(_runtime_payload_paths(item, path=f"{path}[{index}]"))
    elif isinstance(value, str) and _FORBIDDEN_RUNTIME_TEXT_RE.search(value):
        paths.append(path)
    return paths


class WorkflowTemplateIssue(BaseModel):
    """Validation issue returned by non-throwing template validation."""

    path: str
    message: str
    code: str = "validation_error"


class TemplateOwnerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    team: str | None = Field(default=None, max_length=200)
    contact: str | None = Field(default=None, max_length=300)


class WorkflowHandoffSpec(BaseModel):
    """Discoverable continuation from one complete workflow to another."""

    model_config = ConfigDict(extra="forbid")

    workflow_key: str = Field(min_length=1, max_length=160)
    relationship: Literal["next", "conditional", "fallback"] = "conditional"
    when: str = Field(min_length=1)

    @field_validator("workflow_key")
    @classmethod
    def _workflow_key(cls, value: str) -> str:
        return _validate_ref(value)


class WorkflowExperienceSpec(BaseModel):
    """Small human-and-agent experience contract for a workflow."""

    model_config = ConfigDict(extra="forbid")

    problem: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    operator_path: list[str] = Field(min_length=1)
    agent_path: list[str] = Field(min_length=1)
    why_ai: str | None = None
    progress_signals: list[str] = Field(default_factory=list)
    recovery: list[str] = Field(default_factory=list)
    safe_stopping_points: list[str] = Field(default_factory=list)
    handoffs: list[WorkflowHandoffSpec] = Field(default_factory=list)


class WorkflowPublicSpec(BaseModel):
    """Reviewed public-catalog copy owned by the workflow package."""

    model_config = ConfigDict(extra="forbid")

    audience: str = Field(min_length=1)
    setup: Literal[
        "available",
        "connection-required",
        "project-adapter-required",
        "mixed",
    ]
    prerequisites: list[str] = Field(default_factory=list)
    proof: list[str] = Field(default_factory=list)
    featured: bool = False


class TemplateBaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=160)
    version: str | None = Field(default=None, max_length=40)
    source: str | None = Field(default=None, max_length=40)
    origin_path: str | None = Field(default=None, max_length=1000)

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        return _validate_key(value)


class TemplateIOSpec(BaseModel):
    """Reusable input/output contract for templates and stages."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    key: str = Field(min_length=1, max_length=160)
    name: str | None = Field(default=None, max_length=200)
    description: str = ""
    type: str = Field(default="object", max_length=80)
    required: bool = False
    default_json: Any | None = Field(
        default=None,
        validation_alias=AliasChoices("default", "default_json"),
    )
    schema_data: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("schema", "schema_json", "schema_data"),
        serialization_alias="schema_json",
    )
    options_json: list[Any] | None = None

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        return _validate_key(value)


class ContextRequirementSpec(BaseModel):
    """Declaration of context the agent should request before planning a run."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1, max_length=160)
    source: str = Field(max_length=80)
    purpose: str = ""
    filters_json: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("filters", "filters_json"),
    )
    fields: list[str] = Field(default_factory=list)
    max_items: int = Field(default=10, ge=1, le=MAX_TEMPLATE_CONTEXT_ITEMS)
    return_mode: str = Field(default="compact", max_length=40)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        return _validate_ref(value)

    @field_validator("source")
    @classmethod
    def _source(cls, value: str) -> str:
        if value not in ALLOWED_CONTEXT_SOURCES:
            raise ValueError(f"unknown context source {value!r}")
        return value


class WorkflowAgentRequirementSpec(BaseModel):
    """Agent role requirement attached to a reusable workflow template."""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1, max_length=160)
    requirement: Literal["required", "recommended", "optional"] = "required"
    agent_preset_ref: str = Field(min_length=1, max_length=160)
    purpose: str = ""
    applies_to_steps: list[str] = Field(default_factory=list)
    handoff_notes: list[str] = Field(default_factory=list)

    @field_validator("role", "agent_preset_ref")
    @classmethod
    def _refs(cls, value: str) -> str:
        return _validate_ref(value)

    @field_validator("applies_to_steps")
    @classmethod
    def _step_refs(cls, value: list[str]) -> list[str]:
        return [_validate_ref(item) for item in value]


class WorkflowSkillRequirementSpec(BaseModel):
    """Host/agent skill guidance recommended for operating a workflow template."""

    model_config = ConfigDict(extra="forbid")

    skill_ref: str = Field(min_length=1, max_length=160)
    requirement: Literal["required", "recommended", "optional"] = "recommended"
    purpose: str = ""
    applies_to_steps: list[str] = Field(default_factory=list)
    setup_notes: list[str] = Field(default_factory=list)

    @field_validator("skill_ref")
    @classmethod
    def _skill_ref(cls, value: str) -> str:
        if not _SKILL_REF_RE.match(value):
            raise ValueError(
                "must be a skill name or plugin-qualified skill ref using letters, "
                "digits, underscores, dots, or hyphens"
            )
        return value

    @field_validator("applies_to_steps")
    @classmethod
    def _step_refs(cls, value: list[str]) -> list[str]:
        return [_validate_ref(item) for item in value]


class WorkflowSkillPresetRequirementSpec(BaseModel):
    """Reusable main-agent skill preset guidance for operating a workflow template."""

    model_config = ConfigDict(extra="forbid")

    skill_preset_ref: str = Field(min_length=1, max_length=160)
    requirement: Literal["required", "recommended", "optional"] = "recommended"
    purpose: str = ""
    applies_to_steps: list[str] = Field(default_factory=list)
    setup_notes: list[str] = Field(default_factory=list)

    @field_validator("skill_preset_ref")
    @classmethod
    def _skill_preset_ref(cls, value: str) -> str:
        return _validate_ref(value)

    @field_validator("applies_to_steps")
    @classmethod
    def _step_refs(cls, value: list[str]) -> list[str]:
        return [_validate_ref(item) for item in value]


class CapabilityRequirementSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=160)
    required: bool = True
    description: str = ""
    preferred_providers: list[str] = Field(default_factory=list)

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        return _validate_key(value)

    @field_validator("preferred_providers")
    @classmethod
    def _providers(cls, value: list[str]) -> list[str]:
        return [_validate_key(item) for item in value]


class AuthRequirementSpec(BaseModel):
    """Provider auth shape; never stores tokens or credential refs."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=160)
    provider: str = Field(min_length=1, max_length=160)
    auth_type: str = Field(default="api-key", max_length=80)
    scopes: list[str] = Field(default_factory=list)
    optional: bool = False
    description: str = ""

    @field_validator("key", "provider")
    @classmethod
    def _keys(cls, value: str) -> str:
        return _validate_key(value)


class ActionContractSpec(BaseModel):
    """Action/capability contract a run plan may resolve later."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    key: str = Field(min_length=1, max_length=160)
    name: str | None = Field(default=None, max_length=200)
    description: str = ""
    capability: str | None = Field(default=None, max_length=160)
    action: str | None = Field(default=None, max_length=160)
    provider: str | None = Field(default=None, max_length=160)
    optional: bool = False
    route_group: str | None = Field(default=None, max_length=160)
    route_key: str | None = Field(default=None, max_length=160)
    risk_level: str = Field(default="read", max_length=40)
    input_schema_json: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("input_schema", "input_schema_json"),
    )
    output_schema_json: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("output_schema", "output_schema_json"),
    )
    config_json: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("config", "config_json"),
    )
    auth_ref: str | None = Field(default=None, max_length=160)
    approval_ref: str | None = Field(default=None, max_length=160)

    @field_validator(
        "key",
        "capability",
        "action",
        "provider",
        "auth_ref",
        "approval_ref",
        "route_group",
        "route_key",
    )
    @classmethod
    def _refs(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_ref(value)

    @model_validator(mode="after")
    def _route_pair(self) -> ActionContractSpec:
        if (self.route_group is None) != (self.route_key is None):
            raise ValueError("route_group and route_key must be set together")
        return self


class ResourceContractSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    key: str = Field(min_length=1, max_length=160)
    resource: str = Field(min_length=1, max_length=160)
    purpose: str = ""
    schema_data: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("schema", "schema_json", "schema_data"),
        serialization_alias="schema_json",
    )
    required: bool = False

    @field_validator("key", "resource")
    @classmethod
    def _keys(cls, value: str) -> str:
        return _validate_key(value)


class PolicySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    key: str = Field(min_length=1, max_length=160)
    kind: str = Field(default="general", max_length=80)
    description: str = ""
    config_json: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("config", "config_json"),
    )

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        return _validate_key(value)


class ApprovalGateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    key: str = Field(min_length=1, max_length=160)
    description: str = ""
    required_when: str = "always"
    approver: str | None = Field(default=None, max_length=200)
    config_json: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("config", "config_json"),
    )

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        return _validate_key(value)


class LearningHookSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=160)
    prompt: str
    tags: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        return _validate_key(value)


class WorkflowStepTemplateSpec(BaseModel):
    """Stage blueprint. It names intent/contracts, not concrete action payloads."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=200)
    purpose: str = ""
    instructions: list[str] = Field(default_factory=list)
    input_refs: list[str] = Field(default_factory=list)
    context_refs: list[str] = Field(default_factory=list)
    action_refs: list[str] = Field(default_factory=list)
    resource_refs: list[str] = Field(default_factory=list)
    policy_refs: list[str] = Field(default_factory=list)
    approval_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    extensions_json: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("extensions", "extensions_json"),
    )

    @field_validator(
        "id",
        "input_refs",
        "context_refs",
        "action_refs",
        "resource_refs",
        "policy_refs",
        "approval_refs",
        "output_refs",
        "depends_on",
        mode="before",
    )
    @classmethod
    def _refs(cls, value: Any) -> Any:
        if isinstance(value, str):
            return _validate_ref(value)
        if isinstance(value, list):
            return [_validate_ref(str(item)) for item in value]
        return value


class WorkflowTemplateSpec(BaseModel):
    """Reusable workflow template contract."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: str = WORKFLOW_TEMPLATE_SCHEMA_VERSION
    key: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(default="0.1.0", max_length=40)
    description: str = ""
    domain: str | None = Field(default=None, max_length=120)
    owner: TemplateOwnerSpec | None = None
    experience: WorkflowExperienceSpec | None = None
    public: WorkflowPublicSpec | None = None
    based_on: TemplateBaseSpec | None = None
    when_to_use: list[str] = Field(default_factory=list)
    when_not_to_use: list[str] = Field(default_factory=list)
    inputs: list[TemplateIOSpec] = Field(default_factory=list)
    context_requirements: list[ContextRequirementSpec] = Field(default_factory=list)
    agent_requirements: list[WorkflowAgentRequirementSpec] = Field(default_factory=list)
    skill_requirements: list[WorkflowSkillRequirementSpec] = Field(default_factory=list)
    skill_preset_requirements: list[WorkflowSkillPresetRequirementSpec] = Field(
        default_factory=list
    )
    capability_requirements: list[CapabilityRequirementSpec] = Field(default_factory=list)
    auth_requirements: list[AuthRequirementSpec] = Field(default_factory=list)
    action_contracts: list[ActionContractSpec] = Field(default_factory=list)
    resource_contracts: list[ResourceContractSpec] = Field(default_factory=list)
    policies: list[PolicySpec] = Field(default_factory=list)
    approval_gates: list[ApprovalGateSpec] = Field(default_factory=list)
    steps: list[WorkflowStepTemplateSpec] = Field(min_length=1)
    outputs: list[TemplateIOSpec] = Field(default_factory=list)
    learning_hooks: list[LearningHookSpec] = Field(default_factory=list)
    failure_handling: list[str] = Field(default_factory=list)
    metadata_json: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("metadata", "metadata_json"),
    )
    extensions_json: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("extensions", "extensions_json"),
    )
    ui_json: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("ui", "ui_json"),
    )

    @field_validator("schema_version")
    @classmethod
    def _schema_version(cls, value: str) -> str:
        if value != WORKFLOW_TEMPLATE_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {WORKFLOW_TEMPLATE_SCHEMA_VERSION!r}")
        return value

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        return _validate_key(value)

    @model_validator(mode="after")
    def _cross_refs_and_secret_guard(self) -> WorkflowTemplateSpec:
        refs: dict[str, set[str]] = {
            "inputs": {item.key for item in self.inputs},
            "context": {item.id for item in self.context_requirements},
            "actions": {item.key for item in self.action_contracts},
            "resources": {item.key for item in self.resource_contracts},
            "policies": {item.key for item in self.policies},
            "approvals": {item.key for item in self.approval_gates},
            "outputs": {item.key for item in self.outputs},
            "auth": {item.key for item in self.auth_requirements},
            "steps": {item.id for item in self.steps},
        }
        sources: dict[str, list[Any]] = {
            "inputs": list(self.inputs),
            "context": list(self.context_requirements),
            "actions": list(self.action_contracts),
            "resources": list(self.resource_contracts),
            "policies": list(self.policies),
            "approvals": list(self.approval_gates),
            "outputs": list(self.outputs),
            "auth": list(self.auth_requirements),
            "steps": list(self.steps),
        }
        for label, values in refs.items():
            source = sources[label]
            if len(values) != len(source):
                raise ValueError(f"duplicate {label} keys are not allowed")
        agent_roles = {item.role for item in self.agent_requirements}
        if len(agent_roles) != len(self.agent_requirements):
            raise ValueError("duplicate agent requirement roles are not allowed")
        skill_refs = {item.skill_ref for item in self.skill_requirements}
        if len(skill_refs) != len(self.skill_requirements):
            raise ValueError("duplicate skill requirement refs are not allowed")
        skill_preset_refs = {item.skill_preset_ref for item in self.skill_preset_requirements}
        if len(skill_preset_refs) != len(self.skill_preset_requirements):
            raise ValueError("duplicate skill preset requirement refs are not allowed")

        for action in self.action_contracts:
            if action.auth_ref is not None and action.auth_ref not in refs["auth"]:
                raise ValueError(
                    f"action {action.key!r} references unknown auth {action.auth_ref!r}"
                )
            if action.approval_ref is not None and action.approval_ref not in refs["approvals"]:
                raise ValueError(
                    f"action {action.key!r} references unknown approval {action.approval_ref!r}"
                )

        for step in self.steps:
            _check_refs(step.id, "input_refs", step.input_refs, refs["inputs"])
            _check_refs(step.id, "context_refs", step.context_refs, refs["context"])
            _check_refs(step.id, "action_refs", step.action_refs, refs["actions"])
            _check_refs(step.id, "resource_refs", step.resource_refs, refs["resources"])
            _check_refs(step.id, "policy_refs", step.policy_refs, refs["policies"])
            _check_refs(step.id, "approval_refs", step.approval_refs, refs["approvals"])
            _check_refs(step.id, "output_refs", step.output_refs, refs["outputs"])
            _check_refs(step.id, "depends_on", step.depends_on, refs["steps"], allow_self=False)

        for agent_requirement in self.agent_requirements:
            for step_ref in agent_requirement.applies_to_steps:
                if step_ref not in refs["steps"]:
                    raise ValueError(
                        f"agent requirement {agent_requirement.role!r} applies_to_steps "
                        f"references unknown step {step_ref!r}"
                    )
        for skill_requirement in self.skill_requirements:
            for step_ref in skill_requirement.applies_to_steps:
                if step_ref not in refs["steps"]:
                    raise ValueError(
                        f"skill requirement {skill_requirement.skill_ref!r} applies_to_steps "
                        f"references unknown step {step_ref!r}"
                    )
        for skill_preset_requirement in self.skill_preset_requirements:
            for step_ref in skill_preset_requirement.applies_to_steps:
                if step_ref not in refs["steps"]:
                    raise ValueError(
                        "skill preset requirement "
                        f"{skill_preset_requirement.skill_preset_ref!r} applies_to_steps "
                        f"references unknown step {step_ref!r}"
                    )

        secret_paths = _secret_paths(self.model_dump(mode="python", exclude_none=True))
        if secret_paths:
            raise ValueError(
                "workflow templates must not contain secrets or credential values: "
                + ", ".join(secret_paths[:8])
            )
        runtime_paths = _runtime_payload_paths(self.model_dump(mode="python", exclude_none=True))
        if runtime_paths:
            raise ValueError(
                "workflow templates must not contain final action payloads, provider object ids, "
                "or hard-coded business decisions: " + ", ".join(runtime_paths[:8])
            )
        return self


class WorkflowTemplateValidationOut(BaseModel):
    valid: bool
    template: WorkflowTemplateSpec | None = None
    errors: list[WorkflowTemplateIssue] = Field(default_factory=list)
    warnings: list[WorkflowTemplateIssue] = Field(default_factory=list)


def _check_refs(
    step_id: str,
    field: str,
    values: list[str],
    allowed: set[str],
    *,
    allow_self: bool = True,
) -> None:
    for value in values:
        if value not in allowed or (not allow_self and value == step_id):
            raise ValueError(f"step {step_id!r} {field} references unknown item {value!r}")


def parse_workflow_template_obj(data: dict[str, Any]) -> WorkflowTemplateSpec:
    return WorkflowTemplateSpec.model_validate(data)


def parse_workflow_template_yaml(text: str) -> WorkflowTemplateSpec:
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ValueError("workflow template YAML must contain an object")
    return parse_workflow_template_obj(loaded)


def validate_workflow_template_obj(data: dict[str, Any]) -> WorkflowTemplateValidationOut:
    try:
        template = parse_workflow_template_obj(data)
    except ValidationError as exc:
        return WorkflowTemplateValidationOut(
            valid=False,
            errors=[
                WorkflowTemplateIssue(
                    path=".".join(str(part) for part in err.get("loc", ()) if part != "__root__")
                    or "$",
                    message=str(err.get("msg", "invalid template")),
                    code=str(err.get("type", "validation_error")),
                )
                for err in exc.errors()
            ],
        )
    except Exception as exc:
        return WorkflowTemplateValidationOut(
            valid=False,
            errors=[WorkflowTemplateIssue(path="$", message=str(exc))],
        )
    return WorkflowTemplateValidationOut(valid=True, template=template)


def validate_workflow_template_yaml(text: str) -> WorkflowTemplateValidationOut:
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return WorkflowTemplateValidationOut(
            valid=False,
            errors=[WorkflowTemplateIssue(path="$", message=str(exc), code="yaml_error")],
        )
    if not isinstance(loaded, dict):
        return WorkflowTemplateValidationOut(
            valid=False,
            errors=[
                WorkflowTemplateIssue(
                    path="$",
                    message="workflow template YAML must be an object",
                )
            ],
        )
    return validate_workflow_template_obj(loaded)


__all__ = [
    "ALLOWED_CONTEXT_SOURCES",
    "MAX_TEMPLATE_CONTEXT_ITEMS",
    "WORKFLOW_TEMPLATE_SCHEMA_VERSION",
    "ActionContractSpec",
    "ApprovalGateSpec",
    "AuthRequirementSpec",
    "CapabilityRequirementSpec",
    "ContextRequirementSpec",
    "LearningHookSpec",
    "PolicySpec",
    "ResourceContractSpec",
    "TemplateBaseSpec",
    "TemplateIOSpec",
    "TemplateOwnerSpec",
    "WorkflowExperienceSpec",
    "WorkflowHandoffSpec",
    "WorkflowPublicSpec",
    "WorkflowAgentRequirementSpec",
    "WorkflowSkillPresetRequirementSpec",
    "WorkflowSkillRequirementSpec",
    "WorkflowStepTemplateSpec",
    "WorkflowTemplateIssue",
    "WorkflowTemplateSpec",
    "WorkflowTemplateValidationOut",
    "parse_workflow_template_obj",
    "parse_workflow_template_yaml",
    "validate_workflow_template_obj",
    "validate_workflow_template_yaml",
]
