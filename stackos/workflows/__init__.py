"""StackOS workflow template primitives."""

from __future__ import annotations

from stackos.workflows.authoring_guide import (
    WorkflowAuthoringExample,
    WorkflowAuthoringGuideOut,
    WorkflowAuthoringOperationRef,
    workflow_authoring_guide,
)
from stackos.workflows.run_plan_schema import (
    RunPlanSpec,
    RunPlanValidationOut,
    parse_run_plan_obj,
    run_plan_from_template,
    validate_run_plan_obj,
)
from stackos.workflows.template_loader import (
    LoadedWorkflowTemplate,
    WorkflowTemplateExtensionDeleteOut,
    WorkflowTemplateExtensionGetOut,
    WorkflowTemplateExtensionListOut,
    WorkflowTemplateExtensionOut,
    WorkflowTemplateExtensionUpsertOut,
    WorkflowTemplateExtensionValidationOut,
    WorkflowTemplateListOut,
    WorkflowTemplateLoader,
    WorkflowTemplateSummaryOut,
)
from stackos.workflows.template_schema import (
    WorkflowAgentRequirementSpec,
    WorkflowSkillPresetRequirementSpec,
    WorkflowSkillRequirementSpec,
    WorkflowTemplateSpec,
    WorkflowTemplateValidationOut,
    parse_workflow_template_obj,
    parse_workflow_template_yaml,
    validate_workflow_template_obj,
    validate_workflow_template_yaml,
)

__all__ = [
    "LoadedWorkflowTemplate",
    "RunPlanSpec",
    "RunPlanValidationOut",
    "WorkflowAgentRequirementSpec",
    "WorkflowAuthoringExample",
    "WorkflowAuthoringGuideOut",
    "WorkflowAuthoringOperationRef",
    "WorkflowSkillPresetRequirementSpec",
    "WorkflowSkillRequirementSpec",
    "WorkflowTemplateExtensionDeleteOut",
    "WorkflowTemplateExtensionGetOut",
    "WorkflowTemplateExtensionListOut",
    "WorkflowTemplateExtensionOut",
    "WorkflowTemplateExtensionUpsertOut",
    "WorkflowTemplateExtensionValidationOut",
    "WorkflowTemplateListOut",
    "WorkflowTemplateLoader",
    "WorkflowTemplateSpec",
    "WorkflowTemplateSummaryOut",
    "WorkflowTemplateValidationOut",
    "parse_run_plan_obj",
    "parse_workflow_template_obj",
    "parse_workflow_template_yaml",
    "run_plan_from_template",
    "validate_run_plan_obj",
    "validate_workflow_template_obj",
    "validate_workflow_template_yaml",
    "workflow_authoring_guide",
]
