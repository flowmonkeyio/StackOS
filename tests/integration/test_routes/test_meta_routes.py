"""Tests for ``/api/v1/meta/enums``."""

from __future__ import annotations

from fastapi.testclient import TestClient

from stackos.db.models import (
    RUN_PLAN_STATUS_TRANSITIONS,
    RUN_STATUS_TRANSITIONS,
    RunKind,
    RunStatus,
)


def test_meta_enums_returns_core_payload(api: TestClient) -> None:
    resp = api.get("/api/v1/meta/enums")

    assert resp.status_code == 200
    body = resp.json()
    assert {
        "runs_status",
        "runs_kind",
        "run_steps_status",
        "run_plans_status",
        "run_plan_steps_status",
        "approval_requests_status",
        "action_calls_status",
        "plugins_source",
        "allowed_transitions",
    } <= set(body)


def test_meta_enums_run_values_match_models(api: TestClient) -> None:
    body = api.get("/api/v1/meta/enums").json()

    assert body["runs_status"] == [member.value for member in RunStatus]
    assert body["runs_kind"] == [member.value for member in RunKind]


def test_allowed_transitions_match_core_maps(api: TestClient) -> None:
    body = api.get("/api/v1/meta/enums").json()

    expected_runs = {
        key.value: sorted(value.value for value in values)
        for key, values in RUN_STATUS_TRANSITIONS.items()
    }
    expected_run_plans = {
        key.value: sorted(value.value for value in values)
        for key, values in RUN_PLAN_STATUS_TRANSITIONS.items()
    }
    assert body["allowed_transitions"]["runs"] == expected_runs
    assert body["allowed_transitions"]["run_plans"] == expected_run_plans
