"""Managed setup shares real request execution and task capacity owners."""

# Imported pytest fixtures are intentionally shadowed by fixture arguments.
# ruff: noqa: F811

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from threading import Event
from time import perf_counter
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from stackos import task_delivery
from stackos.db.models import AgentRequest, RunPlan, TrackerTask
from stackos.host_mcp import service
from stackos.host_mcp.result import AgentRunResult
from stackos.repositories.base import ValidationError
from stackos.repositories.project_setup import ProjectSetupRepository
from stackos.repositories.tracker import TrackerRepository
from stackos.repositories.workspaces import WorkspaceRepository
from stackos.task_capacity import save_limit
from tests.integration.test_repositories.test_project_setup import verified_result
from tests.integration.test_repositories.test_request_execution import (
    native_lifetime,  # noqa: F401
    threaded_session,  # noqa: F401
)
from tests.integration.test_repositories.test_task_delivery import (
    QueuedScheduler,
    native_source_fixtures,  # noqa: F401
    prepared_task,
    submit,
)


@pytest.fixture
def managed(session, tmp_path, monkeypatch):
    from stackos import project_setup_execution as execution

    folder = tmp_path / "managed"
    folder.mkdir()
    opened = WorkspaceRepository(session).start_session(runtime="codex", cwd=str(folder))
    scheduler = QueuedScheduler()
    monkeypatch.setattr(service, "inspect_host", lambda _: SimpleNamespace(available=True))
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


def execute_job(scheduler):
    scheduler.run()


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


@pytest.mark.parametrize(
    "shape", ["missing", "unknown", "retired_unknown", "source_only", "namespace_only"]
)
def test_unclassified_setup_fences_capacity_directory_and_ordinary_admission(
    managed, session, project_id, tmp_path, shape
):
    from datetime import datetime

    from stackos.db.models import AgentRequestStatus
    from stackos.repositories.base import ConflictError

    execution, project, scheduler, _factory = managed
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
    if shape == "namespace_only":
        row.source_kind = None
    session.add(row)
    session.commit()
    before = deepcopy(row.metadata_json)
    expected = {"project_id": project, "request_id": request_id, "next_action": "inspect_request"}
    for read in (
        lambda: task_delivery.delivery_capacity(session, scheduler),
        lambda: execution.setup_directories(session),
    ):
        with pytest.raises(ConflictError) as error:
            read()
        assert error.value.data == expected
    prepared_task(session, project_id)
    root = tmp_path / "ordinary"
    root.mkdir()
    with pytest.raises(ConflictError) as error:
        submit(session, project_id, root, scheduler)
    assert error.value.data == expected
    session.expire_all()
    assert session.get(AgentRequest, request_id).metadata_json == before
    task = session.exec(
        select(TrackerTask).where(
            TrackerTask.project_id == project_id, TrackerTask.key == "deliver"
        )
    ).one()
    assert task.metadata_json == {"keep": "yes"}
    assert not scheduler.jobs


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
    assert task_delivery.delivery_capacity(session, scheduler)["running_count"] == 0
    assert task_delivery.delivery_capacity(session, scheduler)["queued_count"] == 0
    assert execution.setup_directories(session) == []


@pytest.mark.parametrize("history_count", [0, 1, 40])
def test_admission_projects_only_relevant_setup_scalars(
    managed, session, project_id, tmp_path, monkeypatch, history_count, record_property
):
    from sqlalchemy import event

    from stackos.repositories.project_setup import SetupAdmissionState, SetupAttempt

    execution, project, scheduler, factory = managed
    opened = launch(managed, host_key="codex")
    retired = opened.attempt.model_dump(mode="json")
    retired["schema_version"] = 999  # Intentionally incompatible full history.
    retired["managed"]["slot_reserved"] = False
    for index in range(history_count):
        retired["status"] = (
            "needs_input",
            "success",
            "partial",
            "failed",
            "interrupted",
            "cancelled",
        )[index % 6]
        stored_setup(session, project, f"retired-{index}", retired)

    validations, projected, hydrated, columns, queries = [], [], [], [], []
    validate = SetupAttempt.model_validate
    project_state = SetupAdmissionState.model_validate

    def validating(value, *args, **kwargs):
        validations.append(value["request_id"])
        return validate(value, *args, **kwargs)

    def loaded(_session, instance):
        if isinstance(instance, AgentRequest):
            hydrated.append(instance.id)

    def queried(_connection, cursor, statement, parameters, *_args):
        if "FROM agent_requests" in statement:
            columns.append([column[0] for column in cursor.description])
            queries.append((statement, parameters))

    def projecting(value, *args, **kwargs):
        projected.append(value["request_id"])
        return project_state(value, *args, **kwargs)

    monkeypatch.setattr(SetupAttempt, "model_validate", validating)
    monkeypatch.setattr(SetupAdmissionState, "model_validate", projecting)
    event.listen(Session, "loaded_as_persistent", loaded)
    event.listen(session.get_bind(), "after_cursor_execute", queried)
    try:
        started = perf_counter()
        with factory() as check:
            assert task_delivery.delivery_capacity(check, scheduler)["running_count"] == 1
            assert execution.setup_directories(check) == [
                opened.attempt.managed.settings["project_dir"]
            ]
        elapsed = perf_counter() - started
        assert validations == []
        assert projected == [opened.attempt.request_id] * 2
        assert hydrated == []
        assert columns and all(
            len(selected) == 9
            and "metadata_json" not in selected
            and "body_preview" not in selected
            for selected in columns
        )
        record_property("retired_history_count", history_count)
        record_property("scalar_rows_per_read", len(projected) // 2)
        record_property("projected_column_count", len(columns[0]))
        record_property("full_model_validations", len(validations))
        record_property("hydrated_requests", len(hydrated))
        record_property("elapsed_seconds_supporting_only", elapsed)
        # Real ordinary admission in a different project must survive bad retired history.
        prepared_task(session, project_id)
        root = tmp_path / "ordinary"
        root.mkdir()
        submit(session, project_id, root, scheduler)
        assert task_delivery.delivery_capacity(session, scheduler)["running_count"] == 2
    finally:
        event.remove(Session, "loaded_as_persistent", loaded)
        event.remove(session.get_bind(), "after_cursor_execute", queried)
    with session.get_bind().connect() as connection:
        statement, parameters = queries[0]
        plan = connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters).all()
        record_property("sqlite_query_plan", [row[3] for row in plan])


@pytest.mark.parametrize(
    "change",
    [
        {"status": "unknown"},
        {"status": None},
        {"status": "missing"},
        {"slot_reserved": None},
        {"slot_reserved": "missing"},
        {"slot_reserved": "false"},
        {"slot_reserved": 0},
        {"project_dir": None},
    ],
)
def test_uncertain_setup_ownership_returns_bounded_repair_context(managed, session, change):
    from stackos.repositories.base import ConflictError

    execution, project, scheduler, _factory = managed
    opened = launch(managed, host_key="codex")
    payload = opened.attempt.model_dump(mode="json")
    payload["status"] = "failed"
    payload["managed"]["slot_reserved"] = False
    for name, value in change.items():
        if name == "status":
            if value == "missing":
                payload.pop(name)
            else:
                payload[name] = value
        elif name == "project_dir":
            payload["managed"]["settings"][name] = value
            payload["managed"]["slot_reserved"] = True
        else:
            if value == "missing":
                payload["managed"].pop(name)
            else:
                payload["managed"][name] = value
    request_id = stored_setup(session, project, "uncertain", payload)
    for read in (
        lambda: task_delivery.delivery_capacity(session, scheduler),
        lambda: execution.setup_directories(session),
    ):
        with pytest.raises(ConflictError) as error:
            read()
        assert error.value.data == {
            "project_id": project,
            "request_id": request_id,
            "next_action": "inspect_request",
        }
    assert (
        session.get(AgentRequest, request_id).metadata_json["project_setup"]["managed"]
        == payload["managed"]
    )


def test_setup_reservation_selects_one_current_claimed_candidate(managed, session, monkeypatch):
    from stackos.repositories.base import ConflictError
    from stackos.repositories.project_setup import SetupAttempt

    execution, project, scheduler, _factory = managed
    opened = launch(managed, host_key="codex")
    queued = opened.attempt.model_dump(mode="json")
    queued["managed"]["slot_reserved"] = False
    queued["managed"]["admission_id"] = "prior-daemon"
    stale = stored_setup(session, project, "stale-queue", queued)
    queued["managed"]["admission_id"] = task_delivery._admission_id(scheduler)
    queued["schema_version"] = 999
    missing_claim = stored_setup(session, project, "lost-claim", queued)
    queued["schema_version"] = 1
    first = stored_setup(session, project, "first-current", queued)
    second = stored_setup(session, project, "second-current", queued)
    scheduler.project_setup_claims.update({stale: "old", first: "first", second: "second"})
    validated = []
    validate = SetupAttempt.model_validate

    def validating(value, *args, **kwargs):
        validated.append(value["request_id"])
        return validate(value, *args, **kwargs)

    monkeypatch.setattr(SetupAttempt, "model_validate", validating)
    assert task_delivery.delivery_capacity(session, scheduler)["queued_count"] == 3
    assert execution.reserve_setup(session, scheduler)[2]["request_id"] == first
    assert validated == [first]
    assert execution.reserve_setup(session, scheduler)[2]["request_id"] == second
    assert validated == [first, second]
    assert execution.reserve_setup(session, scheduler) is None
    assert missing_claim not in scheduler.project_setup_claims
    # A restarted daemon cannot recreate claims, even for otherwise valid queues.
    assert execution.reserve_setup(session, QueuedScheduler()) is None
    # An explicitly selected incompatible body fails with bounded repair context.
    scheduler.project_setup_claims[missing_claim] = "retained-existing-claim"
    with pytest.raises(ConflictError) as error:
        execution.reserve_setup(session, scheduler)
    assert error.value.data == {
        "project_id": project,
        "request_id": missing_claim,
        "next_action": "inspect_request",
    }
    assert validated == [first, second, missing_claim]


def test_missed_setup_hydrates_only_exact_job_and_preserves_other_work(
    managed, session, monkeypatch
):
    from stackos.repositories.project_setup import SetupAttempt

    _execution, project, scheduler, factory = managed
    opened = launch(managed, host_key="codex")
    payload = opened.attempt.model_dump(mode="json")
    payload["status"] = "running"
    payload["schema_version"] = 999
    payload["managed"]["job_ref"] = "project-setup:unrelated"
    unrelated = stored_setup(session, project, "unrelated-active", payload)
    before = deepcopy(session.get(AgentRequest, unrelated).metadata_json)
    validated = []
    validate = SetupAttempt.model_validate

    def validating(value, *args, **kwargs):
        validated.append(value["request_id"])
        return validate(value, *args, **kwargs)

    monkeypatch.setattr(SetupAttempt, "model_validate", validating)
    task_delivery.missed_delivery_job(
        SimpleNamespace(job_id=opened.attempt.managed.job_ref),
        session_factory=factory,
        scheduler=scheduler,
    )
    session.expire_all()
    target = session.get(AgentRequest, opened.attempt.request_id).metadata_json["project_setup"]
    assert target["status"] == "failed" and target["managed"]["slot_reserved"] is False
    assert validated == [opened.attempt.request_id]
    assert session.get(AgentRequest, unrelated).metadata_json == before
    assert task_delivery.delivery_capacity(session, scheduler)["running_count"] == 1


def test_prior_daemon_reserved_setup_counts_and_fences_ordinary_directory(managed, session):
    from pathlib import Path

    execution, project, _scheduler, _factory = managed
    opened = launch(managed, host_key="codex")
    row, attempt = execution._load(session, project, opened.attempt.request_id)
    attempt.status = "cancelled"
    attempt.managed.admission_id = "prior-daemon"
    execution._save(session, row, attempt)
    restarted = QueuedScheduler()
    assert task_delivery.delivery_capacity(session, restarted)["running_count"] == 1
    assert task_delivery.delivery_capacity(session, restarted)["queued_count"] == 0
    prepared_task(session, project)
    with pytest.raises(ValidationError, match="working directory"):
        submit(session, project, Path(attempt.managed.settings["project_dir"]), restarted)


def test_duplicate_start_freezes_choice_and_queued_cancel_retires_once(managed, session):
    execution, project, scheduler, factory = managed
    save_limit(session, 1)
    first = launch(managed, host_key="codex", model="gpt-6.1-sol")
    second = launch(managed, host_key="codex", model="gpt-6-astra")
    assert second.attached and second.attempt.request_id == first.attempt.request_id
    assert first.claim_token is None and second.claim_token is None
    assert len(scheduler.jobs) == 1
    TrackerRepository(session).configure_main_agent(
        project_id=project, preferences={"host_key": "codex", "model": "gpt-6-astra"}
    )
    assert task_delivery.delivery_capacity(session, scheduler)["running_count"] == 1
    execution.cancel_managed(
        project, first.attempt.request_id, session_factory=factory, scheduler=scheduler
    )
    execution.cancel_managed(
        project, first.attempt.request_id, session_factory=factory, scheduler=scheduler
    )
    session.expire_all()
    attempt = ProjectSetupRepository(session).get(project).current_attempt
    assert attempt.status == "cancelled"
    assert attempt.partial_changes_possible is False
    assert attempt.managed.settings["model"] == "gpt-6.1-sol"
    assert task_delivery.delivery_capacity(session, scheduler)["running_count"] == 0
    assert not session.exec(select(RunPlan)).all()
    assert not session.exec(select(TrackerTask)).all()


def test_real_request_executor_persists_report_identity_and_releases(managed, session, monkeypatch):
    _execution, project, scheduler, _factory = managed
    definition = service._definition_by_key("codex")

    def runner(root, instructions, prompt, schema, *, on_session, **kwargs):
        on_session("managed-native-fixture")
        context = json.loads(prompt)
        assert context["project_id"] == project
        assert context["guidance_ref"]["tool_name"] == "workflowTemplate.authoringGuide"
        assert str(root) == context["project_dir"]
        return AgentRunResult(
            0,
            thread_id="managed-native-fixture",
            result={
                "report_json": json.dumps(
                    {
                        "status": "partial",
                        "progress": {
                            "message": "Inspected; configuration needs input",
                            "evidence_ref": "fixture:inspection",
                        },
                        "recovery_condition": "Select the remaining project inputs",
                    }
                )
            },
        )

    monkeypatch.setattr(
        service, "_definition_by_key", lambda key: replace(definition, run_agent=runner)
    )
    first = launch(managed, host_key="codex", model="gpt-6.1-sol")
    execute_job(scheduler)
    session.expire_all()
    row = session.get(AgentRequest, first.attempt.request_id)
    evidence = row.metadata_json["native_execution"]
    assert (
        evidence["finished_at"] and evidence["execution"]["thread_id"] == "managed-native-fixture"
    )
    assert evidence["snapshot"]["model"] == "gpt-6.1-sol"
    assert json.loads(evidence["snapshot"]["prompt"])["claim_token"] == "[redacted]"
    assert row.metadata_json["project_setup"]["status"] == "partial"
    assert task_delivery.delivery_capacity(session, scheduler)["running_count"] == 0


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
    assert first.attempt.managed.settings["model"] == "gpt-6.1-sol"


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
    assert opened.attempt.managed.settings["host_key"] == "codex"
    assert "model" not in opened.attempt.managed.settings
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


@pytest.mark.parametrize("setup_first", [True, False])
def test_setup_and_delivery_compete_in_both_orders(
    managed, session, tmp_path, monkeypatch, setup_first
):
    execution, project, scheduler, factory = managed
    save_limit(session, 1)
    prepared_task(session, project)
    monkeypatch.setattr(task_delivery, "_select_work", lambda *args: (None, None, False))
    root = tmp_path / "task"
    root.mkdir()
    if setup_first:
        opened = launch(managed, host_key="codex")
        submit(session, project, root, scheduler)
    else:
        submit(session, project, root, scheduler)
        opened = launch(managed, host_key="codex")
    session.expire_all()
    current = ProjectSetupRepository(session).get(project).current_attempt
    assert current.status == "queued"
    assert current.managed.slot_reserved == setup_first
    assert task_delivery.delivery_capacity(session, scheduler)["running_count"] == 1
    assert task_delivery.delivery_capacity(session, scheduler)["queued_count"] == 1
    execution.cancel_managed(
        project, opened.attempt.request_id, session_factory=factory, scheduler=scheduler
    )
    scheduler.run()
    assert task_delivery.delivery_capacity(session, scheduler)["running_count"] == 0


@pytest.mark.parametrize("queue_removal_fails", [False, True])
def test_live_cancel_retains_reports_fences_late_report_and_releases_after_callback(
    threaded_session, tmp_path, monkeypatch, queue_removal_fails
):
    from stackos import project_setup_execution as execution
    from stackos.mcp.context import MCPContext
    from stackos.operations.project_setup_state import ProjectSetupReportInput, project_setup_report
    from stackos.repositories.base import ConflictError

    session = threaded_session
    root = tmp_path / "live"
    root.mkdir()
    opened = WorkspaceRepository(session).start_session(
        runtime="codex", cwd=str(root), client_session_id="native"
    )
    project = opened.project_id

    def factory():
        return Session(session.get_bind())

    scheduler = QueuedScheduler()
    monkeypatch.setattr(service, "inspect_host", lambda _: SimpleNamespace(available=True))
    definition = service._definition_by_key("codex")
    entered, finish = Event(), Event()
    checked = []

    def runner(root, instructions, prompt, schema, *, on_session, **kwargs):
        on_session("live-native-session")
        context = json.loads(prompt)
        with factory() as own:
            repo = ProjectSetupRepository(own)
            proof = verified_result(repo, project)
            proof.attempt_ref = context["attempt_ref"]
            arguments = dict(
                project_id=project,
                session_id=opened.data.id,
                client_session_id="native",
                request_id=context["request_id"],
                claim_token=context["claim_token"],
                expected_revision=1,
                report={
                    "status": "running",
                    "progress": {
                        "message": "First workflow checked",
                        "evidence_ref": "fixture:partial",
                    },
                    "workflow_results": [
                        {
                            "workflow_key": "engineering.tracked-delivery",
                            "expected_revision": 0,
                            "result": proof.model_dump(mode="json"),
                        }
                    ],
                },
            )
            ctx = MCPContext(session=own, request_id="native-report", extras={"surface": "mcp"})
            with pytest.raises(ConflictError):
                asyncio.run(
                    project_setup_report(
                        ProjectSetupReportInput(**{**arguments, "claim_token": "foreign"}),
                        ctx,
                        None,
                    )
                )
            assert repo.get(project).configured_count == 0
            envelope = asyncio.run(
                project_setup_report(ProjectSetupReportInput(**arguments), ctx, None)
            )
            assert envelope.data.revision == 2
            checked.append(True)
            entered.set()
            assert finish.wait(10)
            arguments.update(
                expected_revision=2,
                report={
                    "status": "failed",
                    "progress": {"message": "late mutation", "evidence_ref": "fixture:late"},
                },
            )
            with pytest.raises(ConflictError, match="cancellation"):
                asyncio.run(project_setup_report(ProjectSetupReportInput(**arguments), ctx, None))
        return AgentRunResult(0, thread_id="live-native-session", result={"report_json": "{}"})

    monkeypatch.setattr(
        service, "_definition_by_key", lambda _: replace(definition, run_agent=runner)
    )
    managed = (execution, project, scheduler, factory)
    started = launch(managed, host_key="codex")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(scheduler.run)
        try:
            assert entered.wait(10)
            if queue_removal_fails:

                def unavailable_queue(*args, **kwargs):
                    raise RuntimeError("fixture queue unavailable")

                monkeypatch.setattr(scheduler, "remove_job", unavailable_queue)
            cancelled = execution.cancel_managed(
                project, started.attempt.request_id, session_factory=factory, scheduler=scheduler
            )
            assert cancelled.managed.cancel_requested and cancelled.managed.slot_reserved
            assert cancelled.status == "running"
            with factory() as check:
                assert ProjectSetupRepository(check).get(project).configured_count == 1
                assert task_delivery.delivery_capacity(check, scheduler)["running_count"] == 1
        finally:
            finish.set()
        future.result(timeout=10)
    session.expire_all()
    after = ProjectSetupRepository(session).get(project)
    assert checked and after.configured_count == 1
    assert after.current_attempt.status == "cancelled"
    assert len(after.current_attempt.reports) == 1
    assert task_delivery.delivery_capacity(session, scheduler)["running_count"] == 0


def test_restart_or_timeout_never_reclassifies_unconfirmed_live_work(managed, session):
    execution, project, _scheduler, factory = managed
    opened = launch(managed, host_key="codex")
    row, attempt = execution._load(session, project, opened.attempt.request_id)
    attempt.status = "running"
    attempt.managed.callback_started = True
    row.metadata_json = {
        **row.metadata_json,
        "native_execution": {"finished_at": None, "execution": {"thread_id": "still-live"}},
    }
    execution._save(session, row, attempt)
    restarted = QueuedScheduler()
    after = execution.cancel_managed(project, row.id, session_factory=factory, scheduler=restarted)
    assert after.status == "running" and after.managed.slot_reserved
    assert "reconciliation" in after.managed.diagnostic
    assert task_delivery.delivery_capacity(session, restarted)["running_count"] == 1
    assert launch((execution, project, restarted, factory)).attached


def test_changed_folder_and_missed_job_fail_without_launch(managed, session, monkeypatch):
    execution, project, scheduler, factory = managed
    opened = launch(managed, host_key="codex")
    row, attempt = execution._load(session, project, opened.attempt.request_id)
    attempt.managed.settings["project_dir"] += "-unavailable"
    execution._save(session, row, attempt)
    monkeypatch.setattr(
        service, "run_agent", lambda **kw: pytest.fail("must fail before native launch")
    )
    scheduler.run()
    session.expire_all()
    assert ProjectSetupRepository(session).get(project).current_attempt.status == "failed"
    assert task_delivery.delivery_capacity(session, scheduler)["running_count"] == 0
    opened = launch(managed, host_key="codex")
    scheduler.jobs.clear()
    task_delivery.missed_delivery_job(
        SimpleNamespace(job_id=opened.attempt.managed.job_ref),
        session_factory=factory,
        scheduler=scheduler,
    )
    session.expire_all()
    assert ProjectSetupRepository(session).get(project).current_attempt.status == "failed"
    assert task_delivery.delivery_capacity(session, scheduler)["running_count"] == 0


@pytest.mark.parametrize("setup_first", [True, False])
def test_setup_and_delivery_preserve_directory_exclusivity(managed, session, setup_first):
    from pathlib import Path

    from stackos.repositories.base import ConflictError
    from stackos.repositories.projects import ProjectRepository

    _, project, scheduler, _ = managed
    root = Path(ProjectRepository(session).get(project).project_dir)
    prepared_task(session, project)
    if setup_first:
        launch(managed, host_key="codex")
        with pytest.raises(ValidationError, match="working directory"):
            submit(session, project, root, scheduler)
    else:
        submit(session, project, root, scheduler)
        with pytest.raises(ConflictError, match="folder is owned"):
            launch(managed, host_key="codex")


def test_managed_goal_question_does_not_select_example_workflows(managed, session, monkeypatch):
    execution, project, scheduler, factory = managed
    definition = service._definition_by_key("codex")

    def runner(*args, on_session, **kwargs):
        on_session("goal-question")
        return AgentRunResult(
            0,
            thread_id="goal-question",
            result={
                "report_json": json.dumps(
                    {
                        "status": "needs_input",
                        "progress": {
                            "message": "Inspected project intent",
                            "evidence_ref": "fixture:goal",
                        },
                        "question": {
                            "question_id": "goal",
                            "revision": 1,
                            "prompt": "What should this project do?",
                            "workflow_keys": [],
                            "response_channel": "managed_ui",
                        },
                    }
                )
            },
        )

    monkeypatch.setattr(
        service, "_definition_by_key", lambda _: replace(definition, run_agent=runner)
    )
    opened = execution.start_managed(
        project_id=project,
        session_factory=factory,
        scheduler=scheduler,
        mode="setup",
        workflow_keys=[],
        preferences={"host_key": "codex"},
    )
    scheduler.run()
    session.expire_all()
    current = ProjectSetupRepository(session).get(project).current_attempt
    assert current.status == "needs_input" and current.workflow_keys == []
    assert not current.managed.slot_reserved and current.thread_id == "goal-question"
    cancelled = execution.cancel_managed(
        project, opened.attempt.request_id, session_factory=factory, scheduler=scheduler
    )
    assert cancelled.status == "cancelled" and len(cancelled.reports) == 1


@pytest.mark.parametrize("problem", ["host", "folder"])
def test_unavailable_host_or_folder_creates_no_request(managed, session, monkeypatch, problem):
    from pathlib import Path

    from stackos.repositories.projects import ProjectRepository

    _, project, _, _ = managed
    if problem == "host":
        monkeypatch.setattr(
            service,
            "inspect_host",
            lambda _: SimpleNamespace(available=False, message="Install this host"),
        )
    else:
        Path(ProjectRepository(session).get(project).project_dir).rmdir()
    with pytest.raises(ValidationError, match=r"host needs setup|bound folder"):
        launch(managed, host_key="codex")
    assert not session.exec(select(AgentRequest)).all()


def test_unknown_managed_reservation_blocks_cleanup_after_request_lease_expires(
    managed, session, tmp_path
):
    from datetime import UTC, datetime, timedelta

    from stackos.config import Settings
    from stackos.project_cleanup import ProjectCleanupService

    execution, project, _scheduler, _factory = managed
    opened = launch(managed, host_key="codex")
    row, attempt = execution._load(session, project, opened.attempt.request_id)
    attempt.status = "interrupted"
    attempt.managed.callback_started = True
    row.claim_expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    execution._save(session, row, attempt)
    cleanup = ProjectCleanupService(
        session, Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state")
    )
    assert "claimed_request" not in cleanup._blockers(project)
    assert "managed_setup" in cleanup._blockers(project)
    from stackos.db.models import Project

    project_row = session.get(Project, project)
    result = asyncio.run(
        cleanup.delete(
            project,
            confirmation={
                "permanent": True,
                "project_id": project,
                "project_slug": project_row.slug,
            },
        )
    )
    assert result.status == "blocked" and "managed_setup" in result.blockers
    assert session.get(Project, project) is not None


def test_queued_cancel_survives_queue_error_and_late_callback_keeps_replacement_slot(
    managed, session, monkeypatch
):
    execution, project, scheduler, factory = managed
    opened = launch(managed, host_key="codex")

    def unavailable_queue(*args, **kwargs):
        raise RuntimeError("fixture queue unavailable")

    monkeypatch.setattr(scheduler, "remove_job", unavailable_queue)
    cancelled = execution.cancel_managed(
        project, opened.attempt.request_id, session_factory=factory, scheduler=scheduler
    )
    assert cancelled.status == "cancelled" and "queue removal" in cancelled.managed.diagnostic
    task_delivery.missed_delivery_job(
        SimpleNamespace(job_id=cancelled.managed.job_ref),
        session_factory=factory,
        scheduler=scheduler,
    )
    replacement = launch(managed, host_key="codex")
    monkeypatch.setattr(
        service, "run_agent", lambda **kw: pytest.fail("cancelled callback cannot launch")
    )
    scheduler.run()
    session.expire_all()
    assert execution._load(session, project, opened.attempt.request_id)[1].status == "cancelled"
    assert task_delivery.delivery_capacity(session, scheduler)["running_count"] == 1
    assert replacement.attempt.request_id != opened.attempt.request_id
    execution.cancel_managed(
        project, replacement.attempt.request_id, session_factory=factory, scheduler=scheduler
    )


def test_supported_host_can_report_before_native_thread_identity_is_available(
    managed, session, monkeypatch
):
    from stackos.mcp.context import MCPContext
    from stackos.operations.project_setup_state import ProjectSetupReportInput, project_setup_report

    _execution, project, scheduler, _factory = managed
    from stackos.repositories.projects import ProjectRepository

    root = ProjectRepository(session).get(project).project_dir
    reporter = WorkspaceRepository(session).start_session(
        runtime="claude-code", cwd=root, client_session_id="claude-native"
    )
    definition = service._definition_by_key("claude-code")
    observed = []

    def runner(root, instructions, prompt, schema, **kwargs):
        context = json.loads(prompt)
        assert service.active_request_execution(project, context["request_id"])["thread_id"] is None
        report = {
            "status": "needs_input",
            "progress": {"message": "Inspected native project", "evidence_ref": "fixture:host"},
            "question": {
                "question_id": "goal",
                "revision": 1,
                "prompt": "What is the goal?",
                "workflow_keys": ["engineering.tracked-delivery"],
                "response_channel": "managed_ui",
            },
        }
        envelope = asyncio.run(
            project_setup_report(
                ProjectSetupReportInput(
                    project_id=project,
                    session_id=reporter.data.id,
                    client_session_id="claude-native",
                    request_id=context["request_id"],
                    claim_token=context["claim_token"],
                    expected_revision=1,
                    report=report,
                ),
                MCPContext(session=session, request_id="host-report", extras={"surface": "mcp"}),
                None,
            )
        )
        observed.append(envelope.data.revision)
        return AgentRunResult(
            0, thread_id="claude-final-thread", result={"report_json": json.dumps(report)}
        )

    monkeypatch.setattr(
        service, "_definition_by_key", lambda _: replace(definition, run_agent=runner)
    )
    launch(managed, host_key="claude-code", model="claude-sonnet-5-5")
    scheduler.run()
    session.expire_all()
    current = ProjectSetupRepository(session).get(project).current_attempt
    assert observed == [2] and current.status == "needs_input"
    assert current.thread_id == "claude-final-thread" and not current.managed.slot_reserved
