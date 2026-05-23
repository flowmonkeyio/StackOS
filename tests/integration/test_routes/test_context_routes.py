"""REST tests for StackOS project memory primitives."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlmodel import Session

from stackos.db.models import MetricSnapshot


def test_project_memory_routes_create_and_query_sanitized_context(
    api: TestClient,
    project_id: int,
) -> None:
    learning = api.post(
        f"/api/v1/projects/{project_id}/learnings",
        json={
            "statement": "Short hooks won with api_key=secret",
            "domain": "media-buying",
            "confidence": "medium",
            "review_state": "accepted",
            "tags": ["creative"],
            "evidence_json": {"access_token": "tok"},
        },
    )
    assert learning.status_code == 201, learning.text

    queried = api.post(
        f"/api/v1/projects/{project_id}/context/query",
        json={
            "sources": ["learnings"],
            "fields": ["statement", "confidence", "evidence_json"],
            "tags": ["creative"],
            "limit": 5,
        },
    )
    assert queried.status_code == 200, queried.text
    body = queried.json()

    assert body["items"][0]["fields"]["statement"] == "Short hooks won with api_key=[redacted]"
    assert body["items"][0]["fields"]["evidence_json"] == {"access_token": "[redacted]"}
    assert "secret" not in json.dumps(body)


def test_experiment_and_decision_routes_store_supplied_data_without_winner_logic(
    api: TestClient,
    project_id: int,
) -> None:
    exp_resp = api.post(
        f"/api/v1/projects/{project_id}/experiments",
        json={
            "key": "title-test",
            "name": "Title Test",
            "domain": "seo",
            "hypothesis": "Question title improves CTR.",
            "status": "running",
            "variants": [{"key": "question"}, {"key": "statement"}],
        },
    )
    assert exp_resp.status_code == 201, exp_resp.text
    experiment_id = exp_resp.json()["data"]["id"]

    obs_resp = api.post(
        f"/api/v1/projects/{project_id}/experiments/observations",
        json={
            "experiment_id": experiment_id,
            "variant_key": "question",
            "metrics_json": {"ctr": 0.12, "api_key": "secret"},
            "summary": "Early observation only.",
        },
    )
    assert obs_resp.status_code == 200, obs_resp.text

    observations = api.get(
        f"/api/v1/projects/{project_id}/experiments/observations",
        params={"experiment_id": experiment_id},
    )
    assert observations.status_code == 200
    assert observations.json()["items"][0]["metrics_json"]["api_key"] == "[redacted]"

    decision_resp = api.post(
        f"/api/v1/projects/{project_id}/experiments/decisions",
        json={
            "experiment_id": experiment_id,
            "decision": "Keep running until the seven-day window completes.",
            "experiment_status": "running",
        },
    )
    assert decision_resp.status_code == 200, decision_resp.text

    experiments = api.get(
        f"/api/v1/projects/{project_id}/experiments",
        params={"status": "running"},
    )
    decisions = api.get(f"/api/v1/projects/{project_id}/decisions")

    assert [item["id"] for item in experiments.json()["items"]] == [experiment_id]
    assert decisions.json()["items"][0]["decision"].startswith("Keep running")


def test_context_snapshot_and_metric_read_routes_are_sanitized(
    api: TestClient,
    project_id: int,
) -> None:
    snapshot = api.post(
        f"/api/v1/projects/{project_id}/context/snapshots",
        json={
            "name": "Recent history",
            "query_json": {"sources": ["runs"], "access_token": "secret"},
            "selected_sources_json": [{"source": "runs", "ids": [1]}],
        },
    )
    assert snapshot.status_code == 201, snapshot.text

    snapshots = api.get(f"/api/v1/projects/{project_id}/context/snapshots")
    assert snapshots.status_code == 200
    assert snapshots.json()["items"][0]["query_json"]["access_token"] == "[redacted]"

    engine = api.app.state.engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        session.add(
            MetricSnapshot(
                project_id=project_id,
                source_type="experiment",
                source_id=123,
                metric_key="ctr",
                metric_value=0.42,
                metadata_json={"api_key": "secret"},
            )
        )
        session.commit()

    metrics = api.get(f"/api/v1/projects/{project_id}/metrics", params={"metric_key": "ctr"})
    assert metrics.status_code == 200
    assert metrics.json()["items"][0]["metadata_json"]["api_key"] == "[redacted]"


def test_context_query_route_enforces_limit_and_field_projection(
    api: TestClient,
    project_id: int,
) -> None:
    bad_limit = api.post(
        f"/api/v1/projects/{project_id}/context/query",
        json={"sources": ["learnings"], "limit": 51},
    )
    assert bad_limit.status_code == 422

    bad_field = api.post(
        f"/api/v1/projects/{project_id}/context/query",
        json={"sources": ["learnings"], "fields": ["statement", "secret_payload"]},
    )
    assert bad_field.status_code == 422
