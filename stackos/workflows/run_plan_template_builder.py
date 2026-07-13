"""Materialize workflow templates into concrete run-plan models.

This module owns template defaults, project-extension overlays, and derived
grant snapshots. Model classes and action-contract resolution are injected by
the schema facade to keep dependency direction acyclic.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from stackos.workflows.template_loader import LoadedWorkflowTemplate


def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = _deep_merge(merged[key], value) if key in merged else value
        return merged
    return override


def _is_missing_required_input(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _default_mcp_tool_grants(
    spec: Any,
    steps: list[Any],
    *,
    resource_contract_map: dict[str, str],
) -> list[dict[str, Any]]:
    context_requirements = {item.id: item for item in spec.context_requirements}
    auto_artifact_grants = (spec.metadata_json or {}).get("artifact_grant_policy") != "explicit"
    grants: list[dict[str, Any]] = []
    for step in steps:
        if step.action_refs:
            grants.append(
                {"step_id": step.id, "tool": "action.execute", "action_refs": step.action_refs}
            )
        for resource_ref in dict.fromkeys(step.resource_refs):
            grants.append(
                {
                    "step_id": step.id,
                    "tool": "resource.upsert",
                    "resource_key": resource_contract_map.get(resource_ref, resource_ref),
                }
            )
        for context_ref in step.context_refs:
            requirement = context_requirements.get(context_ref)
            if requirement is None or not requirement.fields:
                continue
            grants.append(
                {
                    "step_id": step.id,
                    "tool": "context.query",
                    "sources": [requirement.source],
                    "fields": sorted(set(requirement.fields)),
                }
            )
        if step.output_refs and auto_artifact_grants:
            grants.append(
                {
                    "step_id": step.id,
                    "tools": [
                        "artifact.create",
                        "artifact.update",
                        "artifact.archive",
                        "artifact.supersede",
                    ],
                }
            )
    return grants


def _template_mcp_tool_grants(
    spec: Any,
    *,
    action_contract_refs: dict[str, str],
) -> list[dict[str, Any]]:
    raw_entries = (spec.metadata_json or {}).get("mcp_tool_grants")
    if raw_entries is None:
        return []
    if not isinstance(raw_entries, list):
        raise ValueError("metadata.mcp_tool_grants must be a list")
    grants: list[dict[str, Any]] = []
    for index, item in enumerate(raw_entries):
        if not isinstance(item, dict):
            raise ValueError(f"metadata.mcp_tool_grants[{index}] must be an object")
        grant = dict(item)
        raw_action_refs = grant.get("action_refs", grant.get("action_ref"))
        if raw_action_refs is not None:
            action_refs = (
                [raw_action_refs]
                if isinstance(raw_action_refs, str)
                else _string_list(raw_action_refs)
            )
            grant["action_refs"] = [
                action_contract_refs.get(action_ref, action_ref) for action_ref in action_refs
            ]
            grant.pop("action_ref", None)
        grants.append(grant)
    return grants


def build_run_plan_from_template(
    loaded: LoadedWorkflowTemplate,
    *,
    plan_spec_type: type[Any],
    step_spec_type: type[Any],
    approval_spec_type: type[Any],
    resolve_action_contract_refs: Callable[..., dict[str, str]],
    key: str | None = None,
    title: str | None = None,
    inputs_json: dict[str, Any] | None = None,
    context_snapshot_id: int | None = None,
    selected_context_json: dict[str, Any] | None = None,
    metadata_json: dict[str, Any] | None = None,
    enforce_required_inputs: bool = True,
) -> Any:
    """Create a concrete editable baseline from an inert workflow template."""
    spec = loaded.spec
    extension = loaded.project_extension if loaded.project_extension is not None else None
    active_extension = extension if extension is not None and extension.enabled else None
    effective_inputs = {
        item.key: item.default_json for item in spec.inputs if item.default_json is not None
    }
    if active_extension is not None:
        effective_inputs.update(active_extension.input_defaults_json)
    effective_inputs.update(inputs_json or {})
    if enforce_required_inputs:
        required_input_keys = [item.key for item in spec.inputs if item.required]
        if active_extension is not None:
            required_input_keys.extend(active_extension.required_input_keys_json)
        seen_required: set[str] = set()
        missing_inputs: list[str] = []
        for input_key in required_input_keys:
            if input_key in seen_required:
                continue
            seen_required.add(input_key)
            if _is_missing_required_input(effective_inputs.get(input_key)):
                missing_inputs.append(input_key)
        if missing_inputs:
            raise ValueError("workflow required inputs are missing: " + ", ".join(missing_inputs))

    effective_selected_context = (
        _deep_merge(active_extension.selected_context_json, selected_context_json or {})
        if active_extension is not None
        else selected_context_json
    )
    extension_metadata: dict[str, Any] = {}
    if active_extension is not None:
        extension_metadata = {
            "workflow_extension": {
                "id": active_extension.id,
                "project_id": active_extension.project_id,
                "workflow_key": active_extension.workflow_key,
                "metadata_json": active_extension.metadata_json,
                "guardrails_json": active_extension.guardrails_json,
                "required_input_keys_json": active_extension.required_input_keys_json,
                "template_overrides_json": active_extension.template_overrides_json,
            }
        }
    effective_metadata = _deep_merge(extension_metadata, metadata_json or {})
    approvals = [
        approval_spec_type(
            key=gate.key,
            title=gate.key.replace("-", " ").replace("_", " ").title(),
            description=gate.description,
            required_when=gate.required_when,
            approver=gate.approver,
            metadata_json=gate.config_json or None,
        )
        for gate in spec.approval_gates
    ]
    steps: list[Any] = []
    step_overrides = active_extension.step_overrides_json if active_extension is not None else {}
    template_plugin_slug = loaded.summary.plugin_slug
    action_contract_refs = resolve_action_contract_refs(
        spec.action_contracts,
        template_plugin_slug=template_plugin_slug,
    )
    resource_contract_map = {item.key: item.resource for item in spec.resource_contracts}
    output_contract_map = {item.key: item for item in spec.outputs}
    for index, step in enumerate(spec.steps):
        override = step_overrides.get(step.id) if isinstance(step_overrides, dict) else None
        override = override if isinstance(override, dict) else {}
        prepend_instructions = _string_list(override.get("instructions_prepend"))
        append_instructions = [
            *_string_list(override.get("extra_instructions")),
            *_string_list(override.get("instructions_append")),
        ]
        append_success = [
            *_string_list(override.get("success_criteria")),
            *_string_list(override.get("success_criteria_append")),
        ]
        step_metadata = step.extensions_json
        if active_extension is not None and override:
            step_metadata = _deep_merge(
                step_metadata or {},
                {
                    "workflow_extension": {
                        "extension_id": active_extension.id,
                        "override": override,
                    }
                },
            )
            if isinstance(override.get("metadata"), dict):
                step_metadata = _deep_merge(step_metadata, override["metadata"])
            if isinstance(override.get("metadata_json"), dict):
                step_metadata = _deep_merge(step_metadata, override["metadata_json"])
        expected_outputs = {
            output_ref: output_contract_map[output_ref].model_dump(
                mode="json",
                exclude_none=True,
                by_alias=True,
            )
            for output_ref in step.output_refs
            if output_ref in output_contract_map
        }
        steps.append(
            step_spec_type(
                id=step.id,
                title=step.title,
                purpose=step.purpose,
                position=index,
                depends_on=step.depends_on,
                input_refs=step.input_refs,
                context_refs=step.context_refs,
                action_refs=[action_contract_refs.get(ref, ref) for ref in step.action_refs],
                resource_refs=step.resource_refs,
                policy_refs=step.policy_refs,
                approval_refs=step.approval_refs,
                output_refs=step.output_refs,
                instructions=[*prepend_instructions, *step.instructions, *append_instructions],
                success_criteria=[*step.success_criteria, *append_success],
                expected_outputs_json=expected_outputs or None,
                metadata_json=step_metadata,
            )
        )
    grants = {
        "template_plugin_slug": template_plugin_slug,
        "capability_requirements": [
            item.model_dump(mode="json", exclude_none=True) for item in spec.capability_requirements
        ],
        "auth_requirements": [
            item.model_dump(mode="json", exclude_none=True) for item in spec.auth_requirements
        ],
        "action_contracts": [
            item.model_dump(mode="json", exclude_none=True) for item in spec.action_contracts
        ],
        "resolved_action_contracts": [
            {"key": contract_key, "action_ref": action_ref}
            for contract_key, action_ref in action_contract_refs.items()
        ],
        "resource_contracts": [
            item.model_dump(mode="json", exclude_none=True) for item in spec.resource_contracts
        ],
        "artifact_grant_policy": (spec.metadata_json or {}).get(
            "artifact_grant_policy", "auto_output_refs"
        ),
        "mcp_tool_grants": _default_mcp_tool_grants(
            spec, steps, resource_contract_map=resource_contract_map
        )
        + _template_mcp_tool_grants(spec, action_contract_refs=action_contract_refs),
    }
    return plan_spec_type(
        key=key or f"{spec.key}.run",
        title=title or spec.name,
        goal=spec.description,
        template_key=spec.key,
        template_version=spec.version,
        template_source=loaded.summary.source,
        context_snapshot_id=context_snapshot_id,
        inputs_json=effective_inputs,
        selected_context_json=effective_selected_context,
        context_filters_json={
            "requirements": [
                item.model_dump(mode="json", exclude_none=True)
                for item in spec.context_requirements
            ]
        },
        grant_snapshot_json=grants,
        policy_snapshot_json={
            "policies": [item.model_dump(mode="json", exclude_none=True) for item in spec.policies],
            "approval_gates": [
                item.model_dump(mode="json", exclude_none=True) for item in spec.approval_gates
            ],
        },
        output_contract_json={
            "outputs": [item.model_dump(mode="json", exclude_none=True) for item in spec.outputs]
        },
        steps=steps,
        approvals=approvals,
        metadata_json=effective_metadata or None,
    )
