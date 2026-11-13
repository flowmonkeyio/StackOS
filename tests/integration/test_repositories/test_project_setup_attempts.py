"""Existing-session setup uses one request owner and checked workflow receipts."""

import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlmodel import Session, select

from stackos.db.connection import make_engine
from stackos.db.models import (
    AgentRequest,
    AgentRequestAttentionStatus,
    AgentRequestStatus,
    AgentSession,
    RunPlan,
    TrackerTask,
)
from stackos.operations.registry import build_operation_registry
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.project_setup import ProjectSetupRepository, SetupReport
from stackos.repositories.workspaces import WorkspaceRepository
from stackos.workflows.template_loader import WorkflowTemplateLoader
from tests.helpers.sqlite_schema import capture_database_snapshot, restore_database_snapshot
from tests.integration.test_repositories.test_project_setup import verified_result


@pytest.fixture
def attempt(session, tmp_path):
    folder = tmp_path / "setup-attempt"
    folder.mkdir()
    started = WorkspaceRepository(session).start_session(runtime="codex", cwd=str(folder))
    repo = ProjectSetupRepository(session)
    return started.project_id, started.data.id, repo


def start(attempt):
    project, session_id, repo = attempt
    return repo.start(
        project,
        session_id=session_id,
        mode="setup",
        workflow_keys=["engineering.tracked-delivery", "agency.project-setup"],
    )


def test_start_attaches_without_competing_writer_or_business_state(attempt, session):
    first = start(attempt)
    second = start(attempt)
    assert not first.attached and first.claim_token
    assert second.attached and second.claim_token is None
    assert second.attempt == first.attempt
    assert len(session.exec(select(AgentRequest)).all()) == 1
    assert not session.exec(select(RunPlan)).all()
    assert not session.exec(select(TrackerTask)).all()
    request = AgentRequestRepository(session).get(
        project_id=attempt[0], request_id=first.attempt.request_id
    )
    assert request.run_plan_id is None
    assert request.metadata_json["project_setup"]["entry_kind"] == "existing_agent"
    with pytest.raises(ConflictError, match="projectSetup"):
        AgentRequestRepository(session).claim(
            project_id=attempt[0], request_id=request.id, claimed_by="other"
        )


def test_report_checks_owner_revision_and_independent_results(attempt, session):
    project, session_id, repo = attempt
    opened = start(attempt)
    success = verified_result(repo, project)
    success.attempt_ref = opened.attempt.attempt_ref
    payload = dict(
        status="running",
        progress={"message": "Checked first workflow", "evidence_ref": "fixture/check"},
        workflow_results=[
            {
                "workflow_key": "engineering.tracked-delivery",
                "expected_revision": 0,
                "result": success.model_dump(mode="json"),
            }
        ],
    )
    report = SetupReport(**payload)
    for token in ("foreign", ""):
        with pytest.raises(ConflictError):
            repo.report(
                project,
                request_id=opened.attempt.request_id,
                session_id=session_id,
                claim_token=token,
                expected_revision=1,
                report=report,
            )
    assert repo.get(project).configured_count == 0
    saved = repo.report(
        project,
        request_id=opened.attempt.request_id,
        session_id=session_id,
        claim_token=opened.claim_token,
        expected_revision=1,
        report=report,
    )
    assert saved.revision == 2
    assert repo.get(project).configured_count == 1
    assert (
        repo.get(project).workflows[0].successful["verification"]["readiness"]["execution_ready"]
        is False
    )
    with pytest.raises(ConflictError, match="revision"):
        repo.report(
            project,
            request_id=saved.request_id,
            session_id=session_id,
            claim_token=opened.claim_token,
            expected_revision=1,
            report=report,
        )
    failed = SetupReport(
        status="partial",
        progress={"message": "Second needs repair", "evidence_ref": "fixture/failed"},
        workflow_results=[
            {
                "workflow_key": "agency.project-setup",
                "expected_revision": 0,
                "result": {
                    "attempt_ref": saved.attempt_ref,
                    "result_ref": "fixture/failure",
                    "status": "failed",
                    "changes": ["partial edit"],
                },
            }
        ],
    )
    repo.report(
        project,
        request_id=saved.request_id,
        session_id=session_id,
        claim_token=opened.claim_token,
        expected_revision=2,
        report=failed,
    )
    state = repo.get(project)
    assert state.configured_count == 1
    assert state.current_attempt.status == "partial"
    assert next(
        w for w in state.workflows if w.workflow_key == "agency.project-setup"
    ).partial_changes


def test_report_atomic_failure_and_same_session_question(attempt, session):
    project, session_id, repo = attempt
    opened = start(attempt)
    proof = verified_result(repo, project)
    proof.attempt_ref = opened.attempt.attempt_ref
    bad = SetupReport(
        status="running",
        progress={"message": "Two checks", "evidence_ref": "fixture/check"},
        workflow_results=[
            {
                "workflow_key": "engineering.tracked-delivery",
                "expected_revision": 0,
                "result": proof,
            },
            {
                "workflow_key": "agency.project-setup",
                "expected_revision": 4,
                "result": {
                    "attempt_ref": proof.attempt_ref,
                    "result_ref": "fixture/second",
                    "status": "failed",
                },
            },
        ],
    )
    with pytest.raises(ConflictError):
        repo.report(
            project,
            request_id=opened.attempt.request_id,
            session_id=session_id,
            claim_token=opened.claim_token,
            expected_revision=1,
            report=bad,
        )
    assert repo.get(project).configured_count == 0
    question = SetupReport(
        status="needs_input",
        progress={"message": "Asked in this session", "evidence_ref": "fixture/question"},
        question={
            "question_id": "goal",
            "revision": 1,
            "prompt": "Which project goal?",
            "workflow_keys": ["agency.project-setup"],
        },
    )
    saved = repo.report(
        project,
        request_id=opened.attempt.request_id,
        session_id=session_id,
        claim_token=opened.claim_token,
        expected_revision=1,
        report=question,
    )
    assert saved.question.answer_state == "pending"
    assert saved.question.response_channel == "existing_agent"
    assert "conversation_url" not in saved.model_dump_json()
    with pytest.raises(ConflictError):
        repo.report(
            project,
            request_id=saved.request_id,
            session_id=session_id + 1,
            claim_token=opened.claim_token,
            expected_revision=2,
            report=question,
        )
    assert repo.get(project).current_attempt.revision == 2


def test_reserved_metadata_and_terminal_success_cannot_be_fabricated(attempt, session):
    project, session_id, repo = attempt
    with pytest.raises(ValidationError, match="project_setup"):
        AgentRequestRepository(session).create(
            project_id=project,
            request_key="forged",
            title="forged",
            metadata_json={"project_setup": {}},
        )
    session.rollback()
    opened = start(attempt)
    before = copy.deepcopy(repo.get(project).current_attempt)
    with pytest.raises(ValidationError, match="workflow"):
        repo.report(
            project,
            request_id=opened.attempt.request_id,
            session_id=session_id,
            claim_token=opened.claim_token,
            expected_revision=1,
            report=SetupReport(
                status="success", progress={"message": "done", "evidence_ref": "fixture/no-proof"}
            ),
        )
    assert repo.get(project).current_attempt == before


def test_two_verified_workflows_complete_and_late_report_cannot_change_them(attempt, session):
    project, session_id, repo = attempt
    opened = start(attempt)
    results = []
    for key in opened.attempt.workflow_keys:
        result = verified_result(repo, project, key)
        result.attempt_ref = opened.attempt.attempt_ref
        result.result_ref = f"fixture/result/{key}"
        results.append({"workflow_key": key, "expected_revision": 0, "result": result})
    report = SetupReport(
        status="success",
        progress={"message": "Both checked", "evidence_ref": "fixture/both"},
        workflow_results=results,
    )
    saved = repo.report(
        project,
        request_id=opened.attempt.request_id,
        session_id=session_id,
        claim_token=opened.claim_token,
        expected_revision=1,
        report=report,
    )
    assert saved.status == "success"
    assert repo.get(project).configured_count == 2
    before = repo.get(project).model_dump()
    with pytest.raises(ConflictError):
        repo.report(
            project,
            request_id=saved.request_id,
            session_id=session_id,
            claim_token=opened.claim_token,
            expected_revision=2,
            report=report,
        )
    assert repo.get(project).model_dump() == before
    assert start(attempt).attempt.request_id != saved.request_id
    assert len(session.exec(select(AgentRequest)).all()) == 2


def test_current_update_and_unavailable_startup_preserve_binding_and_receipts(attempt, session):
    project, session_id, repo = attempt
    source = verified_result(repo, project)
    repo.record_result(project, "engineering.tracked-delivery", expected_revision=0, result=source)
    workspace = WorkspaceRepository(session)
    root = Path(session.get(AgentSession, session_id).cwd)
    current = workspace.start_session(runtime="codex", cwd=str(root)).data
    assert current.project_setup["status"] == "current"
    binding = current.workspace_binding_id
    WorkflowTemplateLoader(session).upsert_extension(
        project_id=project,
        workflow_key="engineering.tracked-delivery",
        input_defaults_json={"goal": "updated goal"},
    )
    updated = workspace.start_session(runtime="codex", cwd=str(root)).data
    assert updated.project_setup["update_count"] == 1
    moved = root.with_name("temporarily-moved")
    root.rename(moved)
    unavailable = workspace.start_session(runtime="codex", cwd=str(root), auto_bootstrap=False).data
    assert unavailable.workspace_binding_id == binding
    assert unavailable.project_setup["status"] == "folder_unavailable"
    assert unavailable.project_setup["configured_count"] == 1
    assert unavailable.project_setup["eligible"]
    assert not session.exec(select(AgentRequest)).all()


def test_unknown_legacy_attempt_is_inspected_and_never_reclaimed(attempt, session):
    project, _, repo = attempt
    row = AgentRequest(
        project_id=project,
        request_key="legacy-setup",
        title="Legacy setup",
        source_kind="project_setup",
        status=AgentRequestStatus.NEW,
        attention_status=AgentRequestAttentionStatus.UNREAD,
    )
    session.add(row)
    session.commit()
    assert "unknown legacy" in repo.get(project).attempt_diagnostic
    with pytest.raises(ConflictError, match="legacy"):
        start(attempt)
    assert len(session.exec(select(AgentRequest)).all()) == 1
    assert AgentRequestRepository(session).list_claimable(project_id=project).items == []
    with pytest.raises(ConflictError, match="projectSetup"):
        AgentRequestRepository(session).ignore(
            project_id=project, request_id=row.id, ignored_by="other"
        )


def test_concurrent_sessions_have_one_writer(attempt, session, tmp_path):
    project, session_id, _ = attempt
    engine = make_engine(tmp_path / "concurrent-setup.db")
    restore_database_snapshot(engine, capture_database_snapshot(session.get_bind()))
    barrier = Barrier(2)

    def enter(_):
        with Session(engine) as other:
            barrier.wait(timeout=10)
            return ProjectSetupRepository(other).start(
                project, session_id=session_id, mode="setup", workflow_keys=["agency.project-setup"]
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(enter, range(2)))
        assert len({result.attempt.request_id for result in results}) == 1
        assert sum(result.claim_token is not None for result in results) == 1
        assert sum(result.attached for result in results) == 1
    finally:
        engine.dispose()


def test_completion_rechecks_sources_changed_between_progress_reports(attempt, session):
    project, session_id, repo = attempt
    opened = repo.start(
        project, session_id=session_id, mode="setup", workflow_keys=["agency.project-setup"]
    )
    result = verified_result(repo, project, "agency.project-setup")
    result.attempt_ref = opened.attempt.attempt_ref
    repo.report(
        project,
        request_id=opened.attempt.request_id,
        session_id=session_id,
        claim_token=opened.claim_token,
        expected_revision=1,
        report=SetupReport(
            status="running",
            progress={"message": "Checked", "evidence_ref": "fixture/check"},
            workflow_results=[
                {"workflow_key": "agency.project-setup", "expected_revision": 0, "result": result}
            ],
        ),
    )
    WorkflowTemplateLoader(session).upsert_extension(
        project_id=project,
        workflow_key="agency.project-setup",
        input_defaults_json={"goal": "changed while setup ran"},
    )
    with pytest.raises(ConflictError, match="sources changed"):
        repo.report(
            project,
            request_id=opened.attempt.request_id,
            session_id=session_id,
            claim_token=opened.claim_token,
            expected_revision=2,
            report=SetupReport(
                status="success", progress={"message": "Done", "evidence_ref": "fixture/end"}
            ),
        )
    assert repo.get(project).current_attempt.revision == 2
    assert repo.get(project).current_attempt.status == "running"


def test_report_preserves_request_owned_execution_and_unrelated_metadata(attempt, session):
    project, session_id, repo = attempt
    opened = start(attempt)
    row = session.get(AgentRequest, opened.attempt.request_id)
    # Synthetic retention marker only; this does not claim a native invocation.
    native = {"fixture": "immutable invocation evidence"}
    row.metadata_json = {
        **row.metadata_json,
        "native_execution": native,
        "unrelated": {"retain": True},
    }
    session.add(row)
    session.commit()
    repo.report(
        project,
        request_id=row.id,
        session_id=session_id,
        claim_token=opened.claim_token,
        expected_revision=1,
        report=SetupReport(
            status="running", progress={"message": "Inspecting", "evidence_ref": "fixture/inspect"}
        ),
    )
    request = AgentRequestRepository(session).get(project_id=project, request_id=row.id)
    assert request.metadata_json["native_execution"] == native
    assert request.metadata_json["unrelated"] == {"retain": True}
    assert (
        request.metadata_json["project_setup"]["reports"][0]["progress"]["evidence_ref"]
        == "fixture/inspect"
    )


def test_attempt_operations_are_agent_only_and_keep_repair_response_modes():
    registry = build_operation_registry()
    for name in ("projectSetup.start", "projectSetup.report"):
        spec = registry.get(name)
        assert spec.surfaces.mcp.enabled
        assert not spec.surfaces.rest.enabled and not spec.surfaces.cli.enabled
        assert spec.response_policy.allowed_modes == ("compact", "raw")
        assert spec.response_policy.ack_safe is False


def test_folder_loss_after_verified_progress_blocks_success_but_keeps_recovery_reporting(
    attempt, session
):
    project, session_id, repo = attempt
    opened = repo.start(
        project, session_id=session_id, mode="setup", workflow_keys=["agency.project-setup"]
    )
    result = verified_result(repo, project, "agency.project-setup")
    result.attempt_ref = opened.attempt.attempt_ref
    repo.report(
        project,
        request_id=opened.attempt.request_id,
        session_id=session_id,
        claim_token=opened.claim_token,
        expected_revision=1,
        report=SetupReport(
            status="running",
            progress={"message": "Checked", "evidence_ref": "fixture/checked"},
            workflow_results=[
                {"workflow_key": "agency.project-setup", "expected_revision": 0, "result": result}
            ],
        ),
    )
    root = Path(session.get(AgentSession, session_id).cwd)
    root.rename(root.with_name("unavailable-during-completion"))
    with pytest.raises(ConflictError, match="available selected project folder"):
        repo.report(
            project,
            request_id=opened.attempt.request_id,
            session_id=session_id,
            claim_token=opened.claim_token,
            expected_revision=2,
            report=SetupReport(
                status="success", progress={"message": "Done", "evidence_ref": "fixture/done"}
            ),
        )
    assert repo.get(project).current_attempt.revision == 2
    recovered = repo.report(
        project,
        request_id=opened.attempt.request_id,
        session_id=session_id,
        claim_token=opened.claim_token,
        expected_revision=2,
        report=SetupReport(
            status="interrupted",
            progress={"message": "Folder disappeared", "evidence_ref": "fixture/folder-lost"},
            recovery_condition="Restore the selected folder and revalidate",
        ),
    )
    assert recovered.status == "interrupted"
    assert repo.get(project).configured_count == 1
