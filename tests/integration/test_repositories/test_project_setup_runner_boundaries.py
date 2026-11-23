"""Tracked setup shares ownership and native-result safety with ordinary delivery."""

# ruff: noqa: F811
import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from stackos import task_delivery
from stackos.db.models import AgentRequest, RunPlan, TrackerTask
from stackos.host_mcp.result import AgentRunResult
from stackos.repositories.base import ConflictError
from stackos.repositories.project_setup import ProjectSetupRepository, SetupAttempt
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.run_plans import RunPlanRepository
from tests.helpers.native_task_routes import native_route_defaults  # noqa: F401
from tests.integration.test_repositories.test_managed_setup import (
    install_native,
    launch,
    managed,  # noqa: F401
    native_lifetime,  # noqa: F401
    task_receipts,
)


def test_only_validated_admissions_are_outside_legacy_native_capacity(managed, session):
    execution, project, scheduler, factory = managed
    first = launch(managed, host_key="codex")
    execution.cancel_setup(
        project,
        first.attempt.request_id,
        expected_revision=first.attempt.revision,
        external_work_stopped=False,
        session_factory=factory,
        scheduler=scheduler,
    )
    current = launch(managed, host_key="codex")
    assert current.run_plan_id != first.run_plan_id
    session.expire_all()
    assert ProjectSetupRepository(session).managed_admissions() == []
    capacity = task_delivery.delivery_capacity(session, scheduler, project_id=project)["projects"][
        0
    ]
    assert capacity["running_count"] == 1 and capacity["queued_count"] == 0
    assert execution.setup_directories(session) == []

    # A caller-controlled marker cannot hide a malformed or foreign owner.
    row = session.get(AgentRequest, first.attempt.request_id)
    damaged = deepcopy(row.metadata_json)
    damaged["project_setup"]["workflow_admission"]["task_id"] = (
        current.attempt.workflow_admission.task_id
    )
    row.metadata_json = damaged
    session.add(row)
    session.commit()
    with pytest.raises(ConflictError):
        ProjectSetupRepository(session).managed_admissions()
    with pytest.raises(ConflictError):
        task_delivery.delivery_capacity(session, scheduler, project_id=project)

    damaged = deepcopy(row.metadata_json)
    damaged["project_setup"]["workflow_admission"]["run_plan_id"] = None
    row.metadata_json = damaged
    session.add(row)
    session.commit()
    with pytest.raises(ConflictError):
        ProjectSetupRepository(session).managed_admissions()


def test_tracked_ownership_does_not_load_retained_history(managed, session, monkeypatch):
    execution, project, scheduler, factory = managed
    started = launch(managed, host_key="codex")
    execution.cancel_setup(
        project,
        started.attempt.request_id,
        expected_revision=started.attempt.revision,
        external_work_stopped=False,
        session_factory=factory,
        scheduler=scheduler,
    )
    row = session.get(AgentRequest, started.attempt.request_id)
    metadata = deepcopy(row.metadata_json)
    metadata["project_setup"]["answers"] = [
        {
            "question_id": f"retained-{index}",
            "question_revision": 1,
            "question_request_id": row.id,
            "answer": "UNNEEDED-RETAINED-ANSWER-" + "x" * 7900,
            "answered_at": datetime.now(UTC).isoformat(),
        }
        for index in range(20)
    ]
    SetupAttempt.model_validate(metadata["project_setup"])
    row.metadata_json = metadata
    session.add(row)
    session.commit()
    session.expunge_all()

    def no_attempt(*args, **kwargs):
        pytest.fail("ownership must not parse a full retained setup attempt")

    def no_hydration(_session, instance):
        pytest.fail(f"ownership must not hydrate {type(instance).__name__}")

    execute = session.execute
    selected_bytes = []

    def bounded_execute(*args, **kwargs):
        result = execute(*args, **kwargs).freeze()
        selected_bytes.append(len(json.dumps([list(row) for row in result().all()], default=str)))
        assert selected_bytes[-1] < 4096
        return result()

    monkeypatch.setattr(SetupAttempt, "model_validate", no_attempt)
    monkeypatch.setattr(SetupAttempt, "model_validate_json", no_attempt)
    monkeypatch.setattr(session, "execute", bounded_execute)
    event.listen(session, "loaded_as_persistent", no_hydration)
    try:
        assert ProjectSetupRepository(session).managed_admissions() == []
    finally:
        event.remove(session, "loaded_as_persistent", no_hydration)
    assert len(selected_bytes) == 1


def test_domain_rejection_cannot_persist_secret_native_outputs(managed, session, monkeypatch):
    marker = "SYNTHETIC-SETUP-SECRET-MARKER"

    def native(root, instructions, prompt, schema, *, on_session, **kwargs):
        on_session("secret-boundary")
        return AgentRunResult(
            0,
            thread_id="secret-boundary",
            result={
                "status": "success",
                "summary": "Invalid fixture report",
                "followups": [],
                "outputs": json.dumps({"setup_report": {"status": "invalid"}, "api_key": marker}),
            },
        )

    install_native(monkeypatch, native)
    started = launch(managed, host_key="codex")
    managed[2].run()
    session.expire_all()
    state = ProjectSetupRepository(session).get(managed[1])
    assert state.work.delivery_status == "stopped"
    task, receipts = task_receipts(session, managed[1], started.task_key)
    evidence = receipts[f"workflow-{started.run_plan_id}-prepare"]
    assert evidence["delivery"].get("validated_outcome") is None
    assert "result" not in evidence["delivery"]
    persisted = {
        "task": session.get(TrackerTask, task.id).metadata_json,
        "request": session.get(AgentRequest, started.attempt.request_id).metadata_json,
        "plan": session.get(RunPlan, started.run_plan_id).metadata_json,
        "receipts": receipts,
    }
    assert marker not in json.dumps(persisted)
    assert state.current_attempt.adopted_delivery_digest is None
    assert state.current_attempt.applied_workflow_results == {}


def test_busy_task_directory_rejects_setup_without_orphan_work(managed, session):
    from sqlmodel import select

    from stackos.repositories.base import ValidationError

    _, project, scheduler, factory = managed
    directory = ProjectRepository(session).get(project).project_dir
    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project,
        run_plan_json={
            "key": "existing-owner",
            "title": "Existing directory owner",
            "steps": [{"id": "work", "title": "Work"}],
        },
    ).data
    run = plans.start(plan.id, project_id=project).data
    plans.claim_step(project_id=project, run_plan_id=plan.id, run_id=run.run_id, step_id="work")
    task_delivery.start_delivery(
        session=session,
        session_factory=factory,
        scheduler=scheduler,
        project_id=project,
        task_key=f"workflow-{plan.id}",
        project_dir=directory,
        run_plan_id=plan.id,
        controller_run_id=run.run_id,
        step_id="work",
        host_key="claude-code",
        agents={"delivery_agent": {"name": "existing-worker", "instructions": "Fixture check."}},
    )
    with pytest.raises(ValidationError, match="already owned"):
        launch(managed, host_key="codex")
    session.expire_all()
    assert len(session.exec(select(RunPlan)).all()) == 1
    assert len(session.exec(select(TrackerTask)).all()) == 1
    assert session.exec(select(AgentRequest)).all() == []
    assert len(scheduler.jobs) == 1
    task_delivery.abort_run_plan(
        session, run_plan_id=plan.id, project_id=project, scheduler=scheduler
    )
    assert launch(managed, host_key="codex").run_plan_id != plan.id


def test_registration_failure_retains_work_and_retries_without_fake_session(
    managed, session, monkeypatch
):
    execution, project, scheduler, factory = managed
    register = scheduler.add_job

    def fail(*args, **kwargs):
        raise RuntimeError("Fixture scheduler unavailable")

    monkeypatch.setattr(scheduler, "add_job", fail)
    with pytest.raises(RuntimeError, match="scheduler unavailable"):
        launch(managed, host_key="codex")
    session.expire_all()
    state = ProjectSetupRepository(session).get(project)
    assert state.work.delivery_status == "stopped" and state.work.can_continue
    task, receipts = task_receipts(session, project, state.work.task_key)
    assert all(not receipt.get("sessions") for receipt in receipts.values())
    monkeypatch.setattr(scheduler, "add_job", register)
    continued = execution.continue_managed(
        project,
        state.current_attempt.request_id,
        expected_revision=state.current_attempt.revision,
        session_factory=factory,
        scheduler=scheduler,
    )
    assert continued.task_key == task.key and len(scheduler.jobs) == 1


def test_cancel_verification_preserves_accepted_preparation_and_adoption(
    managed, session, monkeypatch
):
    from tests.integration.test_repositories.test_managed_setup_activation import (
        KEY,
        install_runner,
        proposal_for,
    )

    execution, project, scheduler, factory = managed
    proposal = proposal_for(managed, session)
    prepared = {}

    def cancel_during_verification(calls):
        setup = calls[-1]["packet"]["project_setup"]
        with factory() as check:
            prepared.update(
                deepcopy(ProjectSetupRepository(check)._row(project, KEY).latest_result_json)
            )
        execution.cancel_setup(
            project,
            setup["request_id"],
            expected_revision=setup["expected_revision"],
            external_work_stopped=False,
            session_factory=factory,
            scheduler=scheduler,
        )
        return "success"  # A late successful return cannot undo the canonical abort.

    install_runner(managed, monkeypatch, proposal, verification_status=cancel_during_verification)
    started = launch(managed, host_key="codex")
    scheduler.run()
    session.expire_all()
    repo = ProjectSetupRepository(session)
    current = repo.get(project)
    assert current.current_attempt.status == "cancelled"
    assert current.work.run_status == "aborted"
    assert current.current_attempt.adopted_delivery_digest
    assert repo._row(project, KEY).latest_result_json == prepared
    _, receipts = task_receipts(session, project, started.task_key)
    assert receipts[f"{started.task_key}-prepare"]["delivery"]["validated_outcome"] == "success"
    assert receipts[f"{started.task_key}-activate-verify"]["review"]["validated_outcome"] is None
    assert current.workflows[0].missing_activation_claims
