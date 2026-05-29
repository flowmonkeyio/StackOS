"""Workflow template operation contracts."""

from __future__ import annotations

from stackos.mcp.contract import WriteEnvelope
from stackos.mcp.tools.workflows import (
    WorkflowExtensionGetInput,
    WorkflowExtensionListInput,
    WorkflowExtensionUpsertInput,
    WorkflowExtensionValidateInput,
    WorkflowTemplateDescribeInput,
    WorkflowTemplateForkInput,
    WorkflowTemplateListInput,
    WorkflowTemplateSaveInput,
    WorkflowTemplateValidateInput,
    _extension_get,
    _extension_list,
    _extension_upsert,
    _extension_validate,
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
    WorkflowTemplateExtensionGetOut,
    WorkflowTemplateExtensionListOut,
    WorkflowTemplateExtensionOut,
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
                    title="Get support investigation extension",
                    arguments={
                        "project_id": 1,
                        "workflow_key": "engineering.customer-support-investigation",
                    },
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        operation_spec(
            name="workflowExtension.validate",
            summary="Validate a project workflow extension without saving it.",
            input_model=WorkflowExtensionValidateInput,
            output_model=WorkflowTemplateExtensionValidationOut,
            handler=_extension_validate,
            purpose=(
                "Use this before saving project workflow context. It validates the base "
                "workflow exists, atomic template overrides resolve to a valid effective "
                "workflow, step overrides reference real steps, and no raw secrets are embedded."
            ),
            when_to_use=("An operator or agent is drafting project-specific workflow defaults.",),
            returns=("Validation status plus model-readable errors and warnings.",),
            examples=(
                OperationExample(
                    title="Validate route-bound support setup",
                    arguments={
                        "project_id": 1,
                        "workflow_key": "engineering.customer-support-investigation",
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
            output_model=WriteEnvelope[WorkflowTemplateExtensionOut],
            handler=_extension_upsert,
            purpose=(
                "Use this for explicit project setup when the base workflow should stay "
                "generic but a project needs stable route refs, default inputs, selected "
                "context, guardrails, extra step guidance, or atomic workflow-field overrides."
            ),
            prerequisites=(
                "The referenced workflow template must exist.",
                "Do not include raw secrets; store only safe refs and public/contextual guidance.",
            ),
            examples=(
                OperationExample(
                    title="Configure support investigation route",
                    arguments={
                        "project_id": 1,
                        "workflow_key": "engineering.customer-support-investigation",
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
                    },
                ),
            ),
            mutating=True,
            grant_policy="direct-setup-write",
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
                "agents, skills, grants, outputs, approval gates, and setup notes."
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
                "Use this to validate template JSON/YAML before saving, or to validate an "
                "existing template key before runPlan.create."
            ),
            when_to_use=(
                "A caller drafted template_json/template_yaml and wants validation only.",
                "An agent wants to verify an existing template key is parseable.",
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
            purpose="Use this only for explicit local-admin workflow template setup.",
            prerequisites=("Requires operator/admin authority and reviewed template JSON/YAML.",),
            examples=(
                OperationExample(
                    title="Save project template",
                    arguments={"project_id": 1, "template_json": {"key": "custom.flow"}},
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
            purpose="Use this only for explicit local-admin template customization.",
            prerequisites=("Requires operator/admin authority and a new stable template key.",),
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
