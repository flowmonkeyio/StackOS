"""Project setup uses the canonical workflow/task runner; legacy ownership stays fenced.

Native adapters are deterministic fixtures, not installed-host activation evidence.
Shared queue/session/lifetime matrices remain in test_task_delivery.py.
"""

# Imported fixtures intentionally provide the normal native route and lifetime.

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from sqlmodel import Session, select

from stackos import task_delivery
from stackos.db.models import AgentRequest, RunPlan, TrackerTask, TrackerTicket
from stackos.host_mcp import service
from stackos.host_mcp.result import AgentRunResult, HostMcpResult
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.project_setup import ProjectSetupRepository
from stackos.repositories.tracker import TrackerRepository
from stackos.repositories.workspaces import WorkspaceRepository
from tests.helpers.native_task_routes import native_route_defaults  # noqa: F401
from tests.integration.test_repositories.test_request_execution import native_lifetime  # noqa: F401
from tests.integration.test_repositories.test_task_delivery import QueuedScheduler, generic_result


def capacity_counts(session, scheduler):
    rows = task_delivery.delivery_capacity(session, scheduler)["projects"]
    return {key: sum(row[key] for row in rows) for key in ("running_count", "queued_count")}


def _host_status(host, *, available=True, **_kwargs):
    result = service.normalize_result(
        HostMcpResult(
            host,
            "fixture",
            "registered_current" if available else "absent",
            "Connected" if available else "Install this host",
            True,
            available,
        ),
        service._definition_by_key(host).policy,
    )
    return replace(
        result,
        agent_execution={
            **result.agent_execution,
            "runnable": "available" if available else "unavailable",
        },
    )


@pytest.fixture
def managed(session, tmp_path, monkeypatch):
    from stackos import project_setup_execution as execution

    folder = tmp_path / "managed"
    folder.mkdir()
    opened = WorkspaceRepository(session).start_session(runtime="codex", cwd=str(folder))
    scheduler = QueuedScheduler()
    monkeypatch.setattr(service, "inspect_host", _host_status)
    return execution, opened.project_id, scheduler, lambda: Session(session.get_bind())


def launch(managed, **choices):
    execution, project, scheduler, factory = managed
    return execution.start_managed(
        project_id=project,
        session_factory=factory,
        scheduler=scheduler,
        mode="setup",
        workflow_keys=["engineering.tracked-delivery"],
        preferences=choices or None,
    )


def install_native(monkeypatch, runner):
    """Replace only the native adapter; service owns real invocation receipts."""
    original = service._definition_by_key

    def definition(key):
        found = original(key)
        return replace(found, run_agent=runner) if found and found.run_agent else found

    monkeypatch.setattr(service, "_definition_by_key", definition)


def task_receipts(session, project, task_key):
    session.expire_all()
    task = session.exec(
        select(TrackerTask).where(TrackerTask.project_id == project, TrackerTask.key == task_key)
    ).one()
    tickets = session.exec(select(TrackerTicket).where(TrackerTicket.task_id == task.id)).all()
    return task, {
        ticket.key: (ticket.completion_evidence_json or {}).get("automatic_delivery", {})
        for ticket in tickets
    }


def stored_setup(session, project, key, payload):
    """Persist legacy metadata without validating it through the current model."""
    from stackos.db.models import AgentRequestAttentionStatus, AgentRequestStatus

    row = AgentRequest(
        project_id=project,
        request_key=key,
        title=key,
        source_kind="project_setup",
        status=AgentRequestStatus.FAILED,
        attention_status=AgentRequestAttentionStatus.READ,
    )
    session.add(row)
    session.flush()
    row.metadata_json = {
        "project_setup": {**deepcopy(payload), "project_id": project, "request_id": row.id},
        "native_execution": {"legacy_report": "x" * 128_000},
    }
    session.add(row)
    session.commit()
    return row.id


@pytest.mark.parametrize("has_managed_field", [False, True])
def test_established_external_setup_stays_out_of_managed_admission(
    managed, session, has_managed_field
):
    execution, project, scheduler, _factory = managed
    payload = {"entry_kind": "existing_agent", "status": "running"}
    if has_managed_field:
        payload["managed"] = None
    stored_setup(session, project, "external-owner", payload)
    assert ProjectSetupRepository(session).managed_admissions() == []
    assert capacity_counts(session, scheduler)["running_count"] == 0
    assert capacity_counts(session, scheduler)["queued_count"] == 0
    assert execution.setup_directories(session) == []


def test_invalid_explicit_selection_rejects_before_request(managed, session):
    with pytest.raises(ValidationError, match="catalog"):
        launch(managed, host_key="codex", model="not-a-supported-model")
    assert not session.exec(select(AgentRequest)).all()


def test_saved_main_agent_preserves_specialists_and_inherits(managed, session):
    _, project, _, _ = managed
    repo = TrackerRepository(session)
    original = {
        "host_key": "codex",
        "agents": {"delivery_agent": {"name": "specialist", "model": "gpt-6-astra"}},
    }
    repo.configure_delivery(project_id=project, delivery=original)
    repo.configure_main_agent(
        project_id=project, preferences={"host_key": "codex", "model": "gpt-6.1-sol"}
    )
    assert repo.delivery_defaults(project_id=project) == {
        **original,
        "main_agent": {"host_key": "codex", "model": "gpt-6.1-sol"},
    }
    first = launch(managed)
    assert first.attempt.workflow_admission.preferences["model"] == "gpt-6.1-sol"


def test_omitted_model_keeps_native_default_and_recommendation_is_advisory(managed, session):
    _, project, _, _ = managed
    repo = TrackerRepository(session)
    repo.configure_delivery(
        project_id=project,
        delivery={
            "host_key": "codex",
            "main_agent": {"host_key": "codex", "model": "old-unavailable-model"},
        },
    )
    repo.configure_main_agent(project_id=project, preferences={"host_key": "codex", "model": None})
    opened = launch(managed)
    assert opened.attempt.workflow_admission.preferences["host_key"] == "codex"
    assert "model" not in opened.attempt.workflow_admission.preferences
    catalog = service._definition_by_key("codex").policy.agent_execution
    assert catalog.recommended_model in catalog.models


def test_launch_inheritance_clears_and_host_switch_use_existing_preference_owner(managed, session):
    execution, project, _, _ = managed
    repo = TrackerRepository(session)
    saved = {
        "host_key": "codex",
        "model": "gpt-6.1-sol",
        "model_reasoning_effort": "high",
        "provider": "configured",
    }
    repo.configure_main_agent(project_id=project, preferences=saved)
    assert execution.resolve_preferences(session, project) == saved
    assert execution.resolve_preferences(
        session, project, {"model": None, "model_reasoning_effort": None, "provider": None}
    ) == {"host_key": "codex"}
    assert execution.resolve_preferences(session, project, {"model": "gpt-6-luna"}) == {
        "host_key": "codex",
        "model": "gpt-6-luna",
        "provider": "configured",
    }
    assert execution.resolve_preferences(session, project, {"host_key": "claude-code"}) == {
        "host_key": "claude-code"
    }
    assert repo.delivery_defaults(project_id=project)["main_agent"] == saved
    repo.configure_main_agent(
        project_id=project,
        preferences={"host_key": "codex", "model": None, "model_reasoning_effort": None},
    )
    assert execution.resolve_preferences(session, project) == {"host_key": "codex"}


def test_invalid_reasoning_creates_no_managed_request(managed, session):
    with pytest.raises(ValidationError, match="reasoning effort"):
        launch(managed, host_key="codex", model="gpt-6.1-sol", model_reasoning_effort="none")
    assert not session.exec(select(AgentRequest)).all()
    assert not managed[2].jobs


@pytest.mark.parametrize("problem", ["host", "folder"])
def test_unavailable_host_or_folder_creates_no_request(managed, session, monkeypatch, problem):
    from pathlib import Path

    from stackos.repositories.projects import ProjectRepository

    _, project, _, _ = managed
    if problem == "host":
        monkeypatch.setattr(
            service,
            "inspect_host",
            lambda host, **kwargs: _host_status(host, available=False, **kwargs),
        )
    else:
        Path(ProjectRepository(session).get(project).project_dir).rmdir()
    with pytest.raises(ValidationError, match=r"host needs setup|bound folder"):
        launch(managed, host_key="codex")
    assert not session.exec(select(AgentRequest)).all()


def test_first_setup_owns_one_real_workflow_and_uses_generic_native_receipts(
    managed, session, monkeypatch
):
    execution, project, scheduler, _factory = managed
    calls = []

    def runner(root, instructions, prompt, schema, *, on_session, **kwargs):
        packet = json.loads(prompt)
        calls.append(packet)
        assert packet["project_setup"]["workflow_keys"] == ["engineering.tracked-delivery"]
        assert "claim_token" not in packet["project_setup"]
        assert not (root / ".codex" / "agents").exists()
        on_session("first-setup-question")
        return AgentRunResult(
            0,
            thread_id="first-setup-question",
            result=generic_result(
                "blocked",
                {
                    "setup_report": {
                        "status": "needs_input",
                        "progress": {
                            "message": "Need project goal",
                            "evidence_ref": "fixture/goal",
                        },
                        "question": {
                            "question_id": "goal",
                            "revision": 1,
                            "prompt": "What is the project goal?",
                            "workflow_keys": ["engineering.tracked-delivery"],
                            "response_channel": "managed_ui",
                        },
                    }
                },
            ),
        )

    install_native(monkeypatch, runner)
    opened = launch(managed, host_key="codex", model="gpt-6.1-sol")
    assert opened.run_plan_id and opened.run_id and opened.task_key
    assert opened.attempt.managed is None
    assert opened.attempt.workflow_admission.run_plan_id == opened.run_plan_id
    assert len(scheduler.jobs) == 1 and scheduler.jobs[0][0] is task_delivery.run_delivery
    attached = launch(managed, host_key="claude-code")
    assert attached.attached and attached.run_plan_id == opened.run_plan_id
    assert attached.attempt.workflow_admission.preferences["host_key"] == "codex"
    assert len(scheduler.jobs) == 1
    assert ProjectSetupRepository(session).managed_admissions() == []
    assert execution.setup_directories(session) == []
    assert sum(capacity_counts(session, scheduler).values()) == 1
    scheduler.run()
    task, receipts = task_receipts(session, project, opened.task_key)
    current = ProjectSetupRepository(session).get(project).current_attempt
    assert current.status == "needs_input" and current.question.question_id == "goal"
    assert current.request_id == opened.attempt.request_id
    receipt = receipts[f"{opened.task_key}-prepare"]["delivery"]
    assert receipt["execution"]["invocation_id"] and receipt["finished_at"]
    assert receipt["thread_id"] == "first-setup-question"
    assert receipt["launch"]["model"] == "gpt-6.1-sol"
    assert receipt["selected_role"] == "main_agent"
    assert task.metadata_json["delivery"]["status"] == "stopped"
    assert len(session.exec(select(RunPlan)).all()) == 1
    assert len(session.exec(select(AgentRequest)).all()) == 1
    assert "native_execution" not in session.get(AgentRequest, current.request_id).metadata_json
    assert len(calls) == 1 and not scheduler.jobs


@pytest.mark.parametrize("shape", ["missing", "unknown", "retired_unknown", "source_only"])
def test_unknown_legacy_owner_still_fences_capacity(managed, session, shape):
    from datetime import datetime

    from stackos.db.models import AgentRequestStatus

    execution, project, scheduler, _ = managed
    payload = {"status": "running"}
    if shape in {"unknown", "retired_unknown"}:
        payload["entry_kind"] = "legacy"
    if shape == "retired_unknown":
        payload.update(status="failed", managed={"slot_reserved": False})
    request_id = stored_setup(session, project, "unclassified-owner", payload)
    row = session.get(AgentRequest, request_id)
    row.status = AgentRequestStatus.CLAIMED
    row.claim_expires_at = datetime(2000, 1, 1)
    if shape == "source_only":
        row.metadata_json = {"native_execution": row.metadata_json["native_execution"]}
    session.add(row)
    session.commit()
    before = deepcopy(row.metadata_json)
    expected = {"project_id": project, "request_id": request_id, "next_action": "inspect_request"}
    for read in (
        lambda: capacity_counts(session, scheduler),
        lambda: execution.setup_directories(session),
    ):
        with pytest.raises(ConflictError) as error:
            read()
        assert error.value.data == expected
    session.expire_all()
    assert session.get(AgentRequest, request_id).metadata_json == before


def test_cancel_started_setup_before_first_report_preserves_possible_edits(
    managed, session, monkeypatch
):
    execution, project, scheduler, factory = managed
    calls = []

    def runner(root, instructions, prompt, schema, *, on_session, **kwargs):
        packet = json.loads(prompt)
        calls.append(packet)
        on_session("cancel-before-report")
        (root / "possible-setup-edit.txt").write_text("retained partial work")
        setup = packet["project_setup"]
        with factory() as check:
            assert (
                ProjectSetupRepository(check)._row(project, "engineering.tracked-delivery") is None
            )
        cancelled = execution.cancel_setup(
            project,
            setup["request_id"],
            expected_revision=setup["expected_revision"],
            external_work_stopped=False,
            session_factory=factory,
            scheduler=scheduler,
        )
        assert cancelled.partial_changes_possible
        return AgentRunResult(
            0,
            thread_id="cancel-before-report",
            result=generic_result(
                outputs={
                    "setup_report": {
                        "status": "partial",
                        "progress": {
                            "message": "Late report after cancellation",
                            "evidence_ref": "fixture/late",
                        },
                    }
                },
            ),
        )

    install_native(monkeypatch, runner)
    opened = launch(managed, host_key="codex")
    scheduler.run()
    task, receipts = task_receipts(session, project, opened.task_key)
    current = ProjectSetupRepository(session).get(project).current_attempt
    assert current.status == "cancelled" and current.partial_changes_possible
    assert ProjectSetupRepository(session)._row(project, "engineering.tracked-delivery") is None
    assert TrackerRepository(session).delivery_defaults(project_id=project) == {}
    assert (
        Path(current.workflow_admission.project_dir) / "possible-setup-edit.txt"
    ).read_text() == "retained partial work"
    receipt = receipts[f"{opened.task_key}-prepare"]["delivery"]
    assert receipt["execution"]["invocation_id"] and receipt["finished_at"]
    assert receipt["validated_outcome"] is None
    assert task.metadata_json["delivery"]["slot_reserved"] is False
    assert len(calls) == 1 and not scheduler.jobs
