"""Scope and activation integrity at the real setup receipt owner.

Native outcomes below are deterministic repository fixtures, not activation proof
for this checkout or any installed host.
"""

# Imported pytest fixtures are intentionally shadowed by fixture arguments.
# ruff: noqa: F811

import copy
import json

import pytest
from pydantic import ValidationError as ModelValidationError

from stackos.db.models import AgentRequest, AgentRequestAttentionStatus, AgentRequestStatus
from stackos.jobs.workflow_schedules import MAIN_AGENT_RESULT_SCHEMA
from stackos.operations.project_setup_state import ProjectSetupStartInput
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.project_setup import (
    SetupActivationEvidence,
    SetupAttempt,
    SetupReport,
    _digest,
)
from tests.helpers.native_task_routes import native_route_defaults  # noqa: F401
from tests.integration.test_repositories.test_project_setup import verified_result
from tests.integration.test_repositories.test_project_setup_attempts import attempt  # noqa: F401

KEY = "engineering.tracked-delivery"


def open_setup(attempt, **kwargs):
    project, session_id, repo = attempt
    return repo.start(project, session_id=session_id, mode="setup", workflow_keys=[KEY], **kwargs)


def report_result(attempt, opened, *, status="success", result=None, revision=1):
    project, session_id, repo = attempt
    result = result or verified_result(repo, project)
    result.attempt_ref = opened.attempt.attempt_ref
    return repo.report(
        project,
        session_id=session_id,
        request_id=opened.attempt.request_id,
        claim_token=opened.claim_token,
        expected_revision=revision,
        report=SetupReport(
            status=status,
            progress={"message": "Checked setup", "evidence_ref": "fixture/check"},
            workflow_results=[{"workflow_key": KEY, "expected_revision": 0, "result": result}],
        ),
    )


def reviewer_check(attempt, session, result, *, agent=None):
    """Synthetic native envelope, never a claim of real host execution."""
    from stackos.db.models import TrackerTicket
    from stackos.repositories.projects import ProjectRepository
    from stackos.repositories.tracker import TrackerRepository
    from stackos.task_delivery import resolve_delivery_settings

    project, _, repo = attempt
    tracker = TrackerRepository(session)
    defaults = tracker.delivery_defaults(project_id=project)
    if agent is None and not defaults.get("agents", {}).get("review_agent"):
        tracker.configure_delivery(
            project_id=project,
            delivery={
                **defaults,
                "host_key": "claude-code",
                "agents": {
                    "review_agent": {
                        "name": "fixture-reviewer",
                        "instructions": "Run a bounded check",
                        "execution_requirements": {"filesystem_write": True},
                    }
                },
            },
        )
    root = ProjectRepository(session).get(project).project_dir
    agent = (
        agent
        or resolve_delivery_settings(
            session, project_id=project, project_dir=root, role_keys={"review_agent"}
        )["agents"]["review_agent"]
    )
    binding = repo.execution_binding(project, "review_agent", agent)
    observation = dict(
        claims=["reviewer_verification"],
        status="passed",
        environment_ref="fixture/native",
        evidence_ref="fixture/reviewer-check",
    )
    raw = dict(
        status="success",
        summary="Synthetic reviewer check",
        followups=[],
        outputs=json.dumps({"setup_activation_checks": [observation]}),
    )
    phase = dict(
        agent=agent["name"],
        selected_role="review_agent",
        thread_id="fixture-review-session",
        launch={
            "agent": agent["name"],
            "project_dir": root,
            "sandbox": repo._launch_sandbox(agent, "review_agent"),
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
            "invocation_id": "fixture-review-invocation",
            "ticket_key": "setup-proof-check",
            "agent": agent["name"],
            "host_key": agent["host_key"],
        },
        finished_at="2026-11-14T00:00:00Z",
        exit_code=0,
        error=None,
        validated_outcome="success",
        result=raw,
    )
    tracker.create_task(project_id=project, key="setup-proof", title="Disposable proof")
    ticket = tracker.create_ticket(
        project_id=project, task_key="setup-proof", key="setup-proof-check", title="Check"
    )
    row = session.get(TrackerTicket, ticket.data.ticket.id)
    row.completion_evidence_json = {
        "automatic_delivery": {
            "attempt": 1,
            "review": phase,
            "sessions": {"review": {"thread_id": phase["thread_id"], "agent_snapshot": agent}},
        }
    }
    session.add(row)
    session.commit()
    return dict(
        claims=observation["claims"],
        origin="reused",
        status="passed",
        basis=repo.activation_basis(project, result.verification, environment_ref="fixture/native"),
        reference=dict(
            kind="task_phase",
            task_id=row.task_id,
            ticket_id=row.id,
            attempt=1,
            phase="review",
            invocation_id="fixture-review-invocation",
            outcome_digest=_digest(raw),
        ),
        evidence_ref=observation["evidence_ref"],
        execution=binding,
    )


def activation_result(attempt, session, *, workflow_key=KEY, reviewer=True):
    project, _, repo = attempt
    result = verified_result(repo, project, workflow_key)
    role_check = (
        reviewer_check(attempt, session, result) if reviewer and workflow_key == KEY else None
    )
    basis = repo.activation_basis(project, result.verification, environment_ref="fixture/native")
    native_result = {"status": "success", "summary": "Checked native setup", "run_plan_id": None}
    native = AgentRequest(
        project_id=project,
        request_key="fixture-completed-native",
        title="Existing bounded native check",
        status=AgentRequestStatus.RESOLVED,
        attention_status=AgentRequestAttentionStatus.READ,
        metadata_json={
            "native_execution": {
                "snapshot": {"output_schema": copy.deepcopy(MAIN_AGENT_RESULT_SCHEMA)},
                "execution": {"invocation_id": "fixture-invocation", "thread_id": "fixture-native"},
                "finished_at": "2026-11-14T00:00:00Z",
                "exit_code": 0,
                "result": native_result,
            }
        },
    )
    session.add(native)
    session.commit()
    result.verification.activation_proof = SetupActivationEvidence(
        method_ref="fixture/selected-workflow-method",
        status="verified",
        required_claims=["saved_defaults", "native_consumption", "behavior"]
        + (["reviewer_verification"] if role_check else []),
        checks=[
            {
                "claims": ["saved_defaults", "native_consumption", "behavior"],
                "origin": "reused",
                "basis": basis,
                "reference": {
                    "kind": "native_request",
                    "request_id": native.id,
                    "invocation_id": "fixture-invocation",
                    "outcome_digest": _digest(native_result),
                },
                "evidence_ref": "fixture/observed-check",
            }
        ]
        + ([role_check] if role_check else []),
    )
    return result, native


def test_new_call_default_and_absent_stored_scope_have_distinct_meaning(attempt):
    assert ProjectSetupStartInput(project_id=attempt[0]).requested_scope == "ready_to_use"
    opened = open_setup(attempt)
    assert opened.attempt.requested_scope == "ready_to_use"
    legacy = opened.attempt.model_dump(mode="json")
    legacy.pop("requested_scope")
    assert SetupAttempt.model_validate(legacy).requested_scope == "infrastructure_only"


def test_engineering_generic_native_success_cannot_prove_reviewer(attempt, session):
    from stackos.repositories.tracker import TrackerRepository

    TrackerRepository(session).configure_delivery(
        project_id=attempt[0],
        delivery={
            "host_key": "claude-code",
            "agents": {
                "review_agent": {
                    "name": "reviewer",
                    "instructions": "Run a bounded check",
                    "execution_requirements": {"filesystem_write": True},
                }
            },
        },
    )
    opened = open_setup(attempt)
    result, _ = activation_result(attempt, session, reviewer=False)
    with pytest.raises(ValidationError, match="role claim"):
        report_result(attempt, opened, result=result)


@pytest.mark.parametrize("requirements", [None, {"filesystem_write": False}])
def test_inspection_only_method_does_not_require_write_capability(attempt, session, requirements):
    from stackos.repositories.tracker import TrackerRepository

    choice = {"name": "inspection", "instructions": "Inspect only"}
    if requirements is not None:
        choice["execution_requirements"] = requirements
    TrackerRepository(session).configure_delivery(
        project_id=attempt[0],
        delivery={
            "host_key": "claude-code",
            "agents": {"review_agent": choice},
        },
    )
    opened = open_setup(attempt)
    result, _ = activation_result(attempt, session, reviewer=False)
    assert report_result(attempt, opened, result=result).status == "success"


@pytest.mark.parametrize(
    "failure",
    [
        "binding",
        "role",
        "settings",
        "environment",
        "generic",
        "failed",
        "cancelled",
        "simulated",
        "invocation",
        "foreign",
        "task",
        "source",
        "missing-snapshot",
        "coordinator",
    ],
)
def test_reviewer_claim_requires_actual_current_native_check(attempt, session, tmp_path, failure):
    from stackos.db.models import TrackerTicket

    opened = open_setup(attempt)
    result, _ = activation_result(attempt, session)
    check = result.verification.activation_proof.checks[1]
    row = session.get(TrackerTicket, check.reference.ticket_id)
    metadata = copy.deepcopy(row.completion_evidence_json)
    phase = metadata["automatic_delivery"]["review"]
    if failure == "binding":
        check.execution = None
    elif failure == "role":
        check.execution.role = "delivery_agent"
    elif failure == "settings":
        phase["launch"]["model"] = "another-model"
    elif failure == "environment":
        check.basis.environment_ref = "different-environment"
    elif failure == "generic":
        phase["result"]["outputs"] = "{}"
        check.reference.outcome_digest = _digest(phase["result"])
    elif failure == "failed":
        phase["exit_code"] = 1
    elif failure == "cancelled":
        phase["cancelled"] = True
    elif failure == "simulated":
        check.origin = "simulated"
    elif failure == "invocation":
        check.reference.invocation_id = "another-invocation"
    elif failure == "foreign":
        from stackos.repositories.workspaces import WorkspaceRepository

        folder = tmp_path / "foreign-proof"
        folder.mkdir()
        row.project_id = WorkspaceRepository(session).bootstrap(cwd=str(folder)).project_id
    elif failure == "task":
        check.reference.task_id += 999
    elif failure == "source":
        check.execution.source_digest = "f" * 64
    elif failure == "missing-snapshot":
        metadata["automatic_delivery"]["sessions"]["review"].pop("agent_snapshot")
    elif failure == "coordinator":
        check.reference = result.verification.activation_proof.checks[0].reference
    row.completion_evidence_json = metadata
    session.add(row)
    session.commit()
    with pytest.raises((ConflictError, ValidationError, ModelValidationError), match="activation"):
        report_result(attempt, opened, result=result)
    assert attempt[2].get(attempt[0]).configured_count == 0


def test_failed_reviewer_observation_stays_visible_without_readiness_credit(attempt, session):
    from stackos.db.models import TrackerTicket

    opened = open_setup(attempt)
    result, _ = activation_result(attempt, session)
    activation = result.verification.activation_proof
    check = activation.checks[1]
    row = session.get(TrackerTicket, check.reference.ticket_id)
    evidence = copy.deepcopy(row.completion_evidence_json)
    receipt = evidence["automatic_delivery"]["review"]
    receipt["exit_code"] = 1
    receipt["validated_outcome"] = "failed"
    receipt["result"]["status"] = "failed"
    outputs = json.loads(receipt["result"]["outputs"])
    outputs["setup_activation_checks"][0]["status"] = "failed"
    receipt["result"]["outputs"] = json.dumps(outputs)
    check.reference.outcome_digest = _digest(receipt["result"])
    check.status = "failed"
    activation.status = "blocked"
    activation.diagnostic = "Reviewer check exited 1; preserve evidence and repair its owned setup"
    row.completion_evidence_json = evidence
    session.add(row)
    session.commit()
    saved = report_result(attempt, opened, status="running", result=result)
    assert saved.outcomes[KEY].missing_activation_claims == ["reviewer_verification"]
    assert saved.outcomes[KEY].activation_status == "blocked"
    assert activation.checks[0].status == "passed"  # unaffected generic coverage is retained


def test_completed_reviewer_in_approved_secondary_tree_is_reusable(attempt, session, tmp_path):
    from stackos.db.models import TrackerTicket
    from stackos.repositories.workspaces import WorkspaceRepository

    project, _, repo = attempt
    opened = open_setup(attempt)
    result, _ = activation_result(attempt, session)
    check = result.verification.activation_proof.checks[1]
    tree = tmp_path / "task-tree"
    tree.mkdir()
    binding = (
        WorkspaceRepository(session)
        .connect(
            project_id=project, last_known_root=str(tree), repo_fingerprint="fixture:task-tree"
        )
        .data
    )
    row = session.get(TrackerTicket, check.reference.ticket_id)
    evidence = copy.deepcopy(row.completion_evidence_json)
    evidence["automatic_delivery"]["review"]["launch"]["project_dir"] = str(tree)
    row.completion_evidence_json = evidence
    session.add(row)
    session.commit()
    check.execution.binding_id = binding.id
    assert (
        report_result(attempt, opened, status="running", result=result)
        .outcomes[KEY]
        .activation_status
        == "verified"
    )
    # Merely naming a different binding cannot relabel the actual launched tree.
    check.execution.binding_id = check.basis.binding_id
    with pytest.raises(ConflictError, match="source binding"):
        repo._validate_activation(project, result.verification)


@pytest.mark.parametrize("historical", [False, True])
def test_reviewer_task_phase_reuses_completed_exact_source_snapshot(attempt, session, historical):
    from pathlib import Path

    from stackos.db.models import TrackerTicket
    from stackos.repositories.projects import ProjectRepository
    from stackos.repositories.tracker import TrackerRepository
    from tests.integration.test_repositories.test_task_delivery_sources import source

    project, _, repo = attempt
    root = Path(ProjectRepository(session).get(project).project_dir)
    ref = source(session, project, root, "reviewer", settings='sandbox_mode="workspace-write"\n')
    TrackerRepository(session).configure_delivery(
        project_id=project,
        delivery={
            "host_key": "codex",
            "agents": {
                "review_agent": {
                    "source_ref": ref,
                    "execution_requirements": {"filesystem_write": True},
                }
            },
        },
    )
    opened = open_setup(attempt)
    result, _ = activation_result(attempt, session)
    check = result.verification.activation_proof.checks[1]
    row = session.get(TrackerTicket, check.reference.ticket_id)
    if historical:
        evidence = copy.deepcopy(row.completion_evidence_json)
        automatic = evidence["automatic_delivery"]
        automatic["attempts"] = [{"attempt": 1, "review": automatic.pop("review")}]
        automatic["attempt"] = 2
        row.completion_evidence_json = evidence
        session.add(row)
        session.commit()
    assert (
        report_result(attempt, opened, status="running", result=result)
        .outcomes[KEY]
        .activation_status
        == "verified"
    )
    # Changing an unrelated default invalidates saved readback, not the executed
    # reviewer identity. Refresh the basis and reuse that exact native check.
    tracker = TrackerRepository(session)
    tracker.configure_delivery(
        project_id=project,
        delivery={
            **tracker.delivery_defaults(project_id=project),
            "main_agent": {"host_key": "codex"},
        },
    )
    for item in result.verification.activation_proof.checks:
        item.basis = repo.activation_basis(
            project, result.verification, environment_ref=item.basis.environment_ref
        )
    repo._validate_activation(project, result.verification)
    path = root / ref["source_path"]
    path.write_text(path.read_text() + "\n# newer guidance\n")
    # Even rebasing the receipt cannot relabel a frozen old source as current.
    for item in result.verification.activation_proof.checks:
        item.basis = repo.activation_basis(
            project, result.verification, environment_ref=item.basis.environment_ref
        )
    with pytest.raises(ConflictError, match="native execution"):
        repo._validate_activation(project, result.verification)


def test_ready_to_use_cannot_finish_with_preparation_only(attempt):
    opened = open_setup(attempt)
    report_result(attempt, opened, status="running")
    project, session_id, repo = attempt
    state = repo.get(project)
    assert state.workflows[0].requested_scope == "ready_to_use"
    assert state.workflows[0].activation_status == "pending"
    assert state.current_attempt.outcomes[KEY].status == "success"
    with pytest.raises(ValidationError, match="activation") as error:
        repo.report(
            project,
            session_id=session_id,
            request_id=opened.attempt.request_id,
            claim_token=opened.claim_token,
            expected_revision=2,
            report=SetupReport(
                status="success",
                progress={"message": "Prepared", "evidence_ref": "fixture/prepared"},
            ),
        )
    assert error.value.data["next_action"] == "complete_activation"
    assert repo.get(project).current_attempt.revision == 2
    assert repo.get(project).workflows[0].successful is not None


def test_explicit_infrastructure_scope_retains_preparation_success(attempt):
    opened = open_setup(attempt, requested_scope="infrastructure_only")
    result = verified_result(attempt[2], attempt[0])
    result.verification.activation = "unknown"
    saved = report_result(attempt, opened, result=result)
    assert saved.status == "success"
    assert saved.requested_scope == "infrastructure_only"
    assert attempt[2].get(attempt[0]).workflows[0].status == "activation_pending"


def test_current_reused_native_evidence_can_complete_without_new_workflow(attempt, session):
    from sqlmodel import select

    from stackos.db.models import RunPlan, TrackerTask

    opened = open_setup(attempt)
    result, _ = activation_result(attempt, session)
    saved = report_result(attempt, opened, result=result)
    assert saved.status == "success"
    assert saved.outcomes[KEY].activation_status == "verified"
    workflow = attempt[2].get(attempt[0]).workflows[0]
    assert workflow.status == "current" and workflow.activation_status == "verified"
    assert (
        workflow.successful["verification"]["activation_proof"]["checks"][0]["origin"] == "reused"
    )
    assert not session.exec(select(RunPlan)).all()
    assert len(session.exec(select(TrackerTask)).all()) == 1  # existing completed proof owner


@pytest.mark.parametrize(
    "failure", ["foreign", "outcome", "invocation", "failed", "cancelled", "live", "basis"]
)
def test_activation_reference_rejects_forged_stale_or_incomplete_outcome(attempt, session, failure):
    opened = open_setup(attempt)
    result, native = activation_result(attempt, session)
    check = result.verification.activation_proof.checks[0]
    if failure == "foreign":
        check.basis.project_id += 1000
    elif failure == "outcome":
        check.reference.outcome_digest = "f" * 64
    elif failure == "invocation":
        check.reference.invocation_id = "different-invocation"
    elif failure == "basis":
        check.basis.delivery_digest = "b" * 64
    else:
        metadata = copy.deepcopy(native.metadata_json)
        metadata["native_execution"].update(
            {
                "failed": {"exit_code": 1},
                "cancelled": {"cancelled": True},
                "live": {"finished_at": None},
            }[failure]
        )
        native.metadata_json = metadata
        session.add(native)
        session.commit()
    with pytest.raises((ConflictError, ValidationError), match="activation"):
        report_result(attempt, opened, result=result)
    assert attempt[2].get(attempt[0]).configured_count == 0
    assert attempt[2].get(attempt[0]).current_attempt.revision == 1


def test_simulated_coverage_cannot_be_verified(attempt, session):
    result, _ = activation_result(attempt, session)
    evidence = result.verification.activation_proof.model_dump(mode="json")
    evidence["checks"][0]["origin"] = "simulated"
    with pytest.raises(ModelValidationError, match="required claims"):
        SetupActivationEvidence.model_validate(evidence)


def test_blocked_workflow_preserves_other_completed_activation(attempt, session):
    project, session_id, repo = attempt
    opened = repo.start(
        project, session_id=session_id, mode="update", workflow_keys=[KEY, "agency.project-setup"]
    )
    result, _ = activation_result(attempt, session)
    report_result(attempt, opened, result=result, status="running")
    before = repo.get(project, workflow_key=KEY).workflows[0].successful
    saved = repo.report(
        project,
        session_id=session_id,
        request_id=opened.attempt.request_id,
        claim_token=opened.claim_token,
        expected_revision=2,
        report=SetupReport(
            status="partial",
            progress={
                "message": "No safe verification method for the other workflow",
                "evidence_ref": "fixture/blocked",
            },
            recovery_condition=(
                "Provide an authorized technical check; do not execute a business action."
            ),
            workflow_results=[
                {
                    "workflow_key": "agency.project-setup",
                    "expected_revision": 0,
                    "result": {
                        "attempt_ref": opened.attempt.attempt_ref,
                        "result_ref": "fixture/unfinished",
                        "status": "unfinished",
                        "diagnostics": ["Safe activation capability unavailable"],
                    },
                }
            ],
        ),
    )
    assert saved.status == "partial" and saved.outcomes[KEY].activation_status == "verified"
    assert repo.get(project, workflow_key=KEY).workflows[0].successful == before
    assert saved.outcomes["agency.project-setup"].activation_status == "pending"


def test_final_success_rechecks_retained_outcome_and_saved_defaults(attempt, session):
    from stackos.repositories.tracker import TrackerRepository

    opened = open_setup(attempt)
    result, _ = activation_result(attempt, session)
    report_result(attempt, opened, result=result, status="running")
    project, session_id, repo = attempt
    TrackerRepository(session).configure_delivery(
        project_id=project, delivery={"host_key": "claude"}
    )
    assert repo.get(project).workflows[0].activation_status == "pending"
    with pytest.raises(ConflictError, match="activation"):
        repo.report(
            project,
            session_id=session_id,
            request_id=opened.attempt.request_id,
            claim_token=opened.claim_token,
            expected_revision=2,
            report=SetupReport(
                status="success", progress={"message": "Done", "evidence_ref": "fixture/end"}
            ),
        )
    assert repo.get(project).current_attempt.revision == 2


@pytest.mark.parametrize("failure", [None, "phase", "outcome", "aborted", "foreign"])
def test_completed_workflow_step_reference_uses_real_owner(attempt, session, tmp_path, failure):
    from stackos.db.models import RunPlan, RunPlanStatus, RunPlanStep, RunPlanStepStatus
    from stackos.repositories.project_setup import SetupProofReference
    from stackos.repositories.workspaces import WorkspaceRepository

    opened = open_setup(attempt)
    result, _ = activation_result(attempt, session)
    project = attempt[0]
    if failure == "foreign":
        folder = tmp_path / "foreign"
        folder.mkdir()
        project = WorkspaceRepository(session).bootstrap(cwd=str(folder)).project_id
    plan = RunPlan(
        project_id=project,
        key="existing-proof",
        title="Existing completed work",
        status=RunPlanStatus.COMPLETED,
    )
    session.add(plan)
    session.flush()
    step = RunPlanStep(
        run_plan_id=plan.id,
        step_id="existing-check",
        title="Check",
        position=0,
        status=RunPlanStepStatus.SUCCESS,
        result_json={"behavior": "passed"},
    )
    if failure == "aborted":
        plan.status = RunPlanStatus.ABORTED
    session.add(step)
    session.add(plan)
    session.commit()
    result.verification.activation_proof.checks[0].reference = SetupProofReference(
        kind="workflow_step",
        run_plan_id=plan.id,
        step_id="forged-phase" if failure == "phase" else step.step_id,
        outcome_digest="f" * 64 if failure == "outcome" else _digest(step.result_json),
    )
    if failure:
        with pytest.raises(ConflictError, match="activation"):
            report_result(attempt, opened, result=result)
        assert attempt[2].get(attempt[0]).configured_count == 0
    else:
        assert report_result(attempt, opened, result=result).status == "success"


def test_adoption_proposal_is_scoped_evidence_without_write_authority(attempt, session):
    from stackos.repositories.project_setup import SetupAdoptionProposal
    from stackos.repositories.projects import ProjectRepository
    from stackos.repositories.tracker import TrackerRepository

    project, session_id, repo = attempt
    opened = open_setup(attempt)
    current = ProjectRepository(session).get(project)
    proposal = SetupAdoptionProposal(
        project_id=project,
        binding_id=current.primary_workspace_binding_id,
        project_dir=current.project_dir,
        expected_delivery_digest=_digest({}),
        roles={
            "delivery_agent": {
                "host_key": "claude",
                "name": "fixture-delivery",
                "instructions": "fixture instructions",
                "expected_digest": _digest("fixture instructions"),
                "provenance_ref": "fixture/native-contract",
            }
        },
    )
    args = dict(
        project_id=project,
        session_id=session_id,
        request_id=opened.attempt.request_id,
        claim_token=opened.claim_token,
        expected_revision=1,
    )
    report = SetupReport(
        status="running",
        progress={"message": "Proposed adoption", "evidence_ref": "fixture/proposal"},
        adoption_proposal=proposal,
    )
    saved = repo.report(**args, report=report)
    assert saved.adoption_proposal == proposal
    assert saved.reports[0].adoption_proposal == proposal
    assert TrackerRepository(session).delivery_defaults(project_id=project) == {}
    forged = report.model_dump(mode="json")
    forged["adoption_proposal"]["trusted_local_admin"] = True
    with pytest.raises(ModelValidationError, match="Extra inputs"):
        SetupReport.model_validate(forged)
    for field, value in (("project_id", project + 1000), ("expected_delivery_digest", "f" * 64)):
        stale = report.model_copy(deep=True)
        setattr(stale.adoption_proposal, field, value)
        with pytest.raises(ConflictError, match="proposal"):
            repo.report(**{**args, "expected_revision": 2}, report=stale)
    assert repo.get(project).current_attempt.revision == 2


def test_failed_native_observation_is_retained_without_covering_activation(attempt, session):
    opened = open_setup(attempt)
    result, native = activation_result(attempt, session)
    evidence = result.verification.activation_proof
    evidence.status = "blocked"
    evidence.diagnostic = "Native check failed; repair before retry."
    evidence.checks[0].status = "failed"
    native.metadata_json = {
        "native_execution": {**native.metadata_json["native_execution"], "exit_code": 1}
    }
    session.add(native)
    session.commit()
    result.status = "unfinished"
    saved = report_result(attempt, opened, status="partial", result=result)
    assert saved.outcomes[KEY].activation_status == "blocked"
    assert saved.outcomes[KEY].missing_activation_claims == [
        "behavior",
        "native_consumption",
        "saved_defaults",
    ]
    workflow = attempt[2].get(attempt[0]).workflows[0]
    assert workflow.successful is None
    assert workflow.diagnostic == "Native check failed; repair before retry."
    assert (
        workflow.latest_result["verification"]["activation_proof"]["checks"][0]["status"]
        == "failed"
    )


@pytest.mark.parametrize(
    "outcome", ["failed", "blocked", "missing", "malformed", "unresolved", "schema"]
)
def test_process_success_cannot_replace_valid_native_outcome(attempt, session, outcome):
    opened = open_setup(attempt)
    result, native = activation_result(attempt, session)
    metadata = copy.deepcopy(native.metadata_json)
    execution = metadata["native_execution"]
    if outcome in {"failed", "blocked"}:
        execution["result"]["status"] = outcome
    elif outcome == "missing":
        execution["result"] = None
    elif outcome == "malformed":
        execution["result"] = {"status": "success"}
    elif outcome == "unresolved":
        native.status = AgentRequestStatus.CLAIMED
    else:
        execution["snapshot"].pop("output_schema")
    # Even a resolved request cannot bless a contradictory or invalid result.
    native.metadata_json = metadata
    session.add(native)
    session.commit()
    result.verification.activation_proof.checks[0].reference.outcome_digest = _digest(
        execution["result"]
    )
    with pytest.raises(ConflictError, match="native outcome"):
        report_result(attempt, opened, result=result)
    state = attempt[2].get(attempt[0])
    assert state.current_attempt.revision == 1 and state.configured_count == 0


@pytest.mark.parametrize("outcome", ["failed", "blocked", "missing"])
def test_logical_failure_or_missing_result_is_retained_without_activation_credit(
    attempt, session, outcome
):
    opened = open_setup(attempt)
    result, native = activation_result(attempt, session)
    metadata = copy.deepcopy(native.metadata_json)
    raw = metadata["native_execution"]["result"]
    if outcome == "missing":
        raw = None
    else:
        raw["status"] = outcome
    metadata["native_execution"]["result"] = raw
    native.metadata_json = metadata
    native.status = (
        AgentRequestStatus.CLAIMED if outcome == "blocked" else AgentRequestStatus.FAILED
    )
    session.add(native)
    session.commit()
    result.status = "unfinished"
    evidence = result.verification.activation_proof
    evidence.status = "blocked"
    evidence.diagnostic = f"Native outcome is {outcome}; complete the missing check."
    evidence.checks[0].status = "failed"
    evidence.checks[0].reference.outcome_digest = _digest(raw)
    saved = report_result(attempt, opened, status="partial", result=result)
    assert saved.outcomes[KEY].activation_status == "blocked"
    assert set(saved.outcomes[KEY].missing_activation_claims) == {
        "saved_defaults",
        "native_consumption",
        "behavior",
    }
    workflow = attempt[2].get(attempt[0]).workflows[0]
    assert workflow.successful is None and workflow.diagnostic == evidence.diagnostic
    assert workflow.latest_result["verification"]["activation_proof"]["checks"][0]["reference"][
        "outcome_digest"
    ] == _digest(raw)


@pytest.mark.parametrize("changed", ["owner", "schema"])
def test_retained_native_eligibility_is_rechecked_on_read_and_final_success(
    attempt, session, changed
):
    opened = open_setup(attempt)
    result, native = activation_result(attempt, session)
    report_result(attempt, opened, result=result, status="running")
    if changed == "owner":
        native.status = AgentRequestStatus.CLAIMED
    else:
        metadata = copy.deepcopy(native.metadata_json)
        metadata["native_execution"]["snapshot"].pop("output_schema")
        native.metadata_json = metadata
    session.add(native)
    session.commit()
    project, session_id, repo = attempt
    workflow = repo.get(project).workflows[0]
    assert workflow.activation_status == "pending" and workflow.status == "activation_pending"
    assert workflow.successful is not None
    with pytest.raises(ConflictError, match="native outcome"):
        repo.report(
            project,
            session_id=session_id,
            request_id=opened.attempt.request_id,
            claim_token=opened.claim_token,
            expected_revision=2,
            report=SetupReport(
                status="success", progress={"message": "Done", "evidence_ref": "fixture/end"}
            ),
        )
    assert repo.get(project).current_attempt.revision == 2


@pytest.mark.parametrize("outcome", ["success", "failed", "malformed"])
@pytest.mark.parametrize("historical_schema", [False, True])
def test_managed_native_reference_uses_wrapped_setup_report(
    attempt, session, outcome, historical_schema
):
    opened = open_setup(attempt)
    result, native = activation_result(attempt, session)
    report = SetupReport(
        status="failed" if outcome == "failed" else "success",
        progress={"message": "Native setup outcome", "evidence_ref": "fixture/native-report"},
    )
    raw = {"report_json": "invalid JSON" if outcome == "malformed" else report.model_dump_json()}
    # Retained native-request proof keeps its historical schema after the
    # executable setup request protocol is replaced by task delivery.
    schema = {
        "type": "object",
        "properties": {
            "report_json": {"type": "string"},
            "verification_status": {
                "type": ["string", "null"],
                "enum": ["passed", "failed", "blocked", None],
            },
        },
        "required": ["report_json", "verification_status"],
        "additionalProperties": False,
    }
    if historical_schema:
        # A completed historical result retains its original optional field
        # contract. Current transport requirements cannot invalidate that proof.
        schema["required"] = ["report_json"]
        schema["properties"]["verification_status"] = {
            "type": "string",
            "enum": ["passed", "failed", "blocked"],
        }
    else:
        raw["verification_status"] = None
    metadata = copy.deepcopy(native.metadata_json)
    metadata["native_execution"].update(snapshot={"output_schema": schema}, result=raw)
    metadata["project_setup"] = {
        **opened.attempt.model_dump(mode="json"),
        "request_id": native.id,
        "root_request_id": native.id,
        "attempt_ref": f"agent-request:{native.id}",
        "entry_kind": "managed_ui",
        "status": "success",
        "managed": {
            "settings": {"host_key": "codex"},
            "binding_id": result.verification.activation_proof.checks[0].basis.binding_id,
            "admission_id": "fixture/managed",
            "job_ref": "fixture/native-job",
        },
    }
    native.source_kind = "project_setup"
    native.metadata_json = metadata
    session.add(native)
    session.commit()
    result.verification.activation_proof.checks[0].reference.outcome_digest = _digest(raw)
    if outcome == "success":
        assert report_result(attempt, opened, result=result).status == "success"
    else:
        with pytest.raises(ConflictError, match="native outcome"):
            report_result(attempt, opened, result=result)


def test_absent_scope_in_persisted_attempt_and_receipt_is_historical(attempt, session):
    project, _, repo = attempt
    opened = open_setup(attempt, requested_scope="infrastructure_only")
    report_result(attempt, opened)
    row = session.get(AgentRequest, opened.attempt.request_id)
    metadata = copy.deepcopy(row.metadata_json)
    metadata["project_setup"].pop("requested_scope")
    row.metadata_json = metadata
    receipt = repo._row(project, KEY)
    receipt.successful_json = {
        k: v for k, v in receipt.successful_json.items() if k != "requested_scope"
    }
    receipt.latest_result_json = {
        k: v for k, v in receipt.latest_result_json.items() if k != "requested_scope"
    }
    session.add(row)
    session.add(receipt)
    session.commit()
    state = repo.get(project)
    assert state.current_attempt.requested_scope == "infrastructure_only"
    assert state.workflows[0].requested_scope == "infrastructure_only"
    assert state.current_attempt.status == "success" and state.workflows[0].status == "current"
    assert "requested_scope" not in session.get(AgentRequest, row.id).metadata_json["project_setup"]


def test_codex_proposal_checks_actual_source_and_digest(attempt, session):
    from pathlib import Path

    from stackos.host_mcp.project_agents import ProjectAgentService
    from stackos.repositories.project_setup import SetupAdoptionProposal
    from stackos.repositories.projects import ProjectRepository
    from stackos.repositories.tracker import TrackerRepository
    from tests.integration.test_repositories.test_task_delivery_sources import source

    project, session_id, repo = attempt
    current = ProjectRepository(session).get(project)
    root = Path(current.project_dir)
    ref = source(session, project, root, "fixture-delivery")
    definition = ProjectAgentService(root).get(ref)
    opened = open_setup(attempt)
    proposal = SetupAdoptionProposal(
        project_id=project,
        binding_id=current.primary_workspace_binding_id,
        project_dir=str(root),
        expected_delivery_digest=_digest({}),
        roles={
            "delivery_agent": {
                "source_ref": ref,
                "host_key": "codex",
                "name": "fixture-delivery",
                "expected_digest": definition.digest,
                "provenance_ref": "fixture/source",
            }
        },
    )
    args = dict(
        project_id=project,
        session_id=session_id,
        request_id=opened.attempt.request_id,
        claim_token=opened.claim_token,
        expected_revision=1,
    )
    report = SetupReport(
        status="running",
        progress={"message": "Codex proposal", "evidence_ref": "fixture/proposal"},
        adoption_proposal=proposal,
    )
    assert repo.report(**args, report=report).adoption_proposal == proposal
    assert TrackerRepository(session).delivery_defaults(project_id=project) == {}
    path = root / ref["source_path"]
    path.write_text(path.read_text() + "\n# changed\n")
    with pytest.raises(ConflictError, match="source changed"):
        repo.report(**{**args, "expected_revision": 2}, report=report)


@pytest.mark.parametrize("foreign_workflow", [False, True])
def test_unfinished_result_cannot_attach_coverage_to_wrong_sources(
    attempt, session, foreign_workflow
):
    opened = open_setup(attempt)
    result, _ = activation_result(attempt, session)
    if foreign_workflow:
        other = verified_result(attempt[2], attempt[0], "agency.project-setup")
        other.verification.activation_proof = result.verification.activation_proof
        result = other
    result.status = "unfinished"
    if not foreign_workflow:
        source = result.verification.sources[0]
        source.content["description"] = "Invented source basis"
        source.digest = _digest(source.content)
    result.verification.activation_proof.checks[0].basis.sources_digest = _digest(
        attempt[2]._identity(result.verification.sources)
    )
    with pytest.raises(ConflictError, match="sources changed"):
        report_result(attempt, opened, status="running", result=result)
    assert attempt[2].get(attempt[0]).configured_count == 0
