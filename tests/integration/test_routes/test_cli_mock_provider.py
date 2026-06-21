from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from typer.testing import CliRunner

import stackos.cli.operation_commands as operation_cli
from stackos.cli import app


def _mock_action_plan_json() -> dict[str, Any]:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "utils.mock-provider.run",
        "title": "Mock provider action",
        "grants": {
            "mcp_tool_grants": [
                {
                    "step_id": "execute-mock",
                    "tool": "action.execute",
                    "action_refs": ["utils.mock.echo"],
                }
            ]
        },
        "steps": [
            {
                "id": "execute-mock",
                "title": "Execute mock provider",
                "action_refs": ["utils.mock.echo"],
            }
        ],
    }


def _store_mock_credential(api: TestClient, project_id: int) -> str:
    credential = api.post(
        f"/api/v1/projects/{project_id}/auth/mock-provider/credentials",
        json={
            "auth_method_key": "api_key",
            "profile_key": "primary",
            "label": "Mock CLI Primary",
            "fields": {"api_key": "mock-cli-secret"},
        },
    )
    assert credential.status_code == 201, credential.text
    return str(credential.json()["data"]["credential_ref"])


def _patch_cli_to_test_client(
    monkeypatch: Any,
    api: TestClient,
) -> None:
    def fake_api_request(
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        **_kwargs: object,
    ) -> Any:
        response = api.request(
            method,
            path,
            json=body,
            headers={"X-StackOS-Client-Surface": "cli"},
        )
        assert response.status_code < 400, response.text
        return response.json() if response.content else None

    monkeypatch.setattr(operation_cli, "_api_request", fake_api_request)


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run_cli(runner: CliRunner, args: list[str]) -> dict[str, Any]:
    result = runner.invoke(app, args, catch_exceptions=False)
    assert result.exit_code == 0, result.stdout
    return json.loads(result.stdout)


def test_cli_mock_provider_vertical_slice_uses_shared_operation_registry(
    api: TestClient,
    project_id: int,
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    _patch_cli_to_test_client(monkeypatch, api)
    credential_ref = _store_mock_credential(api, project_id)
    runner = CliRunner()

    created = _run_cli(
        runner,
        [
            "run-plans",
            "create",
            "--project",
            str(project_id),
            "--input",
            str(_write_json(tmp_path / "plan.json", _mock_action_plan_json())),
            "--created-by",
            "cli-test",
        ],
    )
    run_plan_id = created["data"]["id"]
    started = _run_cli(
        runner,
        ["run-plans", "start", str(run_plan_id), "--project", str(project_id)],
    )
    run_token = started["data"]["run_token"]
    claimed = _run_cli(
        runner,
        [
            "run-plans",
            "claim-step",
            str(run_plan_id),
            "--step-id",
            "execute-mock",
            "--run-token",
            run_token,
            "--claimed-by",
            "cli-test",
        ],
    )
    executed = _run_cli(
        runner,
        [
            "actions",
            "execute",
            "utils.mock.echo",
            "--project",
            str(project_id),
            "--run-token",
            run_token,
            "--credential-ref",
            credential_ref,
            "--input",
            str(
                _write_json(
                    tmp_path / "action.json",
                    {
                        "message": "hello from cli mock provider",
                        "echo": {"campaign": "mock-campaign"},
                        "cost_cents": 13,
                    },
                )
            ),
        ],
    )

    data = executed["data"]
    rendered = json.dumps(data)
    assert data["action_call"]["run_id"] == started["data"]["run_id"]
    assert data["action_call"]["run_plan_id"] == run_plan_id
    assert data["action_call"]["run_plan_step_id"] == claimed["data"]["id"]
    assert data["action_call"]["provider_key"] == "mock-provider"
    assert data["action_call"]["connector_key"] == "mock-provider"
    assert data["output_json"]["status"] == "success"
    assert data["output_json"]["credential_ref"] == credential_ref
    assert data["output_json"]["leak_check"] == {
        "authorization": "[redacted]",
        "api_key": "[redacted]",
    }
    assert data["metadata_json"]["access_token"] == "[redacted]"
    assert data["cost_cents"] == 13
    assert "mock-cli-secret" not in rendered

    audit_resp = api.get(
        f"/api/v1/projects/{project_id}/action-calls",
        params={
            "run_id": started["data"]["run_id"],
            "run_plan_id": run_plan_id,
            "run_plan_step_id": claimed["data"]["id"],
            "status": "success",
        },
    )
    assert audit_resp.status_code == 200
    audit = audit_resp.json()
    assert audit["total_estimate"] == 1
    assert "mock-cli-secret" not in json.dumps(audit)
