"""Browser-safe reminder administration through the canonical REST adapter."""

from sqlmodel import Session

from stackos.auth import derive_ui_token
from stackos.repositories.projects import ProjectRepository


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
