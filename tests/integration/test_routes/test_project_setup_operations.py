"""Browser-safe reminder administration through the canonical REST adapter."""

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from sqlmodel import Session

from stackos.auth import derive_ui_token
from stackos.host_mcp import service
from stackos.host_mcp.result import HostMcpResult
from stackos.repositories.project_setup import ProjectSetupRepository
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.workspaces import WorkspaceRepository
from tests.helpers.native_task_routes import controlled_profile_host as controlled_profile_host
from tests.integration.test_repositories.test_project_setup import verified_result
from tests.integration.test_repositories.test_task_delivery import QueuedScheduler


def _available_host(host, **_kwargs):
    result = service.normalize_result(
        HostMcpResult(host, "fixture", "registered_current", "Connected", True, True),
        service._definition_by_key(host).policy,
    )
    return replace(result, agent_execution={**result.agent_execution, "runnable": "available"})


@pytest.mark.parametrize("transition", ["changed", "unknown", "unknown-before"])
def test_console_setup_selection_fences_account_probe(
    api, settings, tmp_path, monkeypatch, transition
):
    from stackos.host_mcp.accounts import DesiredAccount
    from stackos.project_setup_execution import resolve_preferences
    from stackos.repositories.tracker import TrackerRepository

    with Session(api.app.state.engine) as session:
        project = (
            WorkspaceRepository(session)
            .start_session(runtime="codex", cwd=str(tmp_path))
            .project_id
        )
    cli = tmp_path / "grok"
    cli.write_text("#!/bin/sh\nprintf '1.0.92\\n'\n")
    cli.chmod(0o755)
    revision = 0
    changing = False
    model_reads = []
    account_reads = []

    def models(_cli):
        nonlocal revision
        model_reads.append(revision)
        if changing:
            revision += 1
        return ("grok-4.5", "old-account-only-model")

    def account(_definition, *, data_dir=None):
        account_reads.append((data_dir, revision))
        if changing and (
            transition == "unknown-before" or (transition == "unknown" and revision > 0)
        ):
            return False
        return DesiredAccount(revision=revision)

    original_definition = service._definition_by_key
    grok = replace(original_definition("grok"), cli=lambda _home: str(cli), native_models=models)
    monkeypatch.setattr(
        service,
        "_definition_by_key",
        lambda key: grok if key == "grok" else original_definition(key),
    )
    monkeypatch.setattr(service, "_catalog_account", account)
    monkeypatch.setattr(
        service,
        "_invoke_adapter",
        lambda *_args, **_kwargs: HostMcpResult(
            "grok", "fixture", "registered_current", "Connected", True, True
        ),
    )
    ui = {"Authorization": f"Bearer {derive_ui_token(settings.token_path.read_text().strip())}"}

    def save(model):
        return api.post(
            "/api/v1/operations/tracker.configureDelivery/call",
            headers=ui,
            json={
                "arguments": {
                    "project_id": project,
                    "main_agent": {"host_key": "grok", "model": model},
                },
            },
        )

    stable = save("grok-4.5")
    assert stable.status_code == 200, stable.text
    with Session(api.app.state.engine) as session:
        saved = TrackerRepository(session).delivery_defaults(project_id=project)
        # Managed/background sessions have no request operation_services; they
        # must retain the data directory belonging to this database as well.
        assert resolve_preferences(session, project) == {"host_key": "grok", "model": "grok-4.5"}
    prior_reads = len(model_reads)
    changing = True
    rejected = save("old-account-only-model")
    assert rejected.status_code == 422, rejected.text
    assert "changed" in rejected.text and "refresh" in rejected.text
    assert account_reads and all(directory == settings.data_dir for directory, _ in account_reads)
    if transition == "unknown-before":
        assert len(model_reads) == prior_reads
    with Session(api.app.state.engine) as session:
        assert TrackerRepository(session).delivery_defaults(project_id=project) == saved


@pytest.mark.parametrize("configured_host", [None, "grok", "hermes"])
def test_console_previews_inherited_host_without_replacing_saved_choices(
    api, settings, tmp_path, monkeypatch, controlled_profile_host, configured_host
):
    from stackos.project_setup_execution import resolve_preferences
    from stackos.repositories.tracker import TrackerRepository

    probes = []

    def inspect(host, **_kwargs):
        probes.append(host)
        return _available_host(host)

    monkeypatch.setattr(service, "inspect_host", inspect)
    with Session(api.app.state.engine) as session:
        project = (
            WorkspaceRepository(session)
            .start_session(runtime="codex", cwd=str(tmp_path))
            .project_id
        )
        if configured_host:
            TrackerRepository(session).configure_delivery(
                project_id=project, delivery={"host_key": configured_host}
            )
        before = TrackerRepository(session).delivery_defaults(project_id=project)
    ui = {"Authorization": f"Bearer {derive_ui_token(settings.token_path.read_text().strip())}"}

    def call(name, **arguments):
        return api.post(
            f"/api/v1/operations/{name}/call",
            json={"arguments": {"project_id": project, **arguments}},
            headers=ui,
        )

    saved = (
        {"model": "custom/model", "provider": "configured", "profile": "project-profile"}
        if configured_host
        else {"model": "gpt-6-luna", "model_reasoning_effort": "high", "provider": "configured"}
    )
    configured = call("tracker.configureDelivery", main_agent=saved)
    if configured_host == "hermes":
        assert configured.status_code == 422, configured.text
        assert "Hermes managed execution is unavailable" in configured.text
        with Session(api.app.state.engine) as session:
            defaults = TrackerRepository(session).delivery_defaults(project_id=project)
            assert defaults == before
        return
    assert configured.status_code == 200, configured.text
    probes.clear()
    raw = call("projectSetup.get", response_mode="raw")
    assert raw.status_code == 200, raw.text
    assert raw.json()["launch_preferences"] == saved and not probes
    preview = call("projectSetup.get", resolve_launch_preferences=True, response_mode="raw")
    assert preview.status_code == 200, preview.text
    expected = {"host_key": configured_host or "codex", **saved}
    assert preview.json()["launch_preferences"] == expected
    with Session(api.app.state.engine) as session:
        assert resolve_preferences(session, project) == expected
        assert (
            TrackerRepository(session).delivery_defaults(project_id=project)["main_agent"] == saved
        )


def test_console_selection_reaches_native_launch_and_frozen_exact_resume(
    api, settings, tmp_path, monkeypatch
):
    from stackos.db.models import AgentRequest
    from stackos.host_mcp import progress
    from stackos.host_mcp.adapters import codex

    with Session(api.app.state.engine) as session:
        project = (
            WorkspaceRepository(session)
            .start_session(runtime="codex", cwd=str(tmp_path))
            .project_id
        )
    scheduler = QueuedScheduler()
    api.app.state.operation_services["scheduler"] = scheduler
    monkeypatch.setattr(service, "inspect_host", _available_host)
    monkeypatch.setattr(codex, "_select_codex_bin", lambda: "/fixture/codex")
    commands = []

    def native(command, *, on_session, **kwargs):
        commands.append(command)
        on_session("frozen-setup-thread")
        schema = json.loads(Path(command[command.index("--output-schema") + 1]).read_text())
        assert set(schema["required"]) == set(schema["properties"])
        assert schema["properties"]["verification_status"]["type"] == ["string", "null"]
        Path(command[command.index("-o") + 1]).write_text(
            json.dumps(
                {
                    "verification_status": None,
                    "report_json": json.dumps(
                        {
                            "status": "partial",
                            "progress": {
                                "message": "Fixture checked",
                                "evidence_ref": "fixture:native-command",
                            },
                        }
                    ),
                }
            )
        )
        return subprocess.CompletedProcess(
            command, 0, '{"type":"thread.started","thread_id":"frozen-setup-thread"}', ""
        )

    monkeypatch.setattr(progress, "run_codex_process", native)
    ui = {"Authorization": f"Bearer {derive_ui_token(settings.token_path.read_text().strip())}"}

    def call(name, **arguments):
        return api.post(
            f"/api/v1/operations/{name}/call",
            json={"arguments": {"project_id": project, **arguments}},
            headers=ui,
        )

    assert (
        call(
            "tracker.configureDelivery",
            main_agent={
                "host_key": "codex",
                "model": "gpt-6.2-sol",
                "model_reasoning_effort": "low",
            },
        ).status_code
        == 200
    )
    rejected = call(
        "projectSetup.start",
        entry_kind="managed_ui",
        preferences={"model_reasoning_effort": "none"},
    )
    assert rejected.status_code == 422, rejected.text
    assert "reasoning effort" in rejected.text and not commands and not scheduler.jobs
    first = call(
        "projectSetup.start",
        entry_kind="managed_ui",
        preferences={"model_reasoning_effort": "ultra"},
        response_mode="raw",
    )
    assert first.status_code == 200, first.text
    attempt = first.json()["data"]["attempt"]
    assert attempt["managed"]["settings"]["model_reasoning_effort"] == "ultra"
    scheduler.run()
    assert len(commands) == 1
    assert (
        call(
            "tracker.configureDelivery",
            main_agent={
                "host_key": "codex",
                "model": "gpt-6-luna",
                "model_reasoning_effort": "low",
            },
        ).status_code
        == 200
    )
    state = call("projectSetup.get", response_mode="raw").json()
    current = state["current_attempt"]
    assert current["status"] == "partial" and current["thread_id"] == "frozen-setup-thread"
    assert state["launch_preferences"]["model_reasoning_effort"] == "low"
    continued = call(
        "projectSetup.continue",
        request_id=current["request_id"],
        expected_revision=current["revision"],
        response_mode="raw",
    )
    assert continued.status_code == 200, continued.text
    scheduler.run()
    assert len(commands) == 2
    for command in commands:
        assert command[command.index("--model") + 1] == "gpt-6.2-sol"
        assert 'model_reasoning_effort="ultra"' in command
        assert "--approve-for-me" in command
    assert "resume" not in commands[0]
    assert commands[1][commands[1].index("resume") + 1] == "frozen-setup-thread"
    with Session(api.app.state.engine) as session:
        for request_id in (
            current["request_id"],
            continued.json()["data"]["attempt"]["request_id"],
        ):
            snapshot = session.get(AgentRequest, request_id).metadata_json["native_execution"][
                "snapshot"
            ]
            assert snapshot["model_reasoning_effort"] == "ultra"
            assert snapshot["native_settings"]["model_reasoning_effort"] == "ultra"


def test_console_explicit_null_clears_launch_only_and_configure_clears_saved_defaults(
    api, settings, tmp_path, monkeypatch
):
    with Session(api.app.state.engine) as session:
        project = (
            WorkspaceRepository(session)
            .start_session(runtime="codex", cwd=str(tmp_path))
            .project_id
        )
    scheduler = QueuedScheduler()
    api.app.state.operation_services["scheduler"] = scheduler
    monkeypatch.setattr(service, "inspect_host", _available_host)
    ui = {"Authorization": f"Bearer {derive_ui_token(settings.token_path.read_text().strip())}"}

    def call(name, **arguments):
        return api.post(
            f"/api/v1/operations/{name}/call",
            json={"arguments": {"project_id": project, **arguments}},
            headers=ui,
        )

    saved = {
        "host_key": "codex",
        "model": "gpt-6.1-sol",
        "model_reasoning_effort": "high",
        "provider": "native-provider",
    }
    assert call("tracker.configureDelivery", main_agent=saved).status_code == 200
    opened = call(
        "projectSetup.start",
        entry_kind="managed_ui",
        preferences={"model": None, "model_reasoning_effort": None, "provider": None},
        response_mode="raw",
    )
    assert opened.status_code == 200, opened.text
    selected = opened.json()["data"]["attempt"]["managed"]["settings"]
    assert selected["host_key"] == "codex" and not set(selected) & {
        "model",
        "model_reasoning_effort",
        "provider",
    }
    assert call("projectSetup.get", response_mode="raw").json()["launch_preferences"] == saved
    assert (
        call(
            "tracker.configureDelivery",
            main_agent={"host_key": "codex", "model": None, "model_reasoning_effort": None},
        ).status_code
        == 200
    )
    assert call("projectSetup.get", response_mode="raw").json()["launch_preferences"] == {
        "host_key": "codex"
    }


def test_console_existing_agent_cancel_requires_ack_revision_and_fences_replay(
    api, settings, tmp_path
):
    from stackos.db.models import AgentRequest

    with Session(api.app.state.engine) as session:
        opened = WorkspaceRepository(session).start_session(runtime="codex", cwd=str(tmp_path))
        repo = ProjectSetupRepository(session)
        started = repo.start(
            opened.project_id,
            session_id=opened.data.id,
            mode="setup",
            workflow_keys=["agency.project-setup"],
        )
    api.app.state.operation_services["scheduler"] = QueuedScheduler()
    ui = {"Authorization": f"Bearer {derive_ui_token(settings.token_path.read_text().strip())}"}
    args = {
        "project_id": opened.project_id,
        "request_id": started.attempt.request_id,
        "expected_revision": 1,
        "external_work_stopped": True,
        "idempotency_key": "cancel-external",
        "response_mode": "raw",
    }

    def call(arguments):
        return api.post(
            "/api/v1/operations/projectSetup.cancel/call", json={"arguments": arguments}, headers=ui
        )

    absent = call({key: value for key, value in args.items() if key != "external_work_stopped"})
    assert absent.status_code == 409, absent.text
    for ack in ("true", 1):
        invalid = call({**args, "external_work_stopped": ack})
        assert invalid.status_code == 422, invalid.text
    cli = api.post(
        "/api/v1/operations/projectSetup.cancel/call",
        json={"arguments": args},
        headers={**ui, "X-StackOS-Client-Surface": "cli"},
    )
    assert cli.status_code == 403, cli.text
    from tests.integration.test_routes.test_operations_routes import _start_mock_run_plan

    _, run_token, _, _ = _start_mock_run_plan(api, opened.project_id)
    run_denied = call({**args, "run_token": run_token})
    assert run_denied.status_code == 403, run_denied.text

    for patch in (
        {"external_work_stopped": False},
        {"expected_revision": None},
        {"expected_revision": 2},
        {"project_id": opened.project_id + 999},
    ):
        rejected = call({**args, **patch})
        assert rejected.status_code in {404, 409, 422}, rejected.text
        with Session(api.app.state.engine) as session:
            row = session.get(AgentRequest, started.attempt.request_id)
            assert row.claim_token_hash and row.metadata_json["project_setup"]["revision"] == 1
    cancelled = call(args)
    assert cancelled.status_code == 200, cancelled.text
    saved = cancelled.json()["data"]
    assert saved["status"] == "cancelled" and saved["revision"] == 2
    assert "operator" in saved["recovery_condition"].lower()
    with Session(api.app.state.engine) as session:
        assert session.get(AgentRequest, started.attempt.request_id).claim_token_hash is None
        successor = ProjectSetupRepository(session).start(
            opened.project_id,
            session_id=opened.data.id,
            mode="setup",
            workflow_keys=["agency.project-setup"],
        )
    replay = call({**args, "response_mode": "compact"})
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["request_id"] == started.attempt.request_id
    for patch in (
        {"request_id": successor.attempt.request_id},
        {"expected_revision": 2},
        {"external_work_stopped": False},
    ):
        rejected = call({**args, **patch})
        assert rejected.status_code == 409, rejected.text
    with Session(api.app.state.engine) as session:
        assert (
            ProjectSetupRepository(session).get(opened.project_id).current_attempt
            == successor.attempt
        )


@pytest.mark.parametrize("report_status", [None, "running", "needs_input"])
def test_external_cancel_records_unreported_edit_uncertainty(
    api, settings, tmp_path, report_status
):
    from stackos.db.models import AgentRequest
    from stackos.repositories.project_setup import SetupReport

    key = "agency.project-setup"
    with Session(api.app.state.engine) as session:
        opened = WorkspaceRepository(session).start_session(runtime="codex", cwd=str(tmp_path))
        repo = ProjectSetupRepository(session)
        repo.record_result(
            opened.project_id,
            key,
            expected_revision=0,
            result=verified_result(repo, opened.project_id, key),
        )
        receipt_before = repo._row(opened.project_id, key).model_dump(mode="json")
        started = repo.start(
            opened.project_id, session_id=opened.data.id, mode="setup", workflow_keys=[key]
        )
        before = started.attempt
        if report_status:
            before = repo.report(
                opened.project_id,
                request_id=before.request_id,
                session_id=opened.data.id,
                claim_token=started.claim_token,
                expected_revision=before.revision,
                report=SetupReport(
                    status=report_status,
                    progress={
                        "message": "Setup is in progress",
                        "evidence_ref": "fixture/progress",
                    },
                    question={
                        "question_id": "local-choice",
                        "revision": 1,
                        "prompt": "Keep the local guidance?",
                        "workflow_keys": [key],
                        "answer_state": "pending",
                        "response_channel": "existing_agent",
                    }
                    if report_status == "needs_input"
                    else None,
                ),
            )
        assert before.partial_changes_possible is False
        snapshot = before.model_dump(mode="json")
    # An external writer can change files before it reports any result.
    edited = tmp_path / "partial-guidance.md"
    edited.write_text("Unreported external setup edit.\n")
    api.app.state.operation_services["scheduler"] = QueuedScheduler()
    ui = {"Authorization": f"Bearer {derive_ui_token(settings.token_path.read_text().strip())}"}
    args = {
        "project_id": opened.project_id,
        "request_id": before.request_id,
        "expected_revision": before.revision,
        "external_work_stopped": True,
        "idempotency_key": "cancel-unreported",
        "response_mode": "raw",
    }
    path = "/api/v1/operations/projectSetup.cancel/call"
    response = api.post(path, json={"arguments": args}, headers=ui)
    assert response.status_code == 200, response.text
    cancelled = response.json()["data"]
    assert cancelled["partial_changes_possible"] is True
    assert cancelled == {
        **snapshot,
        "status": "cancelled",
        "revision": before.revision + 1,
        "partial_changes_possible": True,
        "recovery_condition": cancelled["recovery_condition"],
    }
    readback = api.post(
        "/api/v1/operations/projectSetup.get/call",
        json={"arguments": {"project_id": opened.project_id, "response_mode": "raw"}},
        headers=ui,
    )
    assert readback.status_code == 200, readback.text
    assert readback.json()["current_attempt"] == cancelled
    with Session(api.app.state.engine) as session:
        row = session.get(AgentRequest, before.request_id)
        assert row.claim_token_hash is None
        assert row.metadata_json["project_setup"]["partial_changes_possible"] is True
        repo = ProjectSetupRepository(session)
        assert repo._row(opened.project_id, key).model_dump(mode="json") == receipt_before
        successor = repo.start(
            opened.project_id, session_id=opened.data.id, mode="setup", workflow_keys=[key]
        )
    replay = api.post(path, json={"arguments": args}, headers=ui)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"] == cancelled
    with Session(api.app.state.engine) as session:
        repo = ProjectSetupRepository(session)
        assert repo.get(opened.project_id).current_attempt == successor.attempt
        assert repo._row(opened.project_id, key).model_dump(mode="json") == receipt_before
    assert edited.read_text() == "Unreported external setup edit.\n"


def test_browser_reminders_are_independent_permanent_and_not_completion(api, project_id, settings):
    ui = {"Authorization": f"Bearer {derive_ui_token(settings.token_path.read_text().strip())}"}
    path = "/api/v1/operations/projectSetup.setReminders/call"
    response = api.post(
        path,
        json={
            "arguments": {"project_id": project_id, "dismiss_setup": True, "response_mode": "raw"}
        },
        headers=ui,
    )
    assert response.status_code == 200, response.text
    first = response.json()["data"]
    assert first["setup_reminder_dismissed_at"] and not first["update_reminder_dismissed_at"]
    assert first["configured_count"] == 0
    second = api.post(
        path,
        json={
            "arguments": {"project_id": project_id, "dismiss_updates": True, "response_mode": "raw"}
        },
        headers=ui,
    )
    assert second.status_code == 200, second.text
    assert second.json()["data"]["update_reminder_dismissed_at"]
    with Session(api.app.state.engine) as session:
        ProjectRepository(session).update(project_id, name="Renamed setup fixture")
    read = api.post(
        "/api/v1/operations/projectSetup.get/call",
        json={"arguments": {"project_id": project_id, "response_mode": "raw"}},
        headers=ui,
    )
    assert read.status_code == 200, read.text
    assert read.json() == second.json()["data"]
    # Extra project mutation fields never turn this into a generic project write.
    rejected = api.post(
        path,
        json={
            "arguments": {
                "project_id": project_id,
                "dismiss_setup": False,
                "patch": {"name": "Injected"},
            }
        },
        headers=ui,
    )
    assert rejected.status_code == 422, rejected.text
    for name in ("get", "setReminders"):
        invalid = api.post(
            f"/api/v1/operations/projectSetup.{name}/call",
            json={"arguments": {"project_id": project_id, "response_mode": "ack"}},
            headers=ui,
        )

        assert invalid.status_code == 422, invalid.text
    assert (
        api.post(path, json={"arguments": {"project_id": project_id}}, headers=ui).status_code
        == 200
    )


def test_console_managed_start_duplicate_cancel_and_preferences(
    api, settings, tmp_path, monkeypatch
):
    with Session(api.app.state.engine) as session:
        startup = WorkspaceRepository(session).start_session(runtime="codex", cwd=str(tmp_path))
        project = startup.project_id
    scheduler = QueuedScheduler()
    api.app.state.operation_services["scheduler"] = scheduler
    monkeypatch.setattr(service, "inspect_host", _available_host)
    ui = {"Authorization": f"Bearer {derive_ui_token(settings.token_path.read_text().strip())}"}

    def call(name, arguments):
        return api.post(
            f"/api/v1/operations/{name}/call", json={"arguments": arguments}, headers=ui
        )

    saved = call(
        "tracker.configureDelivery",
        {"project_id": project, "main_agent": {"host_key": "codex", "model": "gpt-6.1-sol"}},
    )
    assert saved.status_code == 200, saved.text
    arguments = {
        "project_id": project,
        "entry_kind": "managed_ui",
        "idempotency_key": "console-start",
        "response_mode": "raw",
    }
    first = call("projectSetup.start", arguments)
    assert first.status_code == 200, first.text
    data = first.json()["data"]
    assert data["claim_token"] is None
    assert data["attempt"]["requested_scope"] == "ready_to_use"
    assert data["attempt"]["managed"]["settings"]["model"] == "gpt-6.1-sol"
    repeated = call("projectSetup.start", {**arguments, "response_mode": "compact"})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["data"]["attempt"]["request_id"] == data["attempt"]["request_id"]
    changed = call("projectSetup.start", {**arguments, "mode": "update"})
    assert changed.status_code == 409, changed.text
    changed_scope = call(
        "projectSetup.start", {**arguments, "requested_scope": "infrastructure_only"}
    )
    assert changed_scope.status_code == 409, changed_scope.text
    assert len(scheduler.jobs) == 1
    foreign = call(
        "projectSetup.cancel",
        {"project_id": project + 1000, "request_id": data["attempt"]["request_id"]},
    )
    assert foreign.status_code == 404, foreign.text
    cancelled = call(
        "projectSetup.cancel", {"project_id": project, "request_id": data["attempt"]["request_id"]}
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"]["status"] == "cancelled"
    assert not scheduler.jobs
    read = call("projectSetup.get", {"project_id": project, "response_mode": "raw"})
    assert read.json()["current_attempt"]["status"] == "cancelled"
    assert read.json()["launch_preferences"] == {"host_key": "codex", "model": "gpt-6.1-sol"}


def test_console_answer_scope_replay_and_continuation(api, settings, tmp_path, monkeypatch):
    from sqlmodel import select

    from stackos.db.models import AgentRequest
    from tests.integration.test_repositories.test_setup_continuation import question_runner

    with Session(api.app.state.engine) as session:
        project = (
            WorkspaceRepository(session)
            .start_session(runtime="codex", cwd=str(tmp_path))
            .project_id
        )
        foreign = (
            ProjectRepository(session)
            .create(slug="foreign-answer", name="Foreign", domain="example.com")
            .data.id
        )
    scheduler = QueuedScheduler()
    api.app.state.operation_services["scheduler"] = scheduler
    monkeypatch.setattr(service, "inspect_host", _available_host)
    question_runner(monkeypatch)
    ui = {"Authorization": f"Bearer {derive_ui_token(settings.token_path.read_text().strip())}"}

    def call(name, arguments):
        return api.post(
            f"/api/v1/operations/projectSetup.{name}/call",
            json={"arguments": arguments},
            headers=ui,
        )

    started = call("start", {"project_id": project, "entry_kind": "managed_ui"})
    assert started.status_code == 200, started.text
    scheduler.run()
    pending = call("get", {"project_id": project, "response_mode": "raw"}).json()["current_attempt"]
    arguments = {
        "project_id": project,
        "request_id": pending["request_id"],
        "expected_revision": pending["revision"],
        "question_id": "goal",
        "question_revision": 1,
        "answer": "Maintain this project",
        "idempotency_key": "one-answer",
    }
    denied = call("answer", {**arguments, "project_id": foreign})
    assert denied.status_code == 404, denied.text
    assert (
        call(
            "continue",
            {
                key: value
                for key, value in arguments.items()
                if key in {"project_id", "request_id", "expected_revision"}
            },
        ).status_code
        == 409
    )
    result = call("answer", arguments)
    assert result.status_code == 200, result.text
    successor = result.json()["data"]["attempt"]
    assert successor["predecessor_request_id"] == pending["request_id"]
    assert "reports" not in successor and successor["answers"][0]["question_id"] == "goal"
    assert result.json()["data"]["claim_token"] is None
    assert call("answer", {**arguments, "answer": "Different intent"}).status_code == 409
    assert call("answer", {**arguments, "idempotency_key": "duplicate"}).status_code == 409
    replay = call("answer", {**arguments, "response_mode": "raw"})
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["attempt"]["request_id"] == successor["request_id"]
    scheduler.run()
    # Consumed-answer replay recovers its receipt, without another native admission.
    replay = call("answer", arguments)
    assert replay.status_code == 200 and replay.json()["data"]["attempt"]["status"] == "partial"
    with Session(api.app.state.engine) as session:
        assert len(session.exec(select(AgentRequest)).all()) == 2


@pytest.mark.parametrize("directory", ["workflows", "agent-presets", "skill-presets"])
@pytest.mark.parametrize("response_mode", ["compact", "raw"])
def test_source_parse_errors_keep_registered_read_and_reminder_responses_truthful(
    api, project_id, settings, tmp_path, directory, response_mode
):
    root = tmp_path / "source"
    root.mkdir()
    with Session(api.app.state.engine) as session:
        binding = (
            WorkspaceRepository(session)
            .connect(
                project_id=project_id, repo_fingerprint=f"path:{root}", last_known_root=str(root)
            )
            .data
        )
        ProjectRepository(session).update(project_id, primary_workspace_binding_id=binding.id)
        repo = ProjectSetupRepository(session)
        saved = repo.record_result(
            project_id,
            "engineering.tracked-delivery",
            expected_revision=0,
            result=verified_result(repo, project_id),
        )
    source = root / ".stackos" / directory / "partial.yaml"
    source.parent.mkdir(parents=True)
    source.write_text("presets: [synthetic-private-source-content\n")
    ui = {"Authorization": f"Bearer {derive_ui_token(settings.token_path.read_text().strip())}"}
    read = api.post(
        "/api/v1/operations/projectSetup.get/call",
        json={
            "arguments": {
                "project_id": project_id,
                "response_mode": response_mode,
            }
        },
        headers=ui,
    )
    assert read.status_code == 200, read.text
    payload = read.json() if response_mode == "raw" else read.json()["data"]
    workflow = payload["workflows"][0]
    assert workflow["status"] == "source_unavailable"
    assert workflow["revision"] == 1
    assert "projectSetup.get" in workflow["diagnostic"]
    assert "synthetic-private-source-content" not in workflow["diagnostic"]
    if response_mode == "raw":
        assert workflow["successful"] == saved.successful
    reminder = api.post(
        "/api/v1/operations/projectSetup.setReminders/call",
        json={
            "arguments": {
                "project_id": project_id,
                "dismiss_setup": True,
                "response_mode": response_mode,
            }
        },
        headers=ui,
    )
    assert reminder.status_code == 200, reminder.text
    assert reminder.json()["data"]["setup_reminder_dismissed_at"]
    assert reminder.json()["data"]["configured_count"] == 1
    assert reminder.json()["data"]["workflows"][0]["status"] == "source_unavailable"
