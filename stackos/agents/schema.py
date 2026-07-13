"""Agent preset schema for reusable agent operating contracts."""

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

AGENT_PRESET_SCHEMA_VERSION = "stackos.agent-preset.v1"

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(?:[-.][a-z0-9_]+)*$")
_TOOL_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:[-.][A-Za-z0-9_]+)*$")
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


def _validate_ref(value: str) -> str:
    if not _KEY_RE.match(value):
        raise ValueError("must be a lowercase snake/kebab/dotted reference")
    return value


def _validate_tool_ref(value: str) -> str:
    if not _TOOL_REF_RE.match(value):
        raise ValueError("must be a dotted StackOS tool or operation reference")
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


class AgentPresetIssue(BaseModel):
    """Validation issue returned by non-throwing preset validation."""

    path: str
    message: str
    code: str = "validation_error"


class AgentPresetReferenceSpec(BaseModel):
    """Project reference the calling agent should read before adapting a preset."""

    model_config = ConfigDict(extra="forbid")

    ref: str = Field(min_length=1, max_length=300)
    purpose: str = ""
    required: bool = True
    when: str | None = Field(default=None, max_length=240)


class AgentProjectAdaptationSpec(BaseModel):
    """Required project-specific adaptation guidance for a generic preset."""

    model_config = ConfigDict(extra="forbid")

    required: bool = True
    do_not_use_verbatim: bool = True
    instruction: str = (
        "Adapt this generic preset to the current project before acting. Preserve the role "
        "intent, but rewrite stack-specific rules, references, tools, tests, and signoff "
        "steps from project context."
    )
    required_context_refs: list[AgentPresetReferenceSpec] = Field(default_factory=list)
    conditional_context_refs: list[AgentPresetReferenceSpec] = Field(default_factory=list)
    prompt_assembly_order: list[str] = Field(
        default_factory=lambda: [
            "generic_agent_preset",
            "project_adaptation_overlay",
            "workflow_agent_requirements",
            "current_tracker_or_run_plan_context",
            "user_request",
        ]
    )
    required_agent_action: str = (
        "Before execution, create an adapted operating brief for this project. If required "
        "references are missing, fetch them or stop and ask for them."
    )

    @model_validator(mode="after")
    def _adaptation_must_be_explicit(self) -> AgentProjectAdaptationSpec:
        if not self.required:
            raise ValueError("agent presets must require project-specific adaptation")
        if not self.do_not_use_verbatim:
            raise ValueError("agent presets must forbid verbatim generic use")
        return self


class AgentPromptContractSpec(BaseModel):
    """Reusable prompt contract for one agent role."""

    model_config = ConfigDict(extra="forbid")

    mission: str = Field(min_length=1)
    responsibilities: list[str] = Field(default_factory=list)
    must_do: list[str] = Field(default_factory=list)
    must_not_do: list[str] = Field(default_factory=list)
    handoff_inputs: list[str] = Field(default_factory=list)
    handoff_outputs: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    self_check: list[str] = Field(default_factory=list)


class AgentPresetBundleSpec(BaseModel):
    """YAML bundle for multiple related agent presets."""

    model_config = ConfigDict(extra="forbid")

    presets: list[AgentPresetSpec] = Field(min_length=1)


class AgentPresetSpec(BaseModel):
    """Generic agent preset. It is a reusable contract, not a model runner."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: str = AGENT_PRESET_SCHEMA_VERSION
    key: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(default="0.1.0", max_length=40)
    description: str = ""
    domain: str | None = Field(default=None, max_length=120)
    agent_type: str = Field(default="mcp-tool-consumer", max_length=120)
    generic_preset: bool = True
    role: str = Field(min_length=1, max_length=160)
    role_class: Literal["reasoning", "mechanical", "review"] = "reasoning"
    prompt_contract: AgentPromptContractSpec
    project_adaptation: AgentProjectAdaptationSpec = Field(
        default_factory=AgentProjectAdaptationSpec
    )
    recommended_tools: list[str] = Field(default_factory=list)
    workflow_roles: list[str] = Field(default_factory=list)
    applies_to_workflows: list[str] = Field(default_factory=list)
    metadata_json: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("metadata", "metadata_json"),
    )

    @field_validator("schema_version")
    @classmethod
    def _schema_version(cls, value: str) -> str:
        if value != AGENT_PRESET_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {AGENT_PRESET_SCHEMA_VERSION!r}")
        return value

    @field_validator("key", "role")
    @classmethod
    def _key_fields(cls, value: str) -> str:
        return _validate_ref(value)

    @field_validator("workflow_roles", "applies_to_workflows")
    @classmethod
    def _ref_lists(cls, value: list[str]) -> list[str]:
        return [_validate_ref(item) for item in value]

    @field_validator("recommended_tools")
    @classmethod
    def _tool_refs(cls, value: list[str]) -> list[str]:
        return [_validate_tool_ref(item) for item in value]

    @model_validator(mode="after")
    def _guard_contract(self) -> AgentPresetSpec:
        if not self.generic_preset:
            raise ValueError("bundled agent presets must be marked generic_preset=true")
        if self.role not in self.workflow_roles:
            raise ValueError("workflow_roles must include the preset role")
        secret_paths = _secret_paths(self.model_dump(mode="python", exclude_none=True))
        if secret_paths:
            raise ValueError(
                "agent presets must not contain secrets or credential values: "
                + ", ".join(secret_paths[:8])
            )
        return self


class AgentPresetValidationOut(BaseModel):
    valid: bool
    preset: AgentPresetSpec | None = None
    errors: list[AgentPresetIssue] = Field(default_factory=list)
    warnings: list[AgentPresetIssue] = Field(default_factory=list)


def parse_agent_preset_obj(data: dict[str, Any]) -> AgentPresetSpec:
    return AgentPresetSpec.model_validate(data)


def parse_agent_preset_yaml(text: str) -> AgentPresetSpec:
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ValueError("agent preset YAML must contain an object")
    return parse_agent_preset_obj(loaded)


def parse_agent_preset_bundle_obj(data: dict[str, Any]) -> list[AgentPresetSpec]:
    if "presets" in data:
        return AgentPresetBundleSpec.model_validate(data).presets
    return [parse_agent_preset_obj(data)]


def parse_agent_preset_bundle_yaml(text: str) -> list[AgentPresetSpec]:
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ValueError("agent preset YAML must contain an object")
    return parse_agent_preset_bundle_obj(loaded)


def validate_agent_preset_obj(data: dict[str, Any]) -> AgentPresetValidationOut:
    try:
        preset = parse_agent_preset_obj(data)
    except ValidationError as exc:
        return AgentPresetValidationOut(
            valid=False,
            errors=[
                AgentPresetIssue(
                    path=".".join(str(part) for part in err.get("loc", ()) if part != "__root__")
                    or "$",
                    message=str(err.get("msg", "invalid preset")),
                    code=str(err.get("type", "validation_error")),
                )
                for err in exc.errors()
            ],
        )
    except Exception as exc:
        return AgentPresetValidationOut(
            valid=False,
            errors=[AgentPresetIssue(path="$", message=str(exc))],
        )
    return AgentPresetValidationOut(valid=True, preset=preset)


def validate_agent_preset_yaml(text: str) -> AgentPresetValidationOut:
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return AgentPresetValidationOut(
            valid=False,
            errors=[AgentPresetIssue(path="$", message=str(exc), code="yaml_error")],
        )
    if not isinstance(loaded, dict):
        return AgentPresetValidationOut(
            valid=False,
            errors=[AgentPresetIssue(path="$", message="agent preset YAML must be an object")],
        )
    return validate_agent_preset_obj(loaded)


__all__ = [
    "AGENT_PRESET_SCHEMA_VERSION",
    "AgentPresetBundleSpec",
    "AgentPresetIssue",
    "AgentPresetReferenceSpec",
    "AgentPresetSpec",
    "AgentPresetValidationOut",
    "AgentProjectAdaptationSpec",
    "AgentPromptContractSpec",
    "parse_agent_preset_bundle_obj",
    "parse_agent_preset_bundle_yaml",
    "parse_agent_preset_obj",
    "parse_agent_preset_yaml",
    "validate_agent_preset_obj",
    "validate_agent_preset_yaml",
]
