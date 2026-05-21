"""Concrete StackOS run-plan schema.

Run plans are one-run execution objects authored by an agent or human. Unlike
workflow templates, they may contain concrete provider choices, object refs, and
action payload configuration. They still must not contain secrets: tools receive
only opaque credential refs and the daemon resolves backing credentials.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from content_stack.artifacts import redact_secret_text
from content_stack.workflows.template_loader import LoadedWorkflowTemplate

RUN_PLAN_SCHEMA_VERSION = "stackos.run-plan.v1"
MAX_RUN_PLAN_STEPS = 100
MAX_RUN_PLAN_APPROVALS = 50

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(?:[-.][a-z0-9_]+)*$")
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
_OPAQUE_REF_KEYS = frozenset({"auth_ref", "credential_ref"})


def _validate_key(value: str) -> str:
    if not _KEY_RE.match(value):
        raise ValueError("must be a lowercase snake/kebab/dotted identifier")
    return value


def _is_secret_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in _OPAQUE_REF_KEYS or normalized.endswith("_credential_ref"):
        return False
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


def find_run_plan_secret_paths(value: Any) -> list[str]:
    """Return paths that look like raw secrets in run-plan input."""
    return _secret_paths(value)


def ensure_run_plan_has_no_secrets(value: Any) -> None:
    """Raise ``ValueError`` if run-plan input contains raw secrets."""
    paths = find_run_plan_secret_paths(value)
    if paths:
        raise ValueError(
            "run plans must not contain secrets; use opaque credential_ref values: "
            + ", ".join(paths[:8])
        )


def _dependency_cycle(deps_by_step: dict[str, list[str]]) -> list[str] | None:
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(step_id: str) -> list[str] | None:
        if step_id in visited:
            return None
        if step_id in visiting:
            start = visiting.index(step_id)
            return [*visiting[start:], step_id]
        visiting.append(step_id)
        for dep in deps_by_step.get(step_id, []):
            cycle = visit(dep)
            if cycle is not None:
                return cycle
        visiting.pop()
        visited.add(step_id)
        return None

    for step_id in deps_by_step:
        cycle = visit(step_id)
        if cycle is not None:
            return cycle
    return None


class RunPlanIssue(BaseModel):
    """Validation issue returned by non-throwing run-plan validation."""

    path: str
    message: str
    code: str = "validation_error"


class RunPlanStepSpec(BaseModel):
    """Concrete step in an agent-authored run plan."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=300)
    purpose: str = ""
    position: int | None = Field(default=None, ge=0)
    depends_on: list[str] = Field(default_factory=list)
    input_refs: list[str] = Field(default_factory=list)
    context_refs: list[str] = Field(default_factory=list)
    action_refs: list[str] = Field(default_factory=list)
    resource_refs: list[str] = Field(default_factory=list)
    policy_refs: list[str] = Field(default_factory=list)
    approval_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    action_payloads_json: list[dict[str, Any]] | None = Field(
        default=None,
        validation_alias=AliasChoices("action_payloads", "action_payloads_json"),
    )
    expected_outputs_json: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("expected_outputs", "expected_outputs_json"),
    )
    metadata_json: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("metadata", "metadata_json"),
    )

    @field_validator(
        "id",
        "depends_on",
        "input_refs",
        "context_refs",
        "action_refs",
        "resource_refs",
        "policy_refs",
        "approval_refs",
        "output_refs",
        mode="before",
    )
    @classmethod
    def _refs(cls, value: Any) -> Any:
        if isinstance(value, str):
            return _validate_key(value)
        if isinstance(value, list):
            return [_validate_key(str(item)) for item in value]
        return value

    @field_validator("action_payloads_json", mode="before")
    @classmethod
    def _payload_list(cls, value: Any) -> Any:
        if value is None or isinstance(value, list):
            return value
        if isinstance(value, dict):
            return [value]
        return value


class RunPlanApprovalSpec(BaseModel):
    """Approval gate resolved for a concrete run plan."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    key: str = Field(min_length=1, max_length=160)
    title: str | None = Field(default=None, max_length=300)
    description: str = ""
    step_id: str | None = Field(default=None, max_length=160)
    required_when: str = Field(default="always", max_length=160)
    approver: str | None = Field(default=None, max_length=200)
    metadata_json: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("metadata", "metadata_json"),
    )

    @field_validator("key", "step_id")
    @classmethod
    def _keys(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_key(value)


class RunPlanSpec(BaseModel):
    """Concrete one-run plan. StackOS stores and gates it; agents execute it."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: str = RUN_PLAN_SCHEMA_VERSION
    key: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=300)
    goal: str = ""
    template_key: str | None = Field(default=None, max_length=160)
    template_version: str | None = Field(default=None, max_length=40)
    template_source: str | None = Field(default=None, max_length=40)
    context_snapshot_id: int | None = None
    inputs_json: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("inputs", "inputs_json"),
    )
    selected_context_json: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("selected_context", "selected_context_json"),
    )
    context_filters_json: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("context_filters", "context_filters_json"),
    )
    grant_snapshot_json: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("grants", "grant_snapshot_json"),
    )
    budget_snapshot_json: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("budgets", "budget_snapshot_json"),
    )
    policy_snapshot_json: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("policies", "policy_snapshot_json"),
    )
    output_contract_json: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("outputs", "output_contract_json"),
    )
    steps: list[RunPlanStepSpec] = Field(min_length=1, max_length=MAX_RUN_PLAN_STEPS)
    approvals: list[RunPlanApprovalSpec] = Field(
        default_factory=list,
        max_length=MAX_RUN_PLAN_APPROVALS,
    )
    metadata_json: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("metadata", "metadata_json"),
    )

    @field_validator("schema_version")
    @classmethod
    def _schema_version(cls, value: str) -> str:
        if value != RUN_PLAN_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {RUN_PLAN_SCHEMA_VERSION!r}")
        return value

    @field_validator("key", "template_key")
    @classmethod
    def _keys(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_key(value)

    @model_validator(mode="after")
    def _cross_refs_and_secret_guard(self) -> RunPlanSpec:
        step_ids = {step.id for step in self.steps}
        approval_keys = {approval.key for approval in self.approvals}
        if len(step_ids) != len(self.steps):
            raise ValueError("duplicate step ids are not allowed")
        if len(approval_keys) != len(self.approvals):
            raise ValueError("duplicate approval keys are not allowed")

        deps_by_step: dict[str, list[str]] = {}
        for step in self.steps:
            deps_by_step[step.id] = step.depends_on
            for dep in step.depends_on:
                if dep not in step_ids or dep == step.id:
                    raise ValueError(f"step {step.id!r} depends_on references unknown item {dep!r}")
            for approval_ref in step.approval_refs:
                if approval_ref not in approval_keys:
                    raise ValueError(
                        f"step {step.id!r} approval_refs references unknown item "
                        f"{approval_ref!r}"
                    )

        for approval in self.approvals:
            if approval.step_id is not None and approval.step_id not in step_ids:
                raise ValueError(
                    f"approval {approval.key!r} references unknown step {approval.step_id!r}"
                )

        cycle = _dependency_cycle(deps_by_step)
        if cycle is not None:
            raise ValueError("cyclic step dependencies are not allowed: " + " -> ".join(cycle))

        ensure_run_plan_has_no_secrets(self.model_dump(mode="python", exclude_none=True))
        return self


class RunPlanValidationOut(BaseModel):
    valid: bool
    plan: RunPlanSpec | None = None
    errors: list[RunPlanIssue] = Field(default_factory=list)
    warnings: list[RunPlanIssue] = Field(default_factory=list)


def parse_run_plan_obj(data: dict[str, Any]) -> RunPlanSpec:
    return RunPlanSpec.model_validate(data)


def validate_run_plan_obj(data: dict[str, Any]) -> RunPlanValidationOut:
    try:
        plan = parse_run_plan_obj(data)
    except ValidationError as exc:
        return RunPlanValidationOut(
            valid=False,
            errors=[
                RunPlanIssue(
                    path=".".join(str(part) for part in err.get("loc", ()) if part != "__root__")
                    or "$",
                    message=str(err.get("msg", "invalid run plan")),
                    code=str(err.get("type", "validation_error")),
                )
                for err in exc.errors()
            ],
        )
    except Exception as exc:
        return RunPlanValidationOut(
            valid=False,
            errors=[RunPlanIssue(path="$", message=str(exc))],
        )
    return RunPlanValidationOut(valid=True, plan=plan)


def run_plan_from_template(
    loaded: LoadedWorkflowTemplate,
    *,
    key: str | None = None,
    title: str | None = None,
    inputs_json: dict[str, Any] | None = None,
    context_snapshot_id: int | None = None,
    selected_context_json: dict[str, Any] | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> RunPlanSpec:
    """Create a concrete editable baseline from an inert workflow template."""
    spec = loaded.spec
    approvals = [
        RunPlanApprovalSpec(
            key=gate.key,
            title=gate.key.replace("-", " ").replace("_", " ").title(),
            description=gate.description,
            required_when=gate.required_when,
            approver=gate.approver,
            metadata_json=gate.config_json or None,
        )
        for gate in spec.approval_gates
    ]
    steps = [
        RunPlanStepSpec(
            id=step.id,
            title=step.title,
            purpose=step.purpose,
            position=index,
            depends_on=step.depends_on,
            input_refs=step.input_refs,
            context_refs=step.context_refs,
            action_refs=step.action_refs,
            resource_refs=step.resource_refs,
            policy_refs=step.policy_refs,
            approval_refs=step.approval_refs,
            output_refs=step.output_refs,
            instructions=step.instructions,
            success_criteria=step.success_criteria,
            metadata_json=step.extensions_json,
        )
        for index, step in enumerate(spec.steps)
    ]
    grants = {
        "capability_requirements": [
            item.model_dump(mode="json", exclude_none=True)
            for item in spec.capability_requirements
        ],
        "auth_requirements": [
            item.model_dump(mode="json", exclude_none=True)
            for item in spec.auth_requirements
        ],
        "action_contracts": [
            item.model_dump(mode="json", exclude_none=True)
            for item in spec.action_contracts
        ],
        "resource_contracts": [
            item.model_dump(mode="json", exclude_none=True)
            for item in spec.resource_contracts
        ],
    }
    return RunPlanSpec(
        key=key or f"{spec.key}.run",
        title=title or spec.name,
        goal=spec.description,
        template_key=spec.key,
        template_version=spec.version,
        template_source=loaded.summary.source,
        context_snapshot_id=context_snapshot_id,
        inputs_json=inputs_json or {},
        selected_context_json=selected_context_json,
        context_filters_json={
            "requirements": [
                item.model_dump(mode="json", exclude_none=True)
                for item in spec.context_requirements
            ]
        },
        grant_snapshot_json=grants,
        policy_snapshot_json={
            "policies": [
                item.model_dump(mode="json", exclude_none=True)
                for item in spec.policies
            ],
            "approval_gates": [
                item.model_dump(mode="json", exclude_none=True)
                for item in spec.approval_gates
            ],
        },
        output_contract_json={
            "outputs": [
                item.model_dump(mode="json", exclude_none=True)
                for item in spec.outputs
            ]
        },
        steps=steps,
        approvals=approvals,
        metadata_json=metadata_json,
    )


__all__ = [
    "MAX_RUN_PLAN_APPROVALS",
    "MAX_RUN_PLAN_STEPS",
    "RUN_PLAN_SCHEMA_VERSION",
    "RunPlanApprovalSpec",
    "RunPlanIssue",
    "RunPlanSpec",
    "RunPlanStepSpec",
    "RunPlanValidationOut",
    "ensure_run_plan_has_no_secrets",
    "find_run_plan_secret_paths",
    "parse_run_plan_obj",
    "run_plan_from_template",
    "validate_run_plan_obj",
]
