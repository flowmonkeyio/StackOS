"""Registered durable effects remain visible across controller boundaries."""

import pytest
from sqlmodel import select

from stackos import controlled_process_host as host
from stackos.actions import ActionRepository
from stackos.db.models import (
    ActionCall,
    ActionCallStatus,
    Credential,
    DurableActionItem,
    DurableActionJob,
    ProjectCredential,
    Run,
    WorkItemOwnership,
)
from stackos.repositories.base import ConflictError
from stackos.repositories.runs import RunRepository
from tests.integration.test_controlled_command_admission import case_factory as case_factory
from tests.integration.test_controlled_process_terminal import (
    certify_original,
    effect_arguments,
    second_controller,
)
from tests.integration.test_controlled_process_terminal import isolated as isolated
from tests.integration.test_controlled_report_certificate import report_original as report_original


def test_registered_second_controller_effect_blocks_original_certificate(report_original):
    case, original = report_original
    binding = original.original.binding
    repo = RunRepository(case.session)

    certified = certify_original(original)
    assert certified.quiescence_revision == certified.revision

    second = second_controller(case)
    assert not case.session.exec(select(Run).where(Run.parent_run_id == second.run)).all()
    audit = ActionRepository(case.session)
    clean_effect = audit._controlled_effect_snapshot(**effect_arguments(case, original))
    assert clean_effect.status == "known_no_effect" and not clean_effect.issues

    action_ref = "engineering.controlled-proof.v1"
    idempotency_key = "fixture:second-controller:durable-effect"
    owner_binding = audit._supervised_durable_binding(
        project_id=second.project,
        run_id=second.run,
        run_plan_id=second.plan,
        run_plan_step_id=second.step,
        action_ref=action_ref,
        idempotency_key=idempotency_key,
    )
    assert owner_binding is not None
    assert owner_binding["canonical_item_id"] == binding.canonical_item_id
    assert owner_binding["controller_run_id"] == second.run

    credential = Credential(
        credential_ref="fixture:registered-effect",
        provider_key="fixture",
        display_name="Synthetic registered effect",
        display_name_key="synthetic-registered-effect",
    )
    case.session.add(credential)
    case.session.flush()
    case.session.add(ProjectCredential(project_id=second.project, credential_id=credential.id))
    call = ActionCall(
        project_id=second.project,
        run_id=second.run,
        run_plan_id=second.plan,
        run_plan_step_id=second.step,
        action_key="controlled-proof.v1",
        plugin_slug="engineering",
        operation="action.execute",
        status=ActionCallStatus.RUNNING,
        idempotency_key=idempotency_key,
        credential_ref=credential.credential_ref,
        request_json={"fixture": "synthetic durable effect"},
    )
    case.session.add(call)
    case.session.flush()

    job_out = ActionRepository(case.session).create_durable_action_job(
        project_id=second.project,
        action_call_id=call.id,
        credential_ref=credential.credential_ref,
        action_ref=action_ref,
        items=[
            {
                "destination_ref": "fixture:destination",
                "correlation_ref": "fixture:second-controller",
                "input_json": {"fixture": True},
            }
        ],
        idempotency_key=idempotency_key,
        supervised_owner_binding=owner_binding,
        commit_guard=lambda: None,
    )
    job = case.session.get(DurableActionJob, job_out.id, populate_existing=True)
    assert job is not None and job.run_id == second.run
    assert job.action_call_id == call.id
    assert case.session.exec(
        select(DurableActionItem).where(DurableActionItem.job_id == job.id)
    ).one()

    stored = case.session.exec(
        select(WorkItemOwnership).where(
            WorkItemOwnership.project_id == second.project,
            WorkItemOwnership.canonical_item_id == binding.canonical_item_id,
        )
    ).one()
    assert stored.quiescence_revision is not None
    assert stored.quiescence_revision != stored.revision
    with pytest.raises(ConflictError):
        repo._assert_native_certificate_current(stored)
    registration = stored.continuity_json["durable_action_registrations"][str(job.id)]
    assert registration["binding"] == owner_binding
    assert registration["action_call_id"] == call.id
    snapshot = repo._native_quiescence_snapshot(
        project_id=second.project,
        canonical_item_id=binding.canonical_item_id,
        owner_ref=stored.owner_ref,
        expected_epoch=stored.epoch,
        expected_revision=stored.revision,
    )
    assert snapshot.effect.status == "unknown"
    assert "effect_durable_unknown" in snapshot.effect.issues
    assert any(
        row.get("kind") == "job" and row.get("row", {}).get("run_id") == second.run
        for row in snapshot.effect.rows
    )

    verification = host._verify_native_quiescence(
        case.session, snapshot=snapshot, owners=(original,)
    )
    assert type(verification) is host._TerminalHold
    current_owner = case.session.exec(
        select(WorkItemOwnership).where(
            WorkItemOwnership.project_id == second.project,
            WorkItemOwnership.canonical_item_id == binding.canonical_item_id,
        )
    ).one()
    assert current_owner.quiescence_revision != current_owner.revision
