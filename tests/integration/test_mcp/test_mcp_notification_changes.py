"""The new projection retains the real bound bridge's project fence."""

from sqlmodel import Session, select

from stackos.db.models import TrackerRevision
from stackos.repositories.tracker import TrackerRepository

from .test_mcp_bridge_agent_path import (
    _initialize,
    _operation_data,
    _scoped_bridge,
    _structured,
    _toolbox_call,
)


def test_notification_projection_bound_owner_and_foreign_reject(mcp_client, seeded_project):
    own_id = seeded_project["data"]["id"]
    foreign = mcp_client.call_tool_structured(
        "project.create",
        {
            "slug": "notification-foreign",
            "name": "Foreign",
            "domain": "foreign.example",
        },
    )
    other_id = foreign["data"]["id"]
    with Session(mcp_client.test_client.app.state.engine) as session:
        repo = TrackerRepository(session)
        repo.create_task(project_id=own_id, key="work", title="Own")
        repo.create_task(project_id=other_id, key="work", title="Foreign")
        before = len(session.exec(select(TrackerRevision)).all())
    mcp_client.call_tool_structured(
        "workspace.connect",
        {
            "project_id": own_id,
            "repo_fingerprint": "path:notification-fixture",
            "last_known_root": "/tmp/notification-fixture",
        },
    )
    proxy, client = _scoped_bridge(
        mcp_client, cwd="/tmp/notification-fixture", repo_fingerprint="path:notification-fixture"
    )
    _initialize(proxy, client)
    own = _toolbox_call(
        proxy,
        client,
        "tracker.changed",
        {
            "projection": "notifications",
            "since_rev": 0,
            "response_mode": "raw",
        },
    )
    assert not own["result"].get("isError"), own
    data = _operation_data(_structured(own))
    assert data["project_id"] == own_id
    assert data["changes"][0]["task_title"] == "Own"
    denied = _toolbox_call(
        proxy,
        client,
        "tracker.changed",
        {
            "project_id": other_id,
            "projection": "notifications",
            "since_rev": 0,
        },
    )
    assert denied["result"]["isError"] is True
    assert "Foreign" not in str(denied)
    with Session(mcp_client.test_client.app.state.engine) as session:
        assert len(session.exec(select(TrackerRevision)).all()) == before
