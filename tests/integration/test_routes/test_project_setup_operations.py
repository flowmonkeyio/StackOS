"""Browser-safe reminder administration through the canonical REST adapter."""

from types import SimpleNamespace

import pytest
from sqlmodel import Session

from stackos.auth import derive_ui_token
from stackos.host_mcp import service
from stackos.repositories.project_setup import ProjectSetupRepository
from stackos.repositories.projects import ProjectRepository
from stackos.repositories.workspaces import WorkspaceRepository
from tests.integration.test_repositories.test_project_setup import verified_result
from tests.integration.test_repositories.test_task_delivery import QueuedScheduler


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
    monkeypatch.setattr(service, "inspect_host", lambda _: SimpleNamespace(available=True))
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
    assert data["attempt"]["managed"]["settings"]["model"] == "gpt-6.1-sol"
    repeated = call("projectSetup.start", {**arguments, "response_mode": "compact"})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["data"]["attempt"]["request_id"] == data["attempt"]["request_id"]
    changed = call("projectSetup.start", {**arguments, "mode": "update"})
    assert changed.status_code == 409, changed.text
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
