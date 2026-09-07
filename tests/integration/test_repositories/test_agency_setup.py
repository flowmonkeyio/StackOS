"""Run-plan and resource-persistence proof for the minimal agency setup package."""

from __future__ import annotations

from typing import Any

import pytest
from sqlmodel import Session

from stackos.db.models import RunPlanStepStatus
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.resources import ResourceRepository
from stackos.repositories.run_plans import RunPlanRepository
from stackos.workflows.template_loader import WorkflowTemplateLoader

AGENCY_WORKFLOW_KEYS = {"agency.setup", "agency.project-setup"}
FINANCE_RECEIPT_INTAKE = "finance.receipt-intake"


def _agency_inputs(*, requested: bool = False) -> dict[str, Any]:
    inputs: dict[str, Any] = {
        "agency_profile_ref": "agency:fixture",
        "agency_name": "Fixture Agency",
        "brief_context": "A minimal agency setup fixture.",
        "agency_workspace_ref": "workspace:agency-fixture",
        "guidance": ["Keep work scoped to the current project."],
        "finance_setup_requested": requested,
    }
    if requested:
        inputs["finance_workflow_keys"] = [FINANCE_RECEIPT_INTAKE]
    return inputs


def _project_inputs(
    *,
    engagement_ref: str = "engagement:fixture-one",
    agency_profile_ref: str | None = "agency:fixture",
    requested: bool = False,
) -> dict[str, Any]:
    inputs: dict[str, Any] = {
        "engagement_ref": engagement_ref,
        "engagement_name": engagement_ref.replace(":", " ").title(),
        "client_ref": "client:shared-fixture",
        "description": "A minimal client project context fixture.",
        "guidance": ["Do not infer finance authority."],
        "finance_workspace_association_ref": "finance-navigation:agency-fixture",
        "finance_setup_requested": requested,
    }
    if agency_profile_ref is not None:
        inputs["agency_profile_ref"] = agency_profile_ref
    if requested:
        inputs["finance_workflow_keys"] = [FINANCE_RECEIPT_INTAKE]
    return inputs


def _resource_identity(workflow_key: str, inputs: dict[str, Any]) -> tuple[str, str, str]:
    if workflow_key == "agency.setup":
        return "agency-profile", inputs["agency_profile_ref"], inputs["agency_name"]
    return "project-context", inputs["engagement_ref"], inputs["engagement_name"]


def _find_record(
    resources: ResourceRepository,
    *,
    project_id: int,
    resource_key: str,
    external_id: str,
):
    page = resources.query_records(
        project_id=project_id,
        plugin_slug="agency",
        resource_key=resource_key,
    )
    return next((record for record in page.items if record.external_id == external_id), None)


def _create_agency_profile(session: Session, *, project_id: int, profile_ref: str) -> None:
    ResourceRepository(session).upsert_record(
        project_id=project_id,
        plugin_slug="agency",
        resource_key="agency-profile",
        external_id=profile_ref,
        title="Fixture Agency",
        data_json={
            "agency_name": "Fixture Agency",
            "brief_context": "Association fixture for client-project setup.",
        },
        provenance_json={"source": "agency-association-fixture"},
    )


def _resolved_payload(
    workflow_key: str,
    inputs: dict[str, Any],
    existing_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Model the complete write payload, including omitted-field preservation."""

    if workflow_key == "agency.setup":
        required = ("agency_name", "brief_context")
        optional = ("agency_workspace_ref", "guidance")
    else:
        required = ("engagement_ref", "engagement_name", "client_ref", "description")
        optional = (
            "agency_profile_ref",
            "guidance",
            "finance_workspace_association_ref",
        )

    payload = {
        key: value
        for key, value in (existing_data or {}).items()
        if key in required or key in optional
    }
    payload.update({key: inputs[key] for key in required})
    for key in optional:
        if key in inputs:
            payload[key] = inputs[key]
    return payload


def _finance_handoff(inputs: dict[str, Any]) -> dict[str, Any]:
    requested = bool(inputs.get("finance_setup_requested", False))
    workflow_keys = list(inputs.get("finance_workflow_keys", []))
    if not requested:
        return {
            "requested": False,
            "state": "not-requested",
            "workflow_keys": [],
            "authoring_intent": "none",
            "next_action": "No finance setup handoff requested.",
        }
    if not workflow_keys:
        return {
            "requested": True,
            "state": "selection-required",
            "workflow_keys": [],
            "authoring_intent": "none",
            "next_action": "Select exact finance workflow keys before requesting setup.",
        }
    return {
        "requested": True,
        "state": "handoff-only",
        "workflow_keys": workflow_keys,
        "authoring_intent": "setup_existing",
        "next_action": (
            "Set up the selected finance workflows separately; no finance run was started."
        ),
    }


def _inventory_result(
    workflow_key: str,
    *,
    external_id: str,
    record_exists: bool,
    agency_profile_ref: str | None = None,
    association_resolved: bool = False,
) -> dict[str, Any]:
    if workflow_key == "agency.setup":
        return {
            "agency_inventory": {
                "agency_profile_ref": external_id,
                "profile_state": "existing" if record_exists else "new",
                "existing_profile_refs": [external_id] if record_exists else [],
            }
        }
    return {
        "project_context_inventory": {
            "engagement_ref": external_id,
            "context_state": "existing" if record_exists else "new",
            "existing_context_refs": [external_id] if record_exists else [],
            **({"agency_profile_ref": agency_profile_ref} if agency_profile_ref else {}),
            "agency_association_state": (
                "standalone"
                if agency_profile_ref is None
                else "resolved"
                if association_resolved
                else "unresolved"
            ),
        }
    }


def _payload_result(workflow_key: str, payload: dict[str, Any], external_id: str) -> dict[str, Any]:
    if workflow_key == "agency.setup":
        return {
            "agency_profile_payload": {
                "agency_profile_ref": external_id,
                **payload,
            }
        }
    return {"project_context_payload": payload}


def _write_result(workflow_key: str, external_id: str, write_state: str) -> dict[str, Any]:
    if workflow_key == "agency.setup":
        return {
            "agency_profile_write": {
                "agency_profile_ref": external_id,
                "resource_external_id": external_id,
                "write_state": write_state,
            }
        }
    return {
        "project_context_write": {
            "engagement_ref": external_id,
            "resource_external_id": external_id,
            "write_state": write_state,
        }
    }


def _summary(
    workflow_key: str,
    inputs: dict[str, Any],
    *,
    state: str,
) -> dict[str, Any]:
    handoff = _finance_handoff(inputs)
    if workflow_key == "agency.setup":
        return {
            "agency_setup_summary": {
                "agency_profile_ref": inputs["agency_profile_ref"],
                "record_state": state,
                "finance_setup_handoff": handoff,
                "residual_questions": [],
            }
        }
    return {
        "project_setup_summary": {
            "engagement_ref": inputs["engagement_ref"],
            "project_context_ref": f"project-context:{inputs['engagement_ref']}",
            "record_state": state,
            "agency_association_state": (
                "associated" if inputs.get("agency_profile_ref") else "standalone"
            ),
            "finance_setup_handoff": handoff,
            "residual_questions": [],
        }
    }


def _assert_claim_has_previous_handoff(claimed: Any, *, expected_output: str | None) -> None:
    assert claimed.input_values_json
    if expected_output is None:
        assert claimed.direct_dependency_handoffs == []
        return
    assert claimed.input_refs_json
    assert len(claimed.direct_dependency_handoffs) == 1
    handoff = claimed.direct_dependency_handoffs[0]
    assert handoff.result_json is not None
    assert expected_output in handoff.result_json


def _run_setup(
    session: Session,
    *,
    project_id: int,
    workflow_key: str,
    inputs: dict[str, Any],
):
    """Execute a representative full onboarding run using only its persisted contracts."""

    plans = RunPlanRepository(session)
    resources = ResourceRepository(session)
    resource_key, external_id, title = _resource_identity(workflow_key, inputs)
    existing = _find_record(
        resources,
        project_id=project_id,
        resource_key=resource_key,
        external_id=external_id,
    )
    payload = _resolved_payload(
        workflow_key,
        inputs,
        existing.data_json if existing else None,
    )
    write_state = "updated" if existing else "created"
    association_ref = (
        payload.get("agency_profile_ref") if workflow_key == "agency.project-setup" else None
    )
    association = (
        _find_record(
            resources,
            project_id=project_id,
            resource_key="agency-profile",
            external_id=association_ref,
        )
        if association_ref
        else None
    )
    assert association_ref is None or association is not None, (
        "associated project setup fixture requires a same-project agency-profile; "
        "use the blocked-association helper for an unresolved reference"
    )

    plan = plans.create(
        project_id=project_id,
        template_key=workflow_key,
        plugin_slug="agency",
        inputs_json=inputs,
    ).data
    started = plans.start(plan.id, project_id=project_id).data
    written = None
    previous_output: str | None = None

    for step in plan.steps:
        claimed = plans.claim_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id=step.step_id,
            claimed_by="agency-setup-fixture",
        ).data
        _assert_claim_has_previous_handoff(claimed, expected_output=previous_output)

        if step.step_id.startswith("inventory-"):
            result = _inventory_result(
                workflow_key,
                external_id=external_id,
                record_exists=existing is not None,
                agency_profile_ref=association_ref,
                association_resolved=association is not None,
            )
            previous_output = next(iter(result))
        elif step.step_id == "resolve-supplied-facts":
            result = _payload_result(workflow_key, payload, external_id)
            previous_output = next(iter(result))
        elif step.step_id in {"persist-profile", "persist-project-context"}:
            written = resources.upsert_record(
                project_id=project_id,
                plugin_slug="agency",
                resource_key=resource_key,
                external_id=external_id,
                title=title,
                data_json=payload,
                provenance_json={"source": "agency-setup-fixture"},
            ).data
            result = _write_result(workflow_key, external_id, write_state)
            previous_output = next(iter(result))
        else:
            result = _summary(workflow_key, inputs, state=write_state)
            previous_output = next(iter(result))

        plans.record_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id=step.step_id,
            status=RunPlanStepStatus.SUCCESS,
            result_json=result,
        )

    assert written is not None
    return plan, written


def _run_unresolved_association_setup(
    session: Session,
    *,
    project_id: int,
    inputs: dict[str, Any],
):
    """Prove an unresolved association stops before the granted context write."""

    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project_id,
        template_key="agency.project-setup",
        plugin_slug="agency",
        inputs_json=inputs,
    ).data
    started = plans.start(plan.id, project_id=project_id).data

    inventory = plans.claim_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="inventory-existing-context",
        claimed_by="agency-association-fixture",
    ).data
    assert "existing_agency_profiles" in inventory.context_refs_json
    inventory_result = _inventory_result(
        "agency.project-setup",
        external_id=inputs["engagement_ref"],
        record_exists=False,
        agency_profile_ref=inputs["agency_profile_ref"],
        association_resolved=False,
    )
    plans.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="inventory-existing-context",
        status=RunPlanStepStatus.SUCCESS,
        result_json=inventory_result,
    )

    resolve = plans.claim_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="resolve-supplied-facts",
        claimed_by="agency-association-fixture",
    ).data
    _assert_claim_has_previous_handoff(resolve, expected_output="project_context_inventory")
    assert "existing_agency_profiles" in resolve.context_refs_json
    plans.record_step(
        run_plan_id=plan.id,
        run_id=started.run_id,
        step_id="resolve-supplied-facts",
        status=RunPlanStepStatus.BLOCKED,
        error="agency_profile_ref is not resolvable in the current StackOS project",
    )
    return plan


def _step_grants(plan: Any, step_id: str) -> list[dict[str, Any]]:
    return [
        grant
        for grant in plan.grant_snapshot_json["mcp_tool_grants"]
        if grant["step_id"] == step_id
    ]


def test_agency_workflows_validate_structurally_and_strictly_with_resolved_inputs(
    session: Session,
    project_id: int,
) -> None:
    loader = WorkflowTemplateLoader(session)
    plans = RunPlanRepository(session)
    inputs_by_workflow = {
        "agency.setup": _agency_inputs(),
        "agency.project-setup": _project_inputs(),
    }

    assert {item.key for item in loader.list_templates(plugin_slug="agency").templates} >= (
        AGENCY_WORKFLOW_KEYS
    )
    for workflow_key, inputs in inputs_by_workflow.items():
        structural = plans.validate_plan(
            project_id=project_id,
            template_key=workflow_key,
            plugin_slug="agency",
            enforce_required_inputs=False,
        )
        strict = plans.validate_plan(
            project_id=project_id,
            template_key=workflow_key,
            plugin_slug="agency",
            inputs_json=inputs,
            enforce_required_inputs=True,
        )

        assert structural.valid, (workflow_key, structural.errors)
        assert strict.valid, (workflow_key, strict.errors)
        assert strict.plan is not None
        grants = strict.plan.grant_snapshot_json["mcp_tool_grants"]
        assert strict.plan.grant_snapshot_json["artifact_grant_policy"] == "explicit"
        assert all(grant.get("tool") != "action.execute" for grant in grants)
        assert all("artifact" not in str(grant) for grant in grants)


@pytest.mark.parametrize(
    ("workflow_key", "inputs", "persist_step", "resource_key"),
    [
        ("agency.setup", _agency_inputs(requested=True), "persist-profile", "agency-profile"),
        (
            "agency.project-setup",
            _project_inputs(requested=True),
            "persist-project-context",
            "project-context",
        ),
    ],
)
def test_agency_setup_run_uses_only_declared_resource_grant_and_reads_back(
    session: Session,
    project_id: int,
    workflow_key: str,
    inputs: dict[str, Any],
    persist_step: str,
    resource_key: str,
) -> None:
    if workflow_key == "agency.project-setup":
        _create_agency_profile(
            session,
            project_id=project_id,
            profile_ref=inputs["agency_profile_ref"],
        )
    plan, written = _run_setup(
        session,
        project_id=project_id,
        workflow_key=workflow_key,
        inputs=inputs,
    )

    assert {
        "step_id": persist_step,
        "tool": "resource.upsert",
        "resource_key": resource_key,
    } in _step_grants(plan, persist_step)
    assert all(
        "artifact" not in str(grant) for grant in plan.grant_snapshot_json["mcp_tool_grants"]
    )
    assert all(
        grant.get("tool") != "action.execute"
        for grant in plan.grant_snapshot_json["mcp_tool_grants"]
    )

    readback = ResourceRepository(session).query_records(
        project_id=project_id,
        plugin_slug="agency",
        resource_key=resource_key,
    )
    assert [(record.id, record.external_id, record.data_json) for record in readback.items] == [
        (written.id, written.external_id, written.data_json)
    ]


def test_agency_profile_rerun_preserves_omitted_optional_fields_and_stable_identity(
    session: Session,
    project_id: int,
) -> None:
    first_inputs = _agency_inputs()
    _, first = _run_setup(
        session,
        project_id=project_id,
        workflow_key="agency.setup",
        inputs=first_inputs,
    )
    rerun_inputs = {
        "agency_profile_ref": first_inputs["agency_profile_ref"],
        "agency_name": first_inputs["agency_name"],
        "brief_context": "Updated context after a reviewed rerun.",
    }
    _, updated = _run_setup(
        session,
        project_id=project_id,
        workflow_key="agency.setup",
        inputs=rerun_inputs,
    )

    assert updated.id == first.id
    assert updated.data_json["brief_context"] == rerun_inputs["brief_context"]
    assert updated.data_json["agency_workspace_ref"] == first_inputs["agency_workspace_ref"]
    assert updated.data_json["guidance"] == first_inputs["guidance"]


def test_project_context_rerun_preserves_omitted_optional_fields_and_stable_identity(
    session: Session,
    project_id: int,
) -> None:
    first_inputs = _project_inputs()
    _create_agency_profile(
        session,
        project_id=project_id,
        profile_ref=first_inputs["agency_profile_ref"],
    )
    _, first = _run_setup(
        session,
        project_id=project_id,
        workflow_key="agency.project-setup",
        inputs=first_inputs,
    )
    rerun_inputs = {
        "engagement_ref": first_inputs["engagement_ref"],
        "engagement_name": first_inputs["engagement_name"],
        "client_ref": first_inputs["client_ref"],
        "description": "Updated context after a reviewed rerun.",
    }
    _, updated = _run_setup(
        session,
        project_id=project_id,
        workflow_key="agency.project-setup",
        inputs=rerun_inputs,
    )

    assert updated.id == first.id
    assert updated.data_json["description"] == rerun_inputs["description"]
    assert updated.data_json["agency_profile_ref"] == first_inputs["agency_profile_ref"]
    assert updated.data_json["guidance"] == first_inputs["guidance"]
    assert (
        updated.data_json["finance_workspace_association_ref"]
        == first_inputs["finance_workspace_association_ref"]
    )


@pytest.mark.parametrize(
    ("workflow_key", "inputs", "summary_key"),
    [
        (
            "agency.setup",
            {**_agency_inputs(), "finance_setup_requested": True},
            "agency_setup_summary",
        ),
        (
            "agency.project-setup",
            {**_project_inputs(), "finance_setup_requested": True},
            "project_setup_summary",
        ),
    ],
)
def test_finance_setup_without_exact_selection_stops_at_selection_required_handoff(
    session: Session,
    project_id: int,
    workflow_key: str,
    inputs: dict[str, Any],
    summary_key: str,
) -> None:
    if workflow_key == "agency.project-setup":
        _create_agency_profile(
            session,
            project_id=project_id,
            profile_ref=inputs["agency_profile_ref"],
        )
    plan, _ = _run_setup(
        session,
        project_id=project_id,
        workflow_key=workflow_key,
        inputs=inputs,
    )

    final_step = RunPlanRepository(session).get_step(
        plan.id,
        "readback-and-handoff",
        project_id=project_id,
    )
    handoff = final_step.result_json[summary_key]["finance_setup_handoff"]
    assert handoff == {
        "requested": True,
        "state": "selection-required",
        "workflow_keys": [],
        "authoring_intent": "none",
        "next_action": "Select exact finance workflow keys before requesting setup.",
    }
    assert all(
        grant.get("tool") != "action.execute"
        for grant in plan.grant_snapshot_json["mcp_tool_grants"]
    )


@pytest.mark.parametrize("association_location", ["missing", "other-project"])
def test_unresolved_agency_association_blocks_before_creating_project_context(
    session: Session,
    project_id: int,
    association_location: str,
) -> None:
    profile_ref = f"agency:{association_location}"
    inputs = _project_inputs(
        engagement_ref=f"engagement:{association_location}",
        agency_profile_ref=profile_ref,
    )
    if association_location == "other-project":
        other_project = (
            ProjectRepository(session)
            .create(
                slug="agency-association-other-project",
                name="Agency Association Other Project",
                domain="agency-association-other.example",
                locale="en-US",
            )
            .data
        )
        _create_agency_profile(
            session,
            project_id=other_project.id,
            profile_ref=profile_ref,
        )

    plan = _run_unresolved_association_setup(
        session,
        project_id=project_id,
        inputs=inputs,
    )

    plans = RunPlanRepository(session)
    blocked = plans.get_step(
        plan.id,
        "resolve-supplied-facts",
        project_id=project_id,
    )
    persist = plans.get_step(
        plan.id,
        "persist-project-context",
        project_id=project_id,
    )
    assert blocked.status == RunPlanStepStatus.BLOCKED
    assert blocked.error == "agency_profile_ref is not resolvable in the current StackOS project"
    assert persist.status == RunPlanStepStatus.PENDING
    assert (
        ResourceRepository(session)
        .query_records(
            project_id=project_id,
            plugin_slug="agency",
            resource_key="project-context",
        )
        .items
        == []
    )


def test_project_context_supports_same_client_multiple_engagements_and_standalone_use(
    session: Session,
    project_id: int,
) -> None:
    _create_agency_profile(session, project_id=project_id, profile_ref="agency:fixture")
    _, first = _run_setup(
        session,
        project_id=project_id,
        workflow_key="agency.project-setup",
        inputs=_project_inputs(engagement_ref="engagement:one"),
    )
    _, second = _run_setup(
        session,
        project_id=project_id,
        workflow_key="agency.project-setup",
        inputs=_project_inputs(engagement_ref="engagement:two"),
    )
    standalone_inputs = _project_inputs(
        engagement_ref="engagement:standalone",
        agency_profile_ref=None,
    )
    _, standalone = _run_setup(
        session,
        project_id=project_id,
        workflow_key="agency.project-setup",
        inputs=standalone_inputs,
    )

    records = (
        ResourceRepository(session)
        .query_records(
            project_id=project_id,
            plugin_slug="agency",
            resource_key="project-context",
        )
        .items
    )
    assert {record.id for record in records} == {first.id, second.id, standalone.id}
    assert {record.data_json["client_ref"] for record in records} == {"client:shared-fixture"}
    standalone_record = next(record for record in records if record.id == standalone.id)
    assert "agency_profile_ref" not in standalone_record.data_json
    assert "finance_workspace_association_ref" in standalone_record.data_json


def test_project_context_isolation_does_not_create_cross_project_access(
    session: Session,
    project_id: int,
) -> None:
    inputs = _project_inputs(engagement_ref="engagement:isolated")
    _create_agency_profile(
        session,
        project_id=project_id,
        profile_ref=inputs["agency_profile_ref"],
    )
    _, first = _run_setup(
        session,
        project_id=project_id,
        workflow_key="agency.project-setup",
        inputs=inputs,
    )
    second_project = (
        ProjectRepository(session)
        .create(
            slug="agency-isolation-fixture",
            name="Agency Isolation Fixture",
            domain="agency-isolation.example",
            locale="en-US",
        )
        .data
    )
    _create_agency_profile(
        session,
        project_id=second_project.id,
        profile_ref=inputs["agency_profile_ref"],
    )
    _, second = _run_setup(
        session,
        project_id=second_project.id,
        workflow_key="agency.project-setup",
        inputs=inputs,
    )

    records = ResourceRepository(session)
    first_page = records.query_records(
        project_id=project_id,
        plugin_slug="agency",
        resource_key="project-context",
    )
    second_page = records.query_records(
        project_id=second_project.id,
        plugin_slug="agency",
        resource_key="project-context",
    )
    assert [record.id for record in first_page.items] == [first.id]
    assert [record.id for record in second_page.items] == [second.id]
    assert first.id != second.id
