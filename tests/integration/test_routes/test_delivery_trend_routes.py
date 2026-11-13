"""Real operation envelopes retain all six trends and scope boundaries."""

import pytest
from sqlmodel import Session

from tests.integration.test_repositories.test_delivery_trends import FILTERS, ledger
from tests.integration.test_routes.test_tracker_operations_routes import _operation_data


@pytest.mark.parametrize(
    "name,global_scope", [("tracker.agentStats", False), ("tracker.agentStatsAll", True)]
)
def test_six_trends_survive_real_browser_read_envelopes(api, project_id, name, global_scope):
    with Session(api.app.state.engine) as session:
        ledger(session, project_id)
    token = api.get("/api/v1/auth/ui-token").json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    filters = {
        key: value.isoformat() if hasattr(value, "isoformat") else value
        for key, value in FILTERS.items()
    }
    args = {**filters, **({"is_active": None} if global_scope else {"project_id": project_id})}
    previous = None
    for mode in ("raw", "compact"):
        response = api.post(
            f"/api/v1/operations/{name}/call",
            headers=headers,
            json={"arguments": {**args, "response_mode": mode}},
        )
        assert response.status_code == 200, response.text
        data = _operation_data(response)
        trends = data["delivery_trends"]
        assert set(trends["totals"]) == {
            "completed_tasks",
            "completed_tickets",
            "first_review_acceptance",
            "changes_requested",
            "stops",
            "work_time",
        }
        assert trends["totals"]["work_time"]["total_seconds"] == 10800
        assert trends["totals"]["first_review_acceptance"]["rate"] == 0.5
        assert len(trends["daily"]) == len(data["daily"]) == 2
        assert "PRIVATE" not in response.text
        assert previous is None or previous == trends
        previous = trends
    denied = api.post(
        f"/api/v1/operations/{name}/call",
        headers=headers,
        json={"arguments": {**args, "response_mode": "ack"}},
    )
    assert denied.status_code == 422
