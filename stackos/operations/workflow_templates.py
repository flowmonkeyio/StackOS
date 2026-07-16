"""Workflow template operation contracts."""

from __future__ import annotations

from stackos.mcp.contract import WriteEnvelope
from stackos.mcp.tools.workflows import (
    WorkflowExtensionDeleteInput,
    WorkflowExtensionGetInput,
    WorkflowExtensionListInput,
    WorkflowExtensionUpsertInput,
    WorkflowExtensionValidateInput,
    WorkflowTemplateAuthoringGuideInput,
    WorkflowTemplateDescribeInput,
    WorkflowTemplateForkInput,
    WorkflowTemplateListInput,
    WorkflowTemplateSaveInput,
    WorkflowTemplateValidateInput,
    _extension_delete,
    _extension_get,
    _extension_list,
    _extension_upsert,
    _extension_validate,
    _template_authoring_guide,
    _template_describe,
    _template_fork,
    _template_list,
    _template_save,
    _template_validate,
)
from stackos.operations._helpers import operation_spec
from stackos.operations.spec import OperationExample
from stackos.workflows import (
    LoadedWorkflowTemplate,
    WorkflowAuthoringGuideOut,
    WorkflowTemplateExtensionDeleteOut,
    WorkflowTemplateExtensionGetOut,
    WorkflowTemplateExtensionListOut,
    WorkflowTemplateExtensionUpsertOut,
    WorkflowTemplateExtensionValidationOut,
    WorkflowTemplateListOut,
    WorkflowTemplateValidationOut,
)


def operation_specs():
    return [
        operation_spec(
            name="workflowExtension.list",
            summary="List project workflow extensions.",
            input_model=WorkflowExtensionListInput,
            output_model=WorkflowTemplateExtensionListOut,
            handler=_extension_list,
            purpose=(
                "Use this to audit project-scoped workflow configuration that is layered "
                "over reusable base templates without duplicating them."
            ),
            when_to_use=(
                "A setup audit needs to verify which workflows have project defaults.",
                "An agent needs project-specific route, target, or context refs before "
                "creating a run plan.",
            ),
            returns=("Project workflow extension records keyed by workflow_key.",),
            examples=(OperationExample(title="List extensions", arguments={"project_id": 1}),),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="workflowExtension.get",
            summary="Get one project workflow extension.",
            input_model=WorkflowExtensionGetInput,
            output_model=WorkflowTemplateExtensionGetOut,
            handler=_extension_get,
            purpose=(
                "Use this to inspect the project-specific inputs, selected context, "
                "guardrails, and step guidance that runPlan.create will apply."
            ),
            when_to_use=(
                "Before creating a run from a workflow that depends on project setup refs.",
            ),
            returns=(
                "The extension record, or extension=null when the workflow has no extension.",
            ),
            examples=(
                OperationExample(
                    title="Get support intake extension",
                    arguments={
                        "project_id": 1,
                        "workflow_key": "communications.customer-feedback-intake",
                    },
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="workflowExtension.delete",
            summary="Delete one project workflow extension.",
            input_model=WorkflowExtensionDeleteInput,
            output_model=WriteEnvelope[WorkflowTemplateExtensionDeleteOut],
            handler=_extension_delete,
            purpose=(
                "Use this to remove project-scoped workflow setup entirely when it was a "
                "test, mistake, or no longer applies. This is different from disabling an "
                "extension because no leftover extension row remains."
            ),
            when_to_use=(
                "A setup audit found a stale or test workflow extension.",
                "A project should return to the reusable base workflow with no project overlay.",
            ),
            returns=("The deleted extension record for audit/confirmation.",),
            examples=(
                OperationExample(
                    title="Delete support intake extension",
                    arguments={
                        "project_id": 1,
                        "workflow_key": "communications.customer-feedback-intake",
                    },
                ),
            ),
            mutating=True,
            grant_policy="direct-setup-write",
        ),
        operation_spec(
            name="workflowExtension.validate",
            summary="Validate a project workflow extension without saving it.",
            input_model=WorkflowExtensionValidateInput,
            output_model=WorkflowTemplateExtensionValidationOut,
            handler=_extension_validate,
            purpose=(
                "Use this before saving project workflow context. It validates the effective "
                "extension after applying merge/replace semantics, verifies the base workflow "
                "exists, ensures atomic template overrides resolve to a valid workflow, checks "
                "step overrides reference real steps, and rejects raw secrets."
            ),
            when_to_use=("An operator or agent is drafting project-specific workflow defaults.",),
            returns=("Validation status plus model-readable errors and warnings.",),
            examples=(
                OperationExample(
                    title="Validate route-bound support intake setup",
                    arguments={
                        "project_id": 1,
                        "workflow_key": "communications.customer-feedback-intake",
                        "required_input_keys_json": ["communication_route_ref"],
                        "input_defaults_json": {
                            "communication_route_ref": "communication-route:support-feedback"
                        },
                        "template_overrides_json": {
                            "description": "Support investigation tuned for this project.",
                        },
                    },
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="workflowExtension.upsert",
            summary="Save a project workflow extension.",
            input_model=WorkflowExtensionUpsertInput,
            output_model=WriteEnvelope[WorkflowTemplateExtensionUpsertOut],
            handler=_extension_upsert,
            purpose=(
                "Use this for explicit project setup when the base workflow should stay "
                "generic but a project needs stable route refs, default inputs, selected "
                "context, guardrails, extra step guidance, or atomic workflow-field overrides. "
                "By default this is a safe partial update: omitted fields are preserved."
            ),
            prerequisites=(
                "The referenced workflow template must exist.",
                "Do not include raw secrets; store only safe refs and public/contextual guidance.",
                "Use workflowExtension.validate first for reviewed setup.",
                "Use update_mode='replace' only for a full reviewed rewrite; it clears "
                "omitted fields.",
                "Use clear_fields_json to intentionally clear fields during update_mode='merge'.",
            ),
            examples=(
                OperationExample(
                    title="Configure support intake route",
                    arguments={
                        "project_id": 1,
                        "workflow_key": "communications.customer-feedback-intake",
                        "enabled": True,
                        "required_input_keys_json": ["communication_route_ref"],
                        "input_defaults_json": {
                            "communication_route_ref": "communication-route:support-feedback",
                            "canonical_slack_target_ref": "communication-target:support-triage",
                        },
                        "selected_context_json": {
                            "communication": {
                                "route_ref": "communication-route:support-feedback",
                                "target_ref": "communication-target:support-triage",
                            }
                        },
                        "template_overrides_json": {
                            "description": "Support investigation tuned for this project.",
                        },
                        "update_mode": "merge",
                    },
                ),
                OperationExample(
                    title="Clear stale guardrails without replacing context",
                    arguments={
                        "project_id": 1,
                        "workflow_key": "communications.customer-feedback-intake",
                        "clear_fields_json": ["guardrails_json"],
                        "update_mode": "merge",
                    },
                ),
            ),
            mutating=True,
            grant_policy="direct-setup-write",
        ),
        operation_spec(
            name="workflowTemplate.authoringGuide",
            summary="Return the canonical StackOS workflow authoring guide.",
            input_model=WorkflowTemplateAuthoringGuideInput,
            output_model=WorkflowAuthoringGuideOut,
            handler=_template_authoring_guide,
            purpose=(
                "Use this as the single source of truth for selecting an explicit workflow "
                "intent mode, creating or extending a reusable method, setting up project-adapted "
                "agents without creating execution state, and executing only after strict "
                "validation."
            ),
            when_to_use=(
                "An agent outside the StackOS source checkout needs to author or "
                "change a workflow.",
                "A caller needs to decide between a new template, workflow extension, "
                "or one-off run plan.",
                "Docs, skills, or operation descriptions need to point to the "
                "canonical workflow authoring contract.",
            ),
            returns=(
                "Compact mode returns intent modes, deterministic setup phases, agent and "
                "prerequisite policies, completion proof, and canonical operations. Raw mode "
                "adds complete project/plugin authoring paths, schema/review gates, and examples.",
            ),
            examples=(
                OperationExample(title="Read compact setup contract", arguments={}),
                OperationExample(
                    title="Read complete project/plugin authoring contract",
                    arguments={"response_mode": "raw"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="workflowTemplate.list",
            summary="List effective reusable workflow templates without executing them.",
            input_model=WorkflowTemplateListInput,
            output_model=WorkflowTemplateListOut,
            handler=_template_list,
            purpose=(
                "Use this to discover reusable workflow contracts. Templates are inert; "
                "runPlan.create turns a selected template into concrete project work."
            ),
            when_to_use=(
                "An agent needs available workflows for the workspace-bound project.",
                "A setup audit needs to verify plugin workflow templates are visible.",
            ),
            returns=("Template keys, plugin/source metadata, and effective shadowing state.",),
            examples=(
                OperationExample(
                    title="List engineering templates",
                    arguments={"project_id": 1, "plugin_slug": "engineering"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="workflowTemplate.describe",
            summary="Describe one reusable workflow template without executing it.",
            input_model=WorkflowTemplateDescribeInput,
            output_model=LoadedWorkflowTemplate,
            handler=_template_describe,
            purpose=(
                "Use this before creating a run plan. The response contains inputs, steps, "
                "agents, skills, grants, outputs, approval gates, setup notes, and any "
                "enabled project extension layered over the reusable base workflow."
            ),
            when_to_use=("The agent has a template key such as engineering.tracked-delivery.",),
            returns=("One loaded template with normalized workflow metadata.",),
            examples=(
                OperationExample(
                    title="Describe tracked delivery",
                    arguments={"project_id": 1, "key": "engineering.tracked-delivery"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="workflowTemplate.validate",
            summary="Validate a workflow template or preview a template by key.",
            input_model=WorkflowTemplateValidateInput,
            output_model=WorkflowTemplateValidationOut,
            handler=_template_validate,
            purpose=(
                "Use this after workflowTemplate.authoringGuide to validate template JSON/YAML "
                "before saving, or to validate an existing template key before runPlan.create."
            ),
            when_to_use=(
                "A caller drafted template_json/template_yaml and wants validation only.",
                "An agent wants to verify an existing template key is parseable.",
                "An agent outside the StackOS source repo needs model-readable errors while "
                "building a workflow from the canonical authoring guide.",
            ),
            returns=("Validation status plus model-readable errors and warnings.",),
            examples=(
                OperationExample(
                    title="Validate existing template",
                    arguments={"project_id": 1, "key": "engineering.tracked-delivery"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="workflowTemplate.save",
            summary="Save a project/user workflow template.",
            input_model=WorkflowTemplateSaveInput,
            output_model=WriteEnvelope[LoadedWorkflowTemplate],
            handler=_template_save,
            purpose=(
                "Use this only for explicit local-admin workflow template setup after "
                "workflowTemplate.validate succeeds and the operator wants the draft to become "
                "a reusable project or user template."
            ),
            prerequisites=(
                "Read workflowTemplate.authoringGuide first.",
                "Requires operator/admin authority and reviewed template JSON/YAML.",
                "Use workflowExtension.validate/upsert instead when the base workflow should "
                "stay reusable and only project defaults, context, guardrails, agent/skill "
                "requirements, or step guidance need to change.",
            ),
            examples=(
                OperationExample(
                    title="Save project template",
                    arguments={
                        "project_id": 1,
                        "template_json": {
                            "schema_version": "stackos.workflow-template.v1",
                            "key": "custom.flow",
                            "name": "Custom Flow",
                            "version": "0.1.0",
                            "steps": [
                                {
                                    "id": "complete-work",
                                    "title": "Complete Work",
                                    "purpose": "Complete the bounded request.",
                                }
                            ],
                        },
                    },
                ),
            ),
            mutating=True,
            grant_policy="local-admin-workflow-template-write",
        ),
        operation_spec(
            name="workflowTemplate.fork",
            summary="Fork an existing workflow template into a project/user template.",
            input_model=WorkflowTemplateForkInput,
            output_model=WriteEnvelope[LoadedWorkflowTemplate],
            handler=_template_fork,
            purpose=(
                "Use this only for explicit local-admin template customization when the "
                "result should become a separately named reusable workflow identity."
            ),
            prerequisites=(
                "Read workflowTemplate.authoringGuide first.",
                "Requires operator/admin authority and a new stable template key.",
                "Validate project-specific overlays as workflow extensions before deciding "
                "that a forked template identity is actually needed.",
            ),
            examples=(
                OperationExample(
                    title="Fork template",
                    arguments={
                        "project_id": 1,
                        "key": "engineering.tracked-delivery",
                        "new_key": "company.tracked-delivery",
                    },
                ),
            ),
            mutating=True,
            grant_policy="local-admin-workflow-template-write",
        ),
    ]


__all__ = ["operation_specs"]
