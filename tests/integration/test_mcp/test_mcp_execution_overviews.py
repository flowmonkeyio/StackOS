"""Ticket overview scope and existing canonical project action audit adapters."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from stackos.db.models import ActionCall, ActionCallStatus, TrackerItemStatus
from stackos.repositories.projects import ProjectRepository
from tests.integration.test_repositories.test_execution_overviews import _call
from tests.integration.test_repositories.test_ticket_overviews import _ticket

from .conftest import MCPClient
from .test_mcp_bridge_agent_path import (
    _initialize,
    _operation_data,
    _scoped_bridge,
    _send,
    _structured,
    _toolbox_call,
)


def _seed(mcp: MCPClient, project_id: int) -> tuple[int, int]:
    with Session(mcp.test_client.app.state.engine) as session:
        at = datetime.now(UTC) - timedelta(minutes=1)
        actual = _call(
            session,
            project_id,
            at,
            request_json={"api_key": "synthetic-private-key"},
            response_json={"token": "synthetic-private-token"},
        )
        _call(session, project_id, at, dry_run=True)
        other = (
            ProjectRepository(session)
            .create(slug="other-audit", name="Other Audit", domain="other.test")
            .data
        )
        foreign = _call(session, other.id, at, status=ActionCallStatus.RUNNING)
        return actual.id, foreign.id


def _rest(mcp: MCPClient, operation: str, arguments: dict):
    return mcp.test_client.post(
        f"/api/v1/operations/{operation}/call",
        json={"arguments": {"response_mode": "raw", **arguments}},
        headers=mcp._headers(),
    )


def test_existing_project_audit_filters_redaction_and_ledger_defaults(
    mcp_client: MCPClient, seeded_project: dict
) -> None:
    project_id = seeded_project["data"]["id"]
    actual_id, foreign_id = _seed(mcp_client, project_id)
    window = {
        "created_from": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        "created_before": datetime.now(UTC).isoformat(),
    }
    page = _operation_data(
        _rest(
            mcp_client,
            "actionCall.query",
            {"project_id": project_id, "action_call_id": actual_id, **window},
        ).json()
    )
    assert page["items"][0]["request_json"]["api_key"] == "[redacted]"
    assert page["items"][0]["response_json"]["token"] == "[redacted]"
    assert "synthetic-private" not in str(page)
    scoped = _rest(
        mcp_client, "actionCall.query", {"project_id": project_id, "action_call_id": foreign_id}
    )
    assert _operation_data(scoped.json())["items"] == []
    legacy = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls", headers=mcp_client._headers()
    )
    assert legacy.json()["total_estimate"] == 2  # Unfiltered ledger keeps dry-run history.
    filtered = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={
            "dry_run": False,
            "provider_key": "sitemap",
            "action_call_id": actual_id,
            "created_from": window["created_from"],
            "created_before": window["created_before"],
        },
        headers=mcp_client._headers(),
    )
    assert filtered.json()["total_estimate"] == 1
    invalid = mcp_client.test_client.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={"created_from": "2026-01-02T00:00:00Z", "created_before": "2026-01-01T00:00:00Z"},
        headers=mcp_client._headers(),
    )
    assert invalid.status_code == 422
    assert "created_from" in invalid.text
    portfolio = _operation_data(
        _rest(mcp_client, "project.portfolio", {"project_id": project_id}).json()
    )
    assert portfolio["items"][0]["ticket_count"] == 0
    assert portfolio["items"][0]["last_activity_at"] is None
    assert "latest_action" not in portfolio["items"][0]
    assert "request_json" not in str(portfolio)
    with Session(mcp_client.test_client.app.state.engine) as session:
        assert len(session.exec(select(ActionCall)).all()) == 3


def test_scoped_bridge_ticket_counts_preserve_statuses_and_deny_global_reads(
    mcp_client: MCPClient, seeded_project: dict
) -> None:
    project_id = seeded_project["data"]["id"]
    actual_id, foreign_id = _seed(mcp_client, project_id)
    with Session(mcp_client.test_client.app.state.engine) as session:
        for status in TrackerItemStatus:
            _ticket(session, project_id, status.value, status=status)
    mcp_client.call_tool_structured(
        "workspace.connect",
        {
            "project_id": project_id,
            "repo_fingerprint": "path:execution-overview",
            "last_known_root": "/tmp/execution-overview",
        },
    )
    proxy, client = _scoped_bridge(
        mcp_client, cwd="/tmp/execution-overview", repo_fingerprint="path:execution-overview"
    )
    _initialize(proxy, client)
    _send(proxy, client, method="tools/list")
    result = _toolbox_call(proxy, client, "tracker.ticketCounts", {"response_mode": "compact"})
    assert not result["result"].get("isError"), result
    summary = _operation_data(_structured(result))
    assert summary["project_id"] == project_id
    assert summary["total_count"] == 7
    assert summary["is_active"] is None
    assert summary["ticket_counts"] == {status.value: 1 for status in TrackerItemStatus}
    assert summary["as_of"]
    assert "must-not-appear" not in str(summary)
    wrong_scope = _toolbox_call(proxy, client, "tracker.ticketCounts", {"project_id": 999999})
    assert wrong_scope["result"]["isError"] is True, wrong_scope
    own = _operation_data(
        _structured(
            _toolbox_call(
                proxy,
                client,
                "actionCall.query",
                {"action_call_id": actual_id, "response_mode": "compact"},
            )
        )
    )
    assert own["items"][0]["id"] == actual_id
    assert "request_json" not in own["items"][0]
    foreign = _operation_data(
        _structured(
            _toolbox_call(proxy, client, "actionCall.query", {"action_call_id": foreign_id})
        )
    )
    assert foreign["items"] == []
    for name in ("tracker.ticketCountsAll", "project.portfolio"):
        denied = _toolbox_call(proxy, client, name)
        assert denied["result"]["isError"] is True, denied
        assert "local-admin" in str(denied), denied


@pytest.mark.parametrize(
    "operation,arguments",
    [
        ("tracker.ticketCountsAll", {"is_active": "false"}),
        ("project.portfolio", {"ticket_status": "blocked"}),
        ("actionCall.query", {"dry_run": "false"}),
        ("actionCall.query", {"sort": "newest"}),
        ("actionCall.query", {"created_from": "2026-01-01T00:00:00"}),
        (
            "actionCall.query",
            {"created_from": "2026-01-02T00:00:00Z", "created_before": "2026-01-01T00:00:00Z"},
        ),
    ],
)
def test_overview_operations_reject_invalid_filters(
    mcp_client: MCPClient, seeded_project: dict, operation: str, arguments: dict
) -> None:
    if operation == "actionCall.query":
        arguments = {"project_id": seeded_project["data"]["id"], **arguments}
    response = _rest(mcp_client, operation, arguments)
    assert response.status_code in (400, 422), response.text


def test_chronological_order_is_explicit_across_mcp_operation_and_existing_get(
    mcp_client: MCPClient, seeded_project: dict
) -> None:
    project_id = seeded_project["data"]["id"]
    with Session(mcp_client.test_client.app.state.engine) as session:
        now = datetime.now(UTC)
        latest = _call(session, project_id, now)
        older = _call(session, project_id, now - timedelta(days=2))
        latest_id, older_id = latest.id, older.id
    args = {"project_id": project_id, "sort": "created_at", "limit": 1, "response_mode": "raw"}
    scoped = mcp_client.call_tool_structured("actionCall.query", args)
    assert _operation_data(scoped)["items"][0]["id"] == latest_id
    page = _operation_data(_rest(mcp_client, "actionCall.query", args).json())
    assert page["items"][0]["id"] == latest_id
    assert page["total_estimate"] == 2
    remaining = _operation_data(
        _rest(mcp_client, "actionCall.query", {**args, "after_id": page["next_cursor"]}).json()
    )
    assert remaining["items"][0]["id"] == older_id
    endpoint = f"/api/v1/projects/{project_id}/action-calls"
    default = mcp_client.test_client.get(endpoint, headers=mcp_client._headers()).json()
    assert [row["id"] for row in default["items"]] == [older_id, latest_id]
    chronological = mcp_client.test_client.get(
        endpoint, params={"sort": "created_at"}, headers=mcp_client._headers()
    ).json()
    assert [row["id"] for row in chronological["items"]] == [latest_id, older_id]
    invalid = mcp_client.test_client.get(
        endpoint, params={"sort": "newest"}, headers=mcp_client._headers()
    )
    assert invalid.status_code == 422


def test_local_admin_ticket_counts_match_contributing_project_directory(
    mcp_client: MCPClient, seeded_project: dict
) -> None:
    project_id = seeded_project["data"]["id"]
    with Session(mcp_client.test_client.app.state.engine) as session:
        projects = ProjectRepository(session)
        archived = projects.create(
            slug="archived-counts", name="Archived", domain="archive.test"
        ).data
        projects.update(archived.id, is_active=False)
        _ticket(session, project_id, "active-failed", status=TrackerItemStatus.FAILED)
        _ticket(session, archived.id, "archived-complete", status=TrackerItemStatus.COMPLETE)
        archived_id = archived.id
    for is_active, total, status in ((True, 1, "failed"), (False, 1, "complete"), (None, 2, None)):
        response = _rest(
            mcp_client,
            "tracker.ticketCountsAll",
            {"is_active": is_active, "response_mode": "compact"},
        )
        assert response.status_code == 200, response.text
        summary = _operation_data(response.json())
        assert summary["project_id"] is None
        assert summary["is_active"] is is_active
        assert summary["total_count"] == total
        assert len(summary["ticket_counts"]) == 7
        directory = _operation_data(
            _rest(
                mcp_client,
                "project.portfolio",
                {"is_active": is_active, "ticket_status": status, "response_mode": "compact"},
            ).json()
        )
        assert sum(row["ticket_count"] for row in directory["items"]) == total
        assert "context_json" not in str(summary)
    archived_counts = _operation_data(
        _rest(mcp_client, "tracker.ticketCounts", {"project_id": archived_id}).json()
    )
    assert archived_counts["total_count"] == 1
    filtered = _operation_data(
        _rest(
            mcp_client, "project.portfolio", {"is_active": True, "ticket_status": "complete"}
        ).json()
    )
    assert filtered["total_estimate"] == 0
    assert filtered["items"] == []
    matching = _operation_data(
        _rest(mcp_client, "tracker.get", {"project_id": project_id, "statuses": ["failed"]}).json()
    )
    assert len(matching["tickets"]) == 1
    missing = _rest(mcp_client, "tracker.ticketCounts", {"project_id": 999999})
    assert missing.status_code == 404
