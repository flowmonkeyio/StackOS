"""Real registry, browser REST and bound bridge active-task contracts."""

from sqlmodel import Session

from tests.integration.test_repositories.test_active_tasks import seed_active_tasks

from .test_mcp_bridge_agent_path import (
    _initialize,
    _operation_data,
    _scoped_bridge,
    _structured,
    _toolbox_call,
)


def test_active_tasks_browser_contract_and_bound_bridge_scope(mcp_client, seeded_project):
    project_id = seeded_project["data"]["id"]
    with Session(mcp_client.test_client.app.state.engine) as session:
        _, other_id, _ = seed_active_tasks(session, project_id)
    api = mcp_client.test_client
    browser = api.get("/api/v1/auth/ui-token").json()["token"]
    headers = {"Authorization": f"Bearer {browser}"}
    for name, arguments, identities in (
        ("tracker.activeTasks", {"project_id": project_id}, {project_id}),
        ("tracker.activeTasksAll", {}, {project_id, other_id}),
    ):
        baseline = None
        for mode in ("raw", "compact"):
            result = api.post(
                f"/api/v1/operations/{name}/call",
                headers=headers,
                json={"arguments": {**arguments, "response_mode": mode}},
            )
            assert result.status_code == 200, result.text
            data = _operation_data(result.json())
            assert {item["project_id"] for item in data["items"]} == identities
            assert len(data["items"]) == 5 * len(identities)
            assert data.pop("as_of")
            assert data["next_cursor"] is None
            assert "PRIVATE" not in result.text
            baseline = data if baseline is None else baseline
            assert data == baseline
        for invalid in ({"response_mode": "ack"}, {"limit": 201}, {"after_id": 0}):
            result = api.post(
                f"/api/v1/operations/{name}/call",
                headers=headers,
                json={"arguments": {**arguments, **invalid}},
            )
            assert result.status_code == 422, result.text
    # The console-only global operation cannot be widened with project_id.
    result = api.post(
        "/api/v1/operations/tracker.activeTasksAll/call",
        headers=headers,
        json={"arguments": {"project_id": project_id}},
    )
    assert result.status_code == 422
    mcp_client.call_tool_structured(
        "workspace.connect",
        {
            "project_id": project_id,
            "repo_fingerprint": "path:home-active",
            "last_known_root": "/tmp/home-active",
        },
    )
    proxy, client = _scoped_bridge(
        mcp_client, cwd="/tmp/home-active", repo_fingerprint="path:home-active"
    )
    _initialize(proxy, client)
    own = _toolbox_call(proxy, client, "tracker.activeTasks", {"response_mode": "compact"})
    assert not own["result"].get("isError"), own
    assert {item["project_id"] for item in _operation_data(_structured(own))["items"]} == {
        project_id
    }
    foreign = _toolbox_call(proxy, client, "tracker.activeTasks", {"project_id": other_id})
    assert foreign["result"]["isError"] is True
    denied = _toolbox_call(proxy, client, "tracker.activeTasksAll")
    assert denied["result"]["isError"] is True
