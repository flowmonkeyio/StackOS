"""Lazy re-exports for StackOS workflow template primitives."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from stackos.workflows.authoring_guide import (
        WorkflowAuthoringExample as WorkflowAuthoringExample,
    )
    from stackos.workflows.authoring_guide import (
        WorkflowAuthoringGuideOut as WorkflowAuthoringGuideOut,
    )
    from stackos.workflows.authoring_guide import (
        WorkflowAuthoringMode as WorkflowAuthoringMode,
    )
    from stackos.workflows.authoring_guide import (
        WorkflowAuthoringOperationRef as WorkflowAuthoringOperationRef,
    )
    from stackos.workflows.authoring_guide import (
        WorkflowSetupPhase as WorkflowSetupPhase,
    )
    from stackos.workflows.authoring_guide import (
        workflow_authoring_guide as workflow_authoring_guide,
    )
    from stackos.workflows.run_plan_schema import (
        RunPlanSpec as RunPlanSpec,
    )
    from stackos.workflows.run_plan_schema import (
        RunPlanValidationOut as RunPlanValidationOut,
    )
    from stackos.workflows.run_plan_schema import (
        parse_run_plan_obj as parse_run_plan_obj,
    )
    from stackos.workflows.run_plan_schema import (
        run_plan_from_template as run_plan_from_template,
    )
    from stackos.workflows.run_plan_schema import (
        validate_run_plan_obj as validate_run_plan_obj,
    )
    from stackos.workflows.template_loader import (
        LoadedWorkflowTemplate as LoadedWorkflowTemplate,
    )
    from stackos.workflows.template_loader import (
        WorkflowTemplateExtensionDeleteOut as WorkflowTemplateExtensionDeleteOut,
    )
    from stackos.workflows.template_loader import (
        WorkflowTemplateExtensionGetOut as WorkflowTemplateExtensionGetOut,
    )
    from stackos.workflows.template_loader import (
        WorkflowTemplateExtensionListOut as WorkflowTemplateExtensionListOut,
    )
    from stackos.workflows.template_loader import (
        WorkflowTemplateExtensionOut as WorkflowTemplateExtensionOut,
    )
    from stackos.workflows.template_loader import (
        WorkflowTemplateExtensionUpsertOut as WorkflowTemplateExtensionUpsertOut,
    )
    from stackos.workflows.template_loader import (
        WorkflowTemplateExtensionValidationOut as WorkflowTemplateExtensionValidationOut,
    )
    from stackos.workflows.template_loader import (
        WorkflowTemplateListOut as WorkflowTemplateListOut,
    )
    from stackos.workflows.template_loader import (
        WorkflowTemplateLoader as WorkflowTemplateLoader,
    )
    from stackos.workflows.template_loader import (
        WorkflowTemplateSummaryOut as WorkflowTemplateSummaryOut,
    )
    from stackos.workflows.template_schema import (
        WorkflowAgentRequirementSpec as WorkflowAgentRequirementSpec,
    )
    from stackos.workflows.template_schema import (
        WorkflowSkillPresetRequirementSpec as WorkflowSkillPresetRequirementSpec,
    )
    from stackos.workflows.template_schema import (
        WorkflowSkillRequirementSpec as WorkflowSkillRequirementSpec,
    )
    from stackos.workflows.template_schema import (
        WorkflowTemplateSpec as WorkflowTemplateSpec,
    )
    from stackos.workflows.template_schema import (
        WorkflowTemplateValidationOut as WorkflowTemplateValidationOut,
    )
    from stackos.workflows.template_schema import (
        parse_workflow_template_obj as parse_workflow_template_obj,
    )
    from stackos.workflows.template_schema import (
        parse_workflow_template_yaml as parse_workflow_template_yaml,
    )
    from stackos.workflows.template_schema import (
        validate_workflow_template_obj as validate_workflow_template_obj,
    )
    from stackos.workflows.template_schema import (
        validate_workflow_template_yaml as validate_workflow_template_yaml,
    )

_EXPORTS: dict[str, str] = {
    "LoadedWorkflowTemplate": "stackos.workflows.template_loader",
    "RunPlanSpec": "stackos.workflows.run_plan_schema",
    "RunPlanValidationOut": "stackos.workflows.run_plan_schema",
    "WorkflowAgentRequirementSpec": "stackos.workflows.template_schema",
    "WorkflowAuthoringExample": "stackos.workflows.authoring_guide",
    "WorkflowAuthoringGuideOut": "stackos.workflows.authoring_guide",
    "WorkflowAuthoringMode": "stackos.workflows.authoring_guide",
    "WorkflowAuthoringOperationRef": "stackos.workflows.authoring_guide",
    "WorkflowSetupPhase": "stackos.workflows.authoring_guide",
    "WorkflowSkillPresetRequirementSpec": "stackos.workflows.template_schema",
    "WorkflowSkillRequirementSpec": "stackos.workflows.template_schema",
    "WorkflowTemplateExtensionDeleteOut": "stackos.workflows.template_loader",
    "WorkflowTemplateExtensionGetOut": "stackos.workflows.template_loader",
    "WorkflowTemplateExtensionListOut": "stackos.workflows.template_loader",
    "WorkflowTemplateExtensionOut": "stackos.workflows.template_loader",
    "WorkflowTemplateExtensionUpsertOut": "stackos.workflows.template_loader",
    "WorkflowTemplateExtensionValidationOut": "stackos.workflows.template_loader",
    "WorkflowTemplateListOut": "stackos.workflows.template_loader",
    "WorkflowTemplateLoader": "stackos.workflows.template_loader",
    "WorkflowTemplateSpec": "stackos.workflows.template_schema",
    "WorkflowTemplateSummaryOut": "stackos.workflows.template_loader",
    "WorkflowTemplateValidationOut": "stackos.workflows.template_schema",
    "parse_run_plan_obj": "stackos.workflows.run_plan_schema",
    "parse_workflow_template_obj": "stackos.workflows.template_schema",
    "parse_workflow_template_yaml": "stackos.workflows.template_schema",
    "run_plan_from_template": "stackos.workflows.run_plan_schema",
    "validate_run_plan_obj": "stackos.workflows.run_plan_schema",
    "validate_workflow_template_obj": "stackos.workflows.template_schema",
    "validate_workflow_template_yaml": "stackos.workflows.template_schema",
    "workflow_authoring_guide": "stackos.workflows.authoring_guide",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value
