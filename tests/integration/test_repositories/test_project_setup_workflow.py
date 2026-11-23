"""Domain acceptance uses actual run/task receipts; native output is synthetic.

The runner/console path is covered by the dependent managed-setup integration.
These checks exercise protected admission, transaction composition and completed
proof identity without launching a model or changing any installed project.
"""

# ruff: noqa: F811

import copy
import json

import pytest
from sqlalchemy import event
from sqlmodel import Session, select

from stackos.db.models import (
    AgentRequest,
    RunPlan,
    RunPlanStatus,
    RunPlanStep,
    RunPlanStepStatus,
    TrackerTask,
    TrackerTicket,
)
from stackos.project_setup_workflow import (
    accept_result,
    create_admission,
    load_admission,
    read_admission,
)
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.project_setup import (
    ProjectSetupRepository,
    SetupActivationEvidence,
    SetupReport,
    _digest,
)
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.tracker import TrackerRepository
from stackos.task_delivery import _save_evidence, resolve_delivery_settings
from tests.helpers.native_task_routes import native_route_defaults  # noqa: F401
from tests.integration.test_repositories.test_project_setup import verified_result
from tests.integration.test_repositories.test_project_setup_attempts import attempt  # noqa: F401

KEY = "engineering.tracked-delivery"


def tracked_setup(attempt, session):
    project, _, _ = attempt
    plans = RunPlanRepository(session)
    plan = plans.create(
        project_id=project,
        run_plan_json={
            "schema_version": "stackos.run-plan.v1",
            "key": "setup-fixture",
            "title": "Setup",
            "steps": [
                {"id": "prepare", "title": "Prepare"},
                {"id": "activate-verify", "title": "Verify", "depends_on": ["prepare"]},
            ],
        },
        _commit=False,
    ).data
    task = session.exec(select(TrackerTask).where(TrackerTask.key == f"workflow-{plan.id}")).one()
    opened = create_admission(
        session,
        project_id=project,
        run_plan_id=plan.id,
        task_id=task.id,
        mode="setup",
        workflow_keys=[KEY],
        preferences={
            "host_key": "claude-code",
            "model": "fixture-model",
            "instructions": "not retained",
        },
    )
    plan = plans.start(plan.id, project_id=project).data.plan
    task.metadata_json = {"delivery": {"status": "running", "handoff_id": "fixture-handoff"}}
    session.add(task)
    session.commit()
    return plan, task, opened


def stage_native(session, project, plan, task, step_id, outputs, *, agent=None, status="success"):
    repo = ProjectSetupRepository(session)
    plans = RunPlanRepository(session)
    step = plans.claim_step(
        run_plan_id=plan.id, run_id=plan.run_id, step_id=step_id, claimed_by="fixture"
    ).data
    ticket = session.exec(
        select(TrackerTicket).where(TrackerTicket.run_plan_step_id == step.id)
    ).one()
    role = "review_agent" if step_id == "activate-verify" else "main_agent"
    agent = agent or {
        "name": "bootstrap-main",
        "host_key": "claude-code",
        "instructions": "Prepare",
    }
    native = dict(
        status=status,
        summary="Synthetic completed output",
        outputs=json.dumps(outputs),
        followups=[],
    )
    receipt = dict(
        agent=agent["name"],
        selected_role=role,
        thread_id=f"fixture-{step_id}",
        launch={
            "agent": agent["name"],
            "project_dir": ProjectRepository(session).get(project).project_dir,
            "sandbox": repo._launch_sandbox(agent, role),
            **{
                key: agent[key]
                for key in (
                    "host_key",
                    "source_ref",
                    "source_digest",
                    "model",
                    "model_reasoning_effort",
                    "provider",
                    "profile",
                    "native_settings",
                )
                if key in agent
            },
        },
        execution={
            "invocation_id": f"invocation-{step_id}",
            "ticket_key": ticket.key,
            "agent": agent["name"],
            "host_key": agent["host_key"],
        },
        finished_at="2026-11-23T00:00:00Z",
        exit_code=0,
        error=None,
        validated_outcome=status,
        result=native,
    )
    evidence = dict(
        attempt=1,
        delivery=receipt,
        sessions={"delivery": {"thread_id": receipt["thread_id"], "agent_snapshot": agent}},
    )
    _save_evidence(TrackerRepository(session), project, ticket.key, evidence, _commit=False)
    return ticket, evidence


def accept(session, project, plan, task, opened, ticket, *, revision=1):
    return accept_result(
        session,
        project_id=project,
        request_id=opened.request_id,
        run_plan_id=plan.id,
        task_id=task.id,
        ticket_id=ticket.id,
        attempt=1,
        phase="delivery",
        invocation_id=f"invocation-{'prepare' if revision == 1 else 'activate-verify'}",
        handoff_id="fixture-handoff",
        expected_revision=revision,
    )


def prepare_report(attempt, session, opened):
    project, _, repo = attempt
    result = verified_result(repo, project)
    result.attempt_ref = opened.attempt_ref
    result.verification.activation_proof = SetupActivationEvidence(
        method_ref="fixture/selected-method",
        status="pending",
        required_claims=["saved_defaults", "native_consumption", "reviewer_verification"],
        missing_claims=["saved_defaults", "native_consumption", "reviewer_verification"],
    )
    project_out = ProjectRepository(session).get(project)
    return SetupReport(
        status="partial",
        progress={"message": "Prepared", "evidence_ref": "fixture/prepared"},
        workflow_results=[{"workflow_key": KEY, "expected_revision": 0, "result": result}],
        adoption_proposal={
            "project_id": project,
            "binding_id": project_out.primary_workspace_binding_id,
            "project_dir": project_out.project_dir,
            "expected_delivery_digest": _digest(
                TrackerRepository(session).delivery_defaults(project_id=project)
            ),
            "roles": {
                "review_agent": {
                    "host_key": "claude-code",
                    "name": "fresh-reviewer",
                    "instructions": "new bounded marker",
                    "expected_digest": _digest("new bounded marker"),
                    "provenance_ref": "fixture/adaptation",
                    "execution_requirements": {"filesystem_write": True},
                }
            },
        },
        workflow_checks={
            KEY: [
                {
                    "role": "review_agent",
                    "claims": ["saved_defaults", "native_consumption", "reviewer_verification"],
                    "instructions": "Run the bounded fixture check",
                    "environment_ref": "fixture/isolated",
                    "evidence_ref": "fixture/check-output",
                }
            ]
        },
    )


def test_tracked_admission_is_protected_and_matches_exact_run(attempt, session):
    project, session_id, repo = attempt
    plan, task, opened = tracked_setup(attempt, session)
    assert opened.workflow_admission.preferences == {
        "host_key": "claude-code",
        "model": "fixture-model",
    }
    assert opened.managed is None
    assert (
        load_admission(
            session,
            project_id=project,
            request_id=opened.request_id,
            run_plan_id=plan.id,
            task_id=task.id,
        )[1]
        == opened
    )
    other = (
        RunPlanRepository(session)
        .create(
            project_id=project,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "foreign",
                "title": "Foreign",
                "steps": [{"id": "prepare", "title": "Prepare"}],
            },
        )
        .data
    )
    with pytest.raises(ConflictError, match="workflow binding"):
        load_admission(
            session,
            project_id=project,
            request_id=opened.request_id,
            run_plan_id=other.id,
            task_id=task.id,
        )
    with pytest.raises(ValidationError, match="projectSetup"):
        AgentRequestRepository(session).create(
            project_id=project, request_key="forged", title="Forged", source_kind="project_setup"
        )
    with pytest.raises(ConflictError, match="owning workflow"):
        repo.report(
            project,
            request_id=opened.request_id,
            session_id=session_id,
            claim_token="",
            expected_revision=1,
            report=SetupReport(
                status="partial",
                progress={"message": "No write", "evidence_ref": "fixture/no-write"},
            ),
        )
    assert repo._current_attempt(project).revision == 1

    other_task = session.exec(
        select(TrackerTask).where(TrackerTask.key == f"workflow-{other.id}")
    ).one()
    admission_args = dict(
        project_id=project,
        run_plan_id=other.id,
        task_id=other_task.id,
        mode="update",
        workflow_keys=[KEY],
        preferences={"host_key": "claude-code"},
    )
    with pytest.raises(ConflictError, match="still owns execution"):
        create_admission(session, **admission_args)
    RunPlanRepository(session).abort(
        run_plan_id=plan.id, project_id=project, reason="Fixture stopped"
    )
    newer = create_admission(session, **admission_args)
    session.commit()
    assert newer.request_id != opened.request_id
    assert (
        read_admission(
            session,
            project_id=project,
            request_id=opened.request_id,
            run_plan_id=plan.id,
            task_id=task.id,
        )[1].request_id
        == opened.request_id
    )
    with pytest.raises(ConflictError, match="current project owner"):
        load_admission(
            session,
            project_id=project,
            request_id=opened.request_id,
            run_plan_id=plan.id,
            task_id=task.id,
        )


def test_current_admission_batches_history_and_refreshes_linked_owners(attempt, session):
    project, _, repo = attempt
    history = []
    for _ in range(4):
        plan, task, opened = tracked_setup(attempt, session)
        history.append((plan, task, opened))
        RunPlanRepository(session).abort(
            run_plan_id=plan.id, project_id=project, reason="Fixture history"
        )
    current_plan, _, current = tracked_setup(attempt, session)
    old_plan, old_task, old_attempt = history[0]
    # Hold loaded identities so a batch query cannot accidentally rely on
    # the identity map instead of refreshing another writer's saved changes.
    retained = (
        session.get(RunPlan, old_plan.id),
        session.get(TrackerTask, old_task.id),
        session.get(AgentRequest, old_attempt.request_id),
    )
    statements = []

    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert repo._current_attempt(project).request_id == current.request_id
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(statements) <= 3
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)

    with Session(engine) as writer:
        plan = writer.get(RunPlan, old_plan.id)
        plan.status = RunPlanStatus.STARTED
        writer.add(plan)
        writer.commit()
    with pytest.raises(ConflictError, match="multiple setup writers"):
        repo._current_attempt(project)

    with Session(engine) as writer:
        plan = writer.get(RunPlan, old_plan.id)
        plan.status = RunPlanStatus.ABORTED
        task = writer.get(TrackerTask, old_task.id)
        task.metadata_json = {"delivery": {"slot_reserved": True}}
        writer.add(plan)
        writer.add(task)
        writer.commit()
    with pytest.raises(ConflictError, match="multiple setup writers"):
        repo._current_attempt(project)

    with Session(engine) as writer:
        task = writer.get(TrackerTask, old_task.id)
        task.metadata_json = {"delivery": {"slot_reserved": False}}
        request = writer.get(AgentRequest, old_attempt.request_id)
        metadata = copy.deepcopy(request.metadata_json)
        metadata["project_setup"]["workflow_admission"]["run_plan_id"] = current_plan.id
        request.metadata_json = metadata
        writer.add(task)
        writer.add(request)
        writer.commit()
    with pytest.raises(ConflictError, match="workflow binding"):
        repo._current_attempt(project)
    assert retained  # Keep identities alive throughout the cross-session checks.


def test_tracked_acceptance_and_step_roll_back_together_then_replay_once(attempt, session):
    project, _, repo = attempt
    plan, task, opened = tracked_setup(attempt, session)
    before = TrackerRepository(session).delivery_defaults(project_id=project)
    report = prepare_report(attempt, session, opened)
    ticket, evidence = stage_native(
        session, project, plan, task, "prepare", {"setup_report": report.model_dump(mode="json")}
    )
    accepted = accept(session, project, plan, task, opened, ticket)
    assert accepted.adopted_delivery_digest != _digest(before)
    RunPlanRepository(session).record_step(
        run_plan_id=plan.id,
        run_id=plan.run_id,
        step_id="prepare",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"prepared": True},
        _commit=False,
    )
    session.rollback()  # The caller's later acceptance failure rolls all staged effects back.
    assert TrackerRepository(session).delivery_defaults(project_id=project) == before
    assert repo._current_attempt(project).revision == 1
    assert session.get(RunPlanStep, ticket.run_plan_step_id).status == RunPlanStepStatus.RUNNING
    assert not session.get(TrackerTicket, ticket.id).completion_evidence_json
    assert repo._row(project, KEY) is None
    _save_evidence(TrackerRepository(session), project, ticket.key, evidence, _commit=False)
    accepted = accept(session, project, plan, task, opened, ticket)
    RunPlanRepository(session).record_step(
        run_plan_id=plan.id,
        run_id=plan.run_id,
        step_id="prepare",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"prepared": True},
        _commit=False,
    )
    session.commit()
    assert accept(session, project, plan, task, opened, ticket) == accepted
    assert len(repo._current_attempt(project).applied_workflow_results) == 1


def test_missing_required_role_selection_rolls_back_adoption(attempt, session):
    project, _, repo = attempt
    plan, task, opened = tracked_setup(attempt, session)
    before = TrackerRepository(session).delivery_defaults(project_id=project)
    report = prepare_report(attempt, session, opened)
    report.workflow_checks = {}
    ticket, _ = stage_native(
        session, project, plan, task, "prepare", {"setup_report": report.model_dump(mode="json")}
    )
    with pytest.raises(ValidationError, match="missing specialist claims"):
        accept(session, project, plan, task, opened, ticket)
    session.rollback()
    assert TrackerRepository(session).delivery_defaults(project_id=project) == before
    assert repo._current_attempt(project).revision == 1
    assert repo._row(project, KEY) is None


def test_blocked_native_retains_question_without_adoption(attempt, session):
    project, _, repo = attempt
    plan, task, opened = tracked_setup(attempt, session)
    before = TrackerRepository(session).delivery_defaults(project_id=project)
    report = SetupReport(
        status="needs_input",
        progress={"message": "Goal needed", "evidence_ref": "fixture/question"},
        question={
            "question_id": "goal",
            "revision": 1,
            "prompt": "Which local goal?",
            "workflow_keys": [KEY],
            "response_channel": "managed_ui",
        },
    )
    ticket, _ = stage_native(
        session,
        project,
        plan,
        task,
        "prepare",
        {"setup_report": report.model_dump(mode="json")},
        status="blocked",
    )
    current = accept(session, project, plan, task, opened, ticket)
    RunPlanRepository(session).record_step(
        run_plan_id=plan.id,
        run_id=plan.run_id,
        step_id="prepare",
        status=RunPlanStepStatus.BLOCKED,
        result_json={"question": "goal"},
        _commit=False,
    )
    session.commit()
    assert current.question == report.question
    assert current.status == "needs_input" and current.adopted_delivery_digest is None
    assert TrackerRepository(session).delivery_defaults(project_id=project) == before
    assert repo._row(project, KEY) is None


@pytest.mark.parametrize(
    "mismatch", [None, "wrong-role", "missing-observation", "changed-adaptation"]
)
def test_server_assembles_exact_completed_proof_without_changing_native_output(
    attempt, session, mismatch
):

    project, _, repo = attempt
    plan, task, opened = tracked_setup(attempt, session)
    report = prepare_report(attempt, session, opened)
    ticket, _ = stage_native(
        session, project, plan, task, "prepare", {"setup_report": report.model_dump(mode="json")}
    )
    current = accept(session, project, plan, task, opened, ticket)
    RunPlanRepository(session).record_step(
        run_plan_id=plan.id,
        run_id=plan.run_id,
        step_id="prepare",
        status=RunPlanStepStatus.SUCCESS,
        result_json={"prepared": True},
    )
    final = report.model_dump(mode="json")
    final.update(status="success", adoption_proposal=None, workflow_checks={})
    final["workflow_results"][0]["expected_revision"] = 1
    proof = final["workflow_results"][0]["result"]["verification"]
    proof["activation_proof"]["status"] = "verified"
    proof["activation"] = "verified_loaded"
    if mismatch == "changed-adaptation":
        proof["local_adaptations"][0]["digest"] = "0" * 64
    agent = resolve_delivery_settings(
        session,
        project_id=project,
        project_dir=ProjectRepository(session).get(project).project_dir,
        role_keys={"review_agent"},
    )["agents"]["review_agent"]
    outputs = {
        "setup_report": final,
        "setup_activation_checks": [
            {
                "claims": ["saved_defaults", "native_consumption", "reviewer_verification"],
                "status": "passed",
                "environment_ref": "fixture/isolated",
                "evidence_ref": "fixture/check-output",
            }
        ],
    }
    if mismatch == "missing-observation":
        outputs["setup_activation_checks"] = []
    ticket, evidence = stage_native(
        session, project, plan, task, "activate-verify", outputs, agent=agent
    )
    if mismatch == "wrong-role":
        evidence["delivery"]["selected_role"] = "main_agent"
        _save_evidence(TrackerRepository(session), project, ticket.key, evidence, _commit=False)
    native_before = copy.deepcopy(evidence["delivery"]["result"])
    if mismatch:
        with pytest.raises((ValidationError, ConflictError, ValueError)):
            accept(session, project, plan, task, current, ticket, revision=2)
        session.rollback()
        assert repo._current_attempt(project).status == "partial"
        return
    completed = accept(session, project, plan, task, current, ticket, revision=2)
    assert completed.status == "success"
    saved = repo._row(project, KEY).successful_json["verification"]["activation_proof"]["checks"]
    assert saved[0]["reference"]["outcome_digest"] == _digest(native_before)
    assert saved[0]["execution"]["role"] == "review_agent"
    assert saved[0]["reference"]["ticket_id"] == ticket.id
    assert (
        session.get(TrackerTicket, ticket.id).completion_evidence_json["automatic_delivery"][
            "delivery"
        ]["result"]
        == native_before
    )
