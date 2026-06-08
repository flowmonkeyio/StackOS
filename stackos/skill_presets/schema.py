"""Skill preset schema for reusable main-agent operating contracts."""

from __future__ import annotations

import re
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from stackos.artifacts import redact_secret_text

SKILL_PRESET_SCHEMA_VERSION = "stackos.skill-preset.v1"

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


class SkillPresetIssue(BaseModel):
    """Validation issue returned by non-throwing preset validation."""

    path: str
    message: str
    code: str = "validation_error"


class SkillPresetReferenceSpec(BaseModel):
    """Project reference the main agent should read before adapting a preset."""

    model_config = ConfigDict(extra="forbid")

    ref: str = Field(min_length=1, max_length=300)
    purpose: str = ""
    required: bool = True
    when: str | None = Field(default=None, max_length=240)


class SkillProjectAdaptationSpec(BaseModel):
    """Required project-specific adaptation guidance for a generic skill preset."""

    model_config = ConfigDict(extra="forbid")

    required: bool = True
    do_not_use_verbatim: bool = True
    instruction: str = (
        "Adapt this generic skill preset to the current project before acting. Preserve "
        "the operating loop, but rewrite project rules, references, tools, tests, and "
        "signoff steps from project context."
    )
    required_context_refs: list[SkillPresetReferenceSpec] = Field(default_factory=list)
    conditional_context_refs: list[SkillPresetReferenceSpec] = Field(default_factory=list)
    prompt_assembly_order: list[str] = Field(
        default_factory=lambda: [
            "generic_skill_preset",
            "project_adaptation_overlay",
            "workflow_skill_preset_requirements",
            "current_tracker_or_run_plan_context",
            "user_request",
        ]
    )
    required_agent_action: str = (
        "Before execution, create an adapted operating brief for this project. If "
        "required references are missing, fetch them or stop and ask for them."
    )

    @model_validator(mode="after")
    def _adaptation_must_be_explicit(self) -> SkillProjectAdaptationSpec:
        if not self.required:
            raise ValueError("skill presets must require project-specific adaptation")
        if not self.do_not_use_verbatim:
            raise ValueError("skill presets must forbid verbatim generic use")
        return self


class SkillOperatingContractSpec(BaseModel):
    """Reusable operating contract for the main agent."""

    model_config = ConfigDict(extra="forbid")

    mission: str = Field(min_length=1)
    responsibilities: list[str] = Field(default_factory=list)
    must_do: list[str] = Field(default_factory=list)
    must_not_do: list[str] = Field(default_factory=list)
    required_outputs: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    self_check: list[str] = Field(default_factory=list)


class SkillPresetBundleSpec(BaseModel):
    """YAML bundle for multiple related skill presets."""

    model_config = ConfigDict(extra="forbid")

    presets: list[SkillPresetSpec] = Field(min_length=1)


class SkillPresetSpec(BaseModel):
    """Generic main-agent skill preset. It is a reusable contract, not a runtime skill."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SKILL_PRESET_SCHEMA_VERSION
    key: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(default="0.1.0", max_length=40)
    description: str = ""
    domain: str | None = Field(default=None, max_length=120)
    skill_type: str = Field(default="main-agent-orchestration", max_length=120)
    generic_preset: bool = True
    operating_contract: SkillOperatingContractSpec
    project_adaptation: SkillProjectAdaptationSpec = Field(
        default_factory=SkillProjectAdaptationSpec
    )
    recommended_tools: list[str] = Field(default_factory=list)
    applies_to_workflows: list[str] = Field(default_factory=list)
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def _schema_version(cls, value: str) -> str:
        if value != SKILL_PRESET_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SKILL_PRESET_SCHEMA_VERSION!r}")
        return value

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        return _validate_ref(value)

    @field_validator("applies_to_workflows")
    @classmethod
    def _workflow_refs(cls, value: list[str]) -> list[str]:
        return [_validate_ref(item) for item in value]

    @field_validator("recommended_tools")
    @classmethod
    def _tool_refs(cls, value: list[str]) -> list[str]:
        return [_validate_tool_ref(item) for item in value]

    @model_validator(mode="after")
    def _guard_contract(self) -> SkillPresetSpec:
        if not self.generic_preset:
            raise ValueError("bundled skill presets must be marked generic_preset=true")
        secret_paths = _secret_paths(self.model_dump(mode="python", exclude_none=True))
        if secret_paths:
            raise ValueError(
                "skill presets must not contain secrets or credential values: "
                + ", ".join(secret_paths[:8])
            )
        return self


class SkillPresetValidationOut(BaseModel):
    valid: bool
    preset: SkillPresetSpec | None = None
    errors: list[SkillPresetIssue] = Field(default_factory=list)
    warnings: list[SkillPresetIssue] = Field(default_factory=list)


def parse_skill_preset_obj(data: dict[str, Any]) -> SkillPresetSpec:
    return SkillPresetSpec.model_validate(data)


def parse_skill_preset_yaml(text: str) -> SkillPresetSpec:
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ValueError("skill preset YAML must contain an object")
    return parse_skill_preset_obj(loaded)


def parse_skill_preset_bundle_obj(data: dict[str, Any]) -> list[SkillPresetSpec]:
    if "presets" in data:
        return SkillPresetBundleSpec.model_validate(data).presets
    return [parse_skill_preset_obj(data)]


def parse_skill_preset_bundle_yaml(text: str) -> list[SkillPresetSpec]:
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ValueError("skill preset YAML must contain an object")
    return parse_skill_preset_bundle_obj(loaded)


def validate_skill_preset_obj(data: dict[str, Any]) -> SkillPresetValidationOut:
    try:
        preset = parse_skill_preset_obj(data)
    except ValidationError as exc:
        return SkillPresetValidationOut(
            valid=False,
            errors=[
                SkillPresetIssue(
                    path=".".join(str(part) for part in err.get("loc", ()) if part != "__root__")
                    or "$",
                    message=str(err.get("msg", "invalid preset")),
                    code=str(err.get("type", "validation_error")),
                )
                for err in exc.errors()
            ],
        )
    except Exception as exc:
        return SkillPresetValidationOut(
            valid=False,
            errors=[SkillPresetIssue(path="$", message=str(exc))],
        )
    return SkillPresetValidationOut(valid=True, preset=preset)


def validate_skill_preset_yaml(text: str) -> SkillPresetValidationOut:
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return SkillPresetValidationOut(
            valid=False,
            errors=[SkillPresetIssue(path="$", message=str(exc), code="yaml_error")],
        )
    if not isinstance(loaded, dict):
        return SkillPresetValidationOut(
            valid=False,
            errors=[SkillPresetIssue(path="$", message="skill preset YAML must be an object")],
        )
    return validate_skill_preset_obj(loaded)


__all__ = [
    "SKILL_PRESET_SCHEMA_VERSION",
    "SkillOperatingContractSpec",
    "SkillPresetBundleSpec",
    "SkillPresetIssue",
    "SkillPresetReferenceSpec",
    "SkillPresetSpec",
    "SkillPresetValidationOut",
    "SkillProjectAdaptationSpec",
    "parse_skill_preset_bundle_obj",
    "parse_skill_preset_bundle_yaml",
    "parse_skill_preset_obj",
    "parse_skill_preset_yaml",
    "validate_skill_preset_obj",
    "validate_skill_preset_yaml",
]
