"""Workflow template operation contracts."""

from __future__ import annotations

from stackos.mcp.contract import WriteEnvelope
from stackos.mcp.tools.workflows import (
    WorkflowTemplateDescribeInput,
    WorkflowTemplateForkInput,
    WorkflowTemplateListInput,
    WorkflowTemplateSaveInput,
    WorkflowTemplateValidateInput,
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
    WorkflowTemplateListOut,
    WorkflowTemplateValidationOut,
)


def operation_specs():
    return [
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
