"""HubSpot customer-lifecycle workflow contract tests."""

from __future__ import annotations

from typing import Any

import pytest
from jsonschema import validate
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from sqlmodel import Session

from stackos.plugins.manifest import BUILTIN_PLUGIN_MANIFESTS
from stackos.repositories.run_plans import RunPlanRepository
from stackos.workflows.run_plan_schema import run_plan_from_template
from stackos.workflows.template_loader import WorkflowTemplateLoader

HUBSPOT_WORKFLOW_KEYS = {
    "gtm.crm-hygiene-pass",
    "gtm.pipeline-risk-review",
    "gtm.marketing-program-lifecycle",
    "gtm.crm-export-handoff",
    "gtm.customer-follow-up",
}

DEFERRED_OR_ADMIN_ACTIONS = {
    "hubspot.sales.sequences.list",
    "hubspot.sales.sequences.enroll",
    "hubspot.sales.sequences.unenroll",
    "hubspot.marketing.emails.publish",
    "hubspot.marketing.emails.bulk_send",
    "hubspot.bulk.imports.create",
    "hubspot.webhooks.subscriptions.configure",
    "hubspot.automation.workflow_actions.register",
    "hubspot.transactional.single_email.send",
}


def _gtm_manifest() -> Any:
    return next(item for item in BUILTIN_PLUGIN_MANIFESTS if item.slug == "gtm")


def _described(loader: WorkflowTemplateLoader, key: str) -> Any:
    return loader.describe_template(key=key, plugin_slug="gtm")


def _action_contracts(described: Any) -> dict[str, Any]:
    return {item.key: item for item in described.spec.action_contracts}


def _step_grants(plan: Any, step_id: str) -> list[dict[str, Any]]:
    return [
        grant
        for grant in plan.grant_snapshot_json["mcp_tool_grants"]
        if grant["step_id"] == step_id
    ]


def _schema_property_names(schema: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    properties = schema.get("properties")
    if isinstance(properties, dict):
        names.update(str(key) for key in properties)
        for value in properties.values():
            if isinstance(value, dict):
                names.update(_schema_property_names(value))
    for key in ("items", "allOf", "anyOf", "oneOf", "if", "then", "else"):
        value = schema.get(key)
        if isinstance(value, dict):
            names.update(_schema_property_names(value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    names.update(_schema_property_names(item))
    return names


def test_hubspot_customer_lifecycle_workflows_are_focused_and_discoverable(
    session: Session,
) -> None:
    loader = WorkflowTemplateLoader(session)
    listed = {item.key for item in loader.list_templates(plugin_slug="gtm").templates}

    assert listed >= HUBSPOT_WORKFLOW_KEYS

    all_referenced_actions: set[str] = set()
    for workflow_key in HUBSPOT_WORKFLOW_KEYS:
        described = _described(loader, workflow_key)
        assert described.spec.experience is not None
        assert described.spec.public is not None
        assert described.spec.when_to_use
        assert described.spec.when_not_to_use
        assert described.spec.agent_requirements
        assert all(item.applies_to_steps for item in described.spec.agent_requirements)
        all_referenced_actions.update(
            item.action for item in described.spec.action_contracts if item.action
        )

    assert DEFERRED_OR_ADMIN_ACTIONS.isdisjoint(all_referenced_actions)

    marketing = _described(loader, "gtm.marketing-program-lifecycle")
    marketing_actions = {item.action for item in marketing.spec.action_contracts}
    assert {
        "hubspot.marketing.campaigns.list",
        "hubspot.marketing.emails.list",
        "hubspot.marketing.segments.list",
        "hubspot.marketing.subscription_types.list",
        "hubspot.marketing.campaigns.create",
        "hubspot.marketing.campaigns.update",
        "hubspot.marketing.emails.create",
        "hubspot.marketing.emails.update",
        "hubspot.marketing.segments.memberships.add",
        "hubspot.marketing.segments.memberships.remove",
        "hubspot.marketing.contact_preferences.get",
        "hubspot.marketing.contact_preferences.update",
    } <= marketing_actions

    pipeline = _described(loader, "gtm.pipeline-risk-review")
    pipeline_actions = {item.action for item in pipeline.spec.action_contracts}
    assert {
        "hubspot.crm.deals.search",
        "hubspot.sales.products.search",
        "hubspot.sales.line_items.search",
        "hubspot.sales.quotes.search",
        "hubspot.sales.goal_targets.list",
    } <= pipeline_actions


def test_hubspot_workflows_select_only_the_capabilities_the_job_needs(
    session: Session,
) -> None:
    loader = WorkflowTemplateLoader(session)
    manifest = _gtm_manifest()
    manifest_actions = {item.key: item for item in manifest.actions}
    manifest_resources = {item.key: item for item in manifest.resources}

    for resource_key in ("marketing-program", "crm-export"):
        resource = manifest_resources[resource_key]
        assert resource.schema_data["additionalProperties"] is False
        assert resource.ui_schema
        assert resource.config["schema_version"] == "stackos.resource.v1"
        assert resource.config["record_kind"] == resource_key
        assert resource.config["agent_guidance"]

    crm_hygiene = _described(loader, "gtm.crm-hygiene-pass")
    crm_hubspot_actions = [
        manifest_actions[item.action]
        for item in crm_hygiene.spec.action_contracts
        if item.provider == "hubspot" and item.action
    ]
    assert crm_hubspot_actions
    assert {item.config["readiness_group"] for item in crm_hubspot_actions} == {"crm-core"}

    pipeline = _described(loader, "gtm.pipeline-risk-review")
    pipeline_groups = {
        manifest_actions[item.action].config["readiness_group"]
        for item in pipeline.spec.action_contracts
        if item.provider == "hubspot" and item.action
    }
    assert pipeline_groups == {"crm-core", "sales"}

    marketing = _described(loader, "gtm.marketing-program-lifecycle")
    assert {
        manifest_actions[item.action].config["readiness_group"]
        for item in marketing.spec.action_contracts
        if item.action
    } == {"marketing"}
    marketing_auth = next(item for item in marketing.spec.auth_requirements if not item.optional)
    assert "marketing.campaigns.read" in marketing_auth.scopes
    assert "crm.export" not in marketing_auth.scopes
    assert "transactional-email" not in marketing_auth.scopes

    export = _described(loader, "gtm.crm-export-handoff")
    export_auth = next(item for item in export.spec.auth_requirements if not item.optional)
    assert export_auth.scopes == ["crm.export"]
    assert {
        manifest_actions[item.action].config["readiness_group"]
        for item in export.spec.action_contracts
        if item.action
    } == {"bulk"}

    followup = _described(loader, "gtm.customer-follow-up")
    followup_auth = next(item for item in followup.spec.auth_requirements if not item.optional)
    assert set(followup_auth.scopes) == {
        "crm.objects.contacts.read",
        "crm.objects.contacts.write",
        "transactional-email",
    }
    assert {
        manifest_actions[item.action].config["readiness_group"]
        for item in followup.spec.action_contracts
        if item.action
    } == {"crm-core"}


def test_customer_follow_up_uses_the_generic_communication_flow_with_exact_grants(
    session: Session,
) -> None:
    described = _described(WorkflowTemplateLoader(session), "gtm.customer-follow-up")
    plan = run_plan_from_template(
        described,
        inputs_json={
            "goal": "Send one approved customer follow-up and record the outcome.",
            "message_packet": {
                "template_ref": "provider-object:hubspot-email:template-safe",
                "template_data": {"case_number": "CASE-42"},
                "transactional_use_confirmed": True,
                "consent_or_relationship_confirmed": True,
                "legal_basis": "EXISTING_CUSTOMER_RELATIONSHIP",
                "legal_basis_explanation": "Approved service follow-up for an active customer.",
                "marketing_contact_state": "non-marketing",
            },
            "delivery_key": "case-42-follow-up-v1",
        },
    )

    send_grants = _step_grants(plan, "send_followup")
    communication_grants = [
        grant for grant in send_grants if grant.get("tool") == "communication.send"
    ]
    assert communication_grants == [
        {
            "step_id": "send_followup",
            "tool": "communication.send",
            "targets": ["communication-target:hubspot-customer-follow-up"],
        }
    ]
    assert all(grant.get("tool") != "action.execute" for grant in send_grants)
    assert (
        "customer_followup_review"
        in next(step for step in plan.steps if step.id == "send_followup").approval_refs
    )
    assert all(
        item.action != "hubspot.transactional.single_email.send"
        for item in described.spec.action_contracts
    )

    record_grants = _step_grants(plan, "record_followup")
    record_action_grant = next(
        grant for grant in record_grants if grant.get("tool") == "action.execute"
    )
    assert set(record_action_grant["action_refs"]) == {
        "gtm.hubspot.crm.notes.create",
        "gtm.hubspot.crm.tasks.create",
        "gtm.hubspot.crm.calls.create",
        "gtm.hubspot.crm.meetings.create",
    }


def test_high_risk_workflow_branches_have_approval_idempotency_and_safe_evidence(
    session: Session,
) -> None:
    loader = WorkflowTemplateLoader(session)
    manifest_actions = {item.key: item for item in _gtm_manifest().actions}

    marketing = _described(loader, "gtm.marketing-program-lifecycle")
    consent = _action_contracts(marketing)["hubspot_contact_preference_update"]
    assert consent.risk_level == "high"
    assert consent.approval_ref == "consent_change_review"
    consent_action = manifest_actions[consent.action]
    assert consent_action.config["idempotency_policy"]
    assert consent_action.config["confirmation_policy"]

    export = _described(loader, "gtm.crm-export-handoff")
    start_export = _action_contracts(export)["hubspot_export_create"]
    assert start_export.risk_level == "high"
    assert start_export.approval_ref == "export_review"
    export_action = manifest_actions[start_export.action]
    assert export_action.config["idempotency_policy"]
    assert export_action.config["confirmation_policy"]

    for workflow_key in HUBSPOT_WORKFLOW_KEYS:
        described = _described(loader, workflow_key)
        serialized = described.spec.model_dump_json()
        assert "hubspot.webhooks" not in serialized
        assert "hubspot.automation" not in serialized
        assert "runPlan.create" not in serialized
        assert "runPlan.start" not in serialized

        for contract in described.spec.action_contracts:
            if not contract.action or not contract.action.startswith("hubspot."):
                continue
            action = manifest_actions[contract.action]
            assert action.config.get("connector") == "hubspot"
            assert action.config.get("execution_mode") != "deferred"
            forbidden_raw_ids = {
                "contact_id",
                "company_id",
                "deal_id",
                "campaign_id",
                "email_id",
                "list_id",
                "owner_id",
                "job_id",
            }
            assert forbidden_raw_ids.isdisjoint(_schema_property_names(action.input_schema))

    marketing_summary = next(
        item for item in marketing.spec.outputs if item.key == "program_summary"
    )
    assert {
        "action_call_refs",
        "provider_request_ids",
        "partial_failures",
        "retry_guidance",
    } <= set(marketing_summary.schema_data["required"])
    export_summary = next(item for item in export.spec.outputs if item.key == "export_summary")
    assert {
        "job_ref",
        "action_call_refs",
        "provider_request_ids",
        "retention_disposition",
    } <= set(export_summary.schema_data["required"])
    assert {"artifact_ref", "record_count"}.isdisjoint(export_summary.schema_data["required"])
    pending_summary = {
        "status": "pending",
        "job_ref": "provider-object:export-job",
        "action_call_refs": ["action-call:1"],
        "provider_request_ids": ["request:1"],
        "partial_failures": [],
        "retry_guidance": ["Poll the same job later."],
        "retention_disposition": {"status": "pending"},
    }
    validate(instance=pending_summary, schema=export_summary.schema_data)
    with pytest.raises(JsonSchemaValidationError):
        validate(
            instance={**pending_summary, "status": "complete"},
            schema=export_summary.schema_data,
        )
    validate(
        instance={
            **pending_summary,
            "status": "complete",
            "artifact_ref": "artifact:hubspot-export",
            "record_count": 42,
        },
        schema=export_summary.schema_data,
    )


def test_hubspot_workflows_pass_structural_and_strict_run_plan_validation(
    session: Session,
    project_id: int,
) -> None:
    repo = RunPlanRepository(session)
    strict_inputs = {
        "gtm.crm-hygiene-pass": {"goal": "Review CRM hygiene."},
        "gtm.pipeline-risk-review": {"goal": "Review pipeline risk."},
        "gtm.marketing-program-lifecycle": {
            "goal": "Prepare and apply an approved marketing program update.",
            "program_scope": {
                "campaign_refs": ["provider-object:hubspot-campaign:campaign-safe"],
                "audience_segment_refs": ["provider-object:hubspot-segment:segment-safe"],
            },
        },
        "gtm.crm-export-handoff": {
            "goal": "Export a bounded CRM dataset.",
            "export_spec": {
                "object_type": "contacts",
                "property_refs": ["provider-object:hubspot-property:email"],
                "format": "CSV",
            },
            "export_key": "customer-audit-2026-07-22",
            "retention_policy": {"delete_after_days": 30},
        },
        "gtm.customer-follow-up": {
            "goal": "Send one approved customer follow-up.",
            "message_packet": {
                "template_ref": "provider-object:hubspot-email:template-safe",
                "template_data": {"case_number": "CASE-42"},
                "transactional_use_confirmed": True,
                "consent_or_relationship_confirmed": True,
                "legal_basis": "EXISTING_CUSTOMER_RELATIONSHIP",
                "legal_basis_explanation": "Approved service follow-up for an active customer.",
                "marketing_contact_state": "non-marketing",
            },
            "delivery_key": "case-42-follow-up-v1",
        },
    }

    for workflow_key, inputs in strict_inputs.items():
        structural = repo.validate_plan(
            project_id=project_id,
            template_key=workflow_key,
            plugin_slug="gtm",
            enforce_required_inputs=False,
        )
        strict = repo.validate_plan(
            project_id=project_id,
            template_key=workflow_key,
            plugin_slug="gtm",
            inputs_json=inputs,
            enforce_required_inputs=True,
        )
        assert structural.valid is True, (workflow_key, structural.errors)
        assert structural.warnings == [], (workflow_key, structural.warnings)
        assert strict.valid is True, (workflow_key, strict.errors)
        assert strict.warnings == [], (workflow_key, strict.warnings)
