"""Observable application contracts affected by the Python dependency refresh."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient


def test_query_defaults_and_partial_project_updates_remain_distinct(
    client: TestClient, auth_token: str
) -> None:
    client.headers.update({"Authorization": f"Bearer {auth_token}"})
    created = client.post(
        "/api/v1/projects",
        json={
            "slug": "dependency-defaults",
            "name": "Dependency defaults",
            "domain": "dependency-defaults.example",
            "niche": "synthetic fixture",
            "locale": "fr-FR",
        },
    )
    assert created.status_code == 201
    project = created.json()["data"]
    path = f"/api/v1/projects/{project['id']}"

    changed = client.patch(path, json={"name": "Updated name"})
    assert changed.status_code == 200
    assert changed.json()["data"]["name"] == "Updated name"
    for key in ("domain", "niche", "locale"):
        assert changed.json()["data"][key] == project[key]
    cleared = client.patch(path, json={"niche": None})
    assert cleared.status_code == 200
    assert cleared.json()["data"]["niche"] is None
    assert cleared.json()["data"]["locale"] == "fr-FR"

    deleted = client.delete(path)
    assert deleted.status_code == 200
    assert deleted.json()["data"]["is_active"] is False
    assert client.get(path).status_code == 200  # Omitted hard=False remains a soft delete.
    for params in ({}, {"active_only": "false"}):
        response = client.get("/api/v1/projects", params=params)
        assert response.status_code == 200
        assert [row["id"] for row in response.json()["items"]] == [project["id"]]
    active = client.get("/api/v1/projects", params={"active_only": "true"})
    assert active.status_code == 200
    assert active.json()["items"] == []
    assert client.get("/api/v1/projects", params={"active_only": "invalid"}).status_code == 422
    assert client.get("/api/v1/projects", params={"limit": "0"}).status_code == 422


def test_default_app_does_not_activate_telemetry_exporters(tmp_path: Path) -> None:
    """An ambient OTLP destination alone must not opt this local app into export."""
    # A subprocess starts without providers configured by another test. Block outbound
    # connects and exporter construction before importing the application; TestClient
    # uses an in-process transport and needs neither.
    script = r"""
import socket
import sys
from pathlib import Path
from unittest.mock import patch

attempts = []

def unexpected(*args, **kwargs):
    attempts.append("outbound/exporter")
    raise AssertionError("default StackOS startup must not export telemetry")

from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

with (
    patch.object(socket.socket, "connect", unexpected),
    patch.object(socket.socket, "connect_ex", unexpected),
    patch.object(OTLPLogExporter, "__init__", unexpected),
    patch.object(OTLPMetricExporter, "__init__", unexpected),
    patch.object(OTLPSpanExporter, "__init__", unexpected),
):
    from fastapi.testclient import TestClient
    from stackos.config import Settings
    from stackos.server import create_app

    root = Path(sys.argv[1])
    settings = Settings(data_dir=root / "data", state_dir=root / "state")
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:5180") as client:
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/projects").status_code == 401
    assert attempts == [], attempts
"""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("OTEL_", "FASTAPI_OTEL_"))
    }
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://127.0.0.1:9"
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
