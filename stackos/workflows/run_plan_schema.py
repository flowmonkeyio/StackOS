"""Concrete StackOS run-plan schema.

Run plans are one-run execution objects authored by an agent or human. Unlike
workflow templates, they may contain concrete provider choices, object refs, and
action payload configuration. They still must not contain secrets: tools receive
only opaque credential refs and the daemon resolves backing credentials.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

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
from stackos.plugins.manifest import get_builtin_plugin_manifest_snapshot
from stackos.secret_refs import is_opaque_secret_ref, is_secret_ref_marker
from stackos.workflows.run_plan_grants import (
    RunPlanMcpToolGrant,
    parse_run_plan_mcp_tool_grants,
    validate_run_plan_mcp_tool_grants,
)
from stackos.workflows.template_schema import ActionContractSpec

if TYPE_CHECKING:
    from stackos.workflows.template_loader import LoadedWorkflowTemplate

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
    if is_secret_ref_marker(value):
        return []
    paths: list[str] = []
    if isinstance(value, dict):
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}"
            normalized_key = key.lower().replace("-", "_")
            opaque_secret_ref = normalized_key == "secret_ref" and is_opaque_secret_ref(raw_value)
            if (
                _is_secret_key(key)
                and not is_secret_ref_marker(raw_value)
                and not opaque_secret_ref
            ):
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
            "run plans must not contain secrets as raw values; use credential_ref for "
            "provider auth or secret.set with an exact $secret_ref marker for action "
            "payload values: " + ", ".join(paths[:8])
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
                        f"step {step.id!r} approval_refs references unknown item {approval_ref!r}"
                    )

        for approval in self.approvals:
            if approval.step_id is not None and approval.step_id not in step_ids:
                raise ValueError(
                    f"approval {approval.key!r} references unknown step {approval.step_id!r}"
                )

        cycle = _dependency_cycle(deps_by_step)
        if cycle is not None:
            raise ValueError("cyclic step dependencies are not allowed: " + " -> ".join(cycle))

        validate_run_plan_mcp_tool_grants(self.grant_snapshot_json, step_ids=step_ids)
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
    return RunPlanValidationOut(
        valid=True,
        plan=plan,
        warnings=_executable_readiness_warnings(plan),
    )


def run_plan_readiness_warnings(plan: RunPlanSpec) -> list[RunPlanIssue]:
    """Return executable-readiness warnings for an already-validated plan."""
    return _executable_readiness_warnings(plan)


def _grants_for_step(plan: RunPlanSpec, step_id: str) -> list[RunPlanMcpToolGrant]:
    try:
        grants = parse_run_plan_mcp_tool_grants(plan.grant_snapshot_json)
    except ValueError:
        return []
    return [grant for grant in grants if grant.step_id == step_id]


def _covered_action_refs(grants: list[RunPlanMcpToolGrant]) -> set[str]:
    refs: set[str] = set()
    for grant in grants:
        if grant.tool_name == "action.execute":
            refs.update(grant.action_refs)
    return refs


def _builtin_action_records() -> tuple[dict[str, str | None], ...]:
    records: list[dict[str, str | None]] = []
    for manifest in get_builtin_plugin_manifest_snapshot().manifests:
        for action in manifest.actions:
            records.append(
                {
                    "ref": f"{manifest.slug}.{action.key}",
                    "plugin_slug": manifest.slug,
                    "key": action.key,
                    "provider": action.provider,
                    "capability": action.capability,
                }
            )
    return tuple(records)


def _builtin_plugin_slugs() -> frozenset[str]:
    return frozenset(manifest.slug for manifest in get_builtin_plugin_manifest_snapshot().manifests)


def _contract_value(contract: ActionContractSpec | dict[str, Any], field: str) -> Any:
    if isinstance(contract, dict):
        return contract.get(field)
    return getattr(contract, field)


def _record_matches_contract(
    record: dict[str, str | None],
    contract: ActionContractSpec | dict[str, Any],
) -> bool:
    provider = _contract_value(contract, "provider")
    if isinstance(provider, str) and provider and record.get("provider") != provider:
        return False
    capability = _contract_value(contract, "capability")
    return not (
        isinstance(capability, str) and capability and record.get("capability") != capability
    )


def _resolve_action_contract_ref(
    contract: ActionContractSpec | dict[str, Any],
    *,
    template_plugin_slug: str | None,
) -> str | None:
    action = _contract_value(contract, "action")
    if not isinstance(action, str) or not action:
        return None
    first_segment = action.split(".", 1)[0]
    if first_segment in _builtin_plugin_slugs():
        return action

    records = _builtin_action_records()
    candidates: list[str] = []
    if isinstance(template_plugin_slug, str) and template_plugin_slug:
        local_ref = f"{template_plugin_slug}.{action}"
        candidates.extend(
            str(record["ref"])
            for record in records
            if record.get("ref") == local_ref and _record_matches_contract(record, contract)
        )
    candidates.extend(
        str(record["ref"])
        for record in records
        if record.get("key") == action and _record_matches_contract(record, contract)
    )
    if candidates:
        return next(iter(dict.fromkeys(candidates)))
    if isinstance(template_plugin_slug, str) and template_plugin_slug:
        return f"{template_plugin_slug}.{action}"
    return action


def _template_action_contract_refs(
    contracts: list[ActionContractSpec],
    *,
    template_plugin_slug: str | None,
) -> dict[str, str]:
    out: dict[str, str] = {}
    for contract in contracts:
        action_ref = _resolve_action_contract_ref(
            contract,
            template_plugin_slug=template_plugin_slug,
        )
        if action_ref is not None:
            out[contract.key] = action_ref
    return out


def _template_action_contract_map(plan: RunPlanSpec) -> dict[str, str]:
    snapshot = plan.grant_snapshot_json or {}
    raw_contracts = snapshot.get("action_contracts")
    if not isinstance(raw_contracts, list):
        return {}
    plugin_slug = snapshot.get("template_plugin_slug")
    out: dict[str, str] = {}
    for item in raw_contracts:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        action = item.get("action")
        if not isinstance(key, str) or not isinstance(action, str) or not action:
            continue
        resolved = _resolve_action_contract_ref(
            item,
            template_plugin_slug=plugin_slug if isinstance(plugin_slug, str) else None,
        )
        out[key] = resolved or action
    return out


def _action_ref_hint(plan: RunPlanSpec, refs: list[str]) -> str:
    contract_actions = _template_action_contract_map(plan)
    mapped = [f"{ref} -> {contract_actions[ref]}" for ref in refs if ref in contract_actions]
    if not mapped:
        return "Add concrete executable action_refs to the action.execute grant."
    return (
        "Template action refs are planning contract keys, not executable action refs; "
        "use concrete action refs such as " + ", ".join(mapped[:5]) + "."
    )


def _tool_granted(grants: list[RunPlanMcpToolGrant], tool_name: str) -> bool:
    return any(grant.tool_name == tool_name for grant in grants)


def _artifact_output_warning_enabled(plan: RunPlanSpec) -> bool:
    snapshot = plan.grant_snapshot_json or {}
    return snapshot.get("artifact_grant_policy") != "explicit"


def _executable_readiness_warnings(plan: RunPlanSpec) -> list[RunPlanIssue]:
    """Return non-blocking warnings for plans that validate but may not run.

    Structural validation answers "is this a valid run-plan object?". These
    warnings answer the agent-facing follow-up: "will the active run-plan step
    grants let the agent call the tools implied by the step contracts?".
    """
    warnings: list[RunPlanIssue] = []
    for index, step in enumerate(plan.steps):
        path = f"steps[{index}]"
        grants = _grants_for_step(plan, step.id)
        covered_action_refs = _covered_action_refs(grants)
        missing_action_refs = [ref for ref in step.action_refs if ref not in covered_action_refs]
        if missing_action_refs:
            warnings.append(
                RunPlanIssue(
                    path=f"{path}.action_refs",
                    code="missing_action_execute_grant",
                    message=(
                        f"step {step.id!r} declares action_refs that are not covered by an "
                        "action.execute grant; runPlan.claimStep will not allow those action "
                        f"calls until grant_snapshot_json.mcp_tool_grants includes step_id "
                        f"{step.id!r}, tool 'action.execute', and matching action_refs. "
                        + _action_ref_hint(plan, missing_action_refs)
                    ),
                )
            )
        if step.resource_refs and not _tool_granted(grants, "resource.upsert"):
            warnings.append(
                RunPlanIssue(
                    path=f"{path}.resource_refs",
                    code="missing_resource_upsert_grant",
                    message=(
                        f"step {step.id!r} references resources but has no resource.upsert "
                        "grant. Add an mcp_tool_grants entry if the step must write "
                        "resources during execution."
                    ),
                )
            )
        if step.context_refs and not _tool_granted(grants, "context.query"):
            warnings.append(
                RunPlanIssue(
                    path=f"{path}.context_refs",
                    code="missing_context_query_grant",
                    message=(
                        f"step {step.id!r} references context but has no context.query grant. "
                        "Add a context.query grant with explicit sources and fields if the "
                        "step must fetch bounded context while running."
                    ),
                )
            )
        if (
            step.output_refs
            and not _tool_granted(grants, "artifact.create")
            and _artifact_output_warning_enabled(plan)
        ):
            warnings.append(
                RunPlanIssue(
                    path=f"{path}.output_refs",
                    code="missing_artifact_create_grant",
                    message=(
                        f"step {step.id!r} declares outputs but has no artifact.create grant. "
                        "Add an artifact.create grant if the step should persist output "
                        "artifacts during execution."
                    ),
                )
            )
    return warnings


def run_plan_from_template(
    loaded: LoadedWorkflowTemplate,
    *,
    key: str | None = None,
    title: str | None = None,
    inputs_json: dict[str, Any] | None = None,
    context_snapshot_id: int | None = None,
    selected_context_json: dict[str, Any] | None = None,
    metadata_json: dict[str, Any] | None = None,
    enforce_required_inputs: bool = True,
) -> RunPlanSpec:
    """Create a concrete editable baseline from an inert workflow template."""
    from stackos.workflows.run_plan_template_builder import build_run_plan_from_template

    return build_run_plan_from_template(
        loaded,
        plan_spec_type=RunPlanSpec,
        step_spec_type=RunPlanStepSpec,
        approval_spec_type=RunPlanApprovalSpec,
        resolve_action_contract_refs=_template_action_contract_refs,
        key=key,
        title=title,
        inputs_json=inputs_json,
        context_snapshot_id=context_snapshot_id,
        selected_context_json=selected_context_json,
        metadata_json=metadata_json,
        enforce_required_inputs=enforce_required_inputs,
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
    "run_plan_readiness_warnings",
    "validate_run_plan_obj",
]
