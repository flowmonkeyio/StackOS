from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

import content_stack.cli as cli_module
from content_stack.cli import app


def test_cli_ops_list_prints_registered_operations(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def fake_api_request(method: str, path: str, **_kwargs: object) -> dict[str, Any]:
        assert method == "GET"
        assert path == "/api/v1/operations"
        return {
            "items": [
                {
                    "name": "action.describe",
                    "summary": "Describe an action.",
                    "surfaces": {
                        "mcp": {"enabled": True},
                        "rest": {"enabled": True},
                        "cli": {"enabled": True},
                    },
                }
            ]
        }

    monkeypatch.setattr(cli_module, "_api_request", fake_api_request)

    result = CliRunner().invoke(app, ["ops", "list"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "action.describe" in result.stdout
    assert "mcp,rest,cli" in result.stdout


def test_cli_ops_describe_json(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def fake_api_request(method: str, path: str, **_kwargs: object) -> dict[str, Any]:
        assert method == "GET"
        assert path == "/api/v1/operations/action.execute"
        return {
            "name": "action.execute",
            "summary": "Execute an action.",
            "purpose": "Run one action.",
            "prerequisites": ["run_token"],
            "examples": [],
        }

    monkeypatch.setattr(cli_module, "_api_request", fake_api_request)

    result = CliRunner().invoke(
        app,
        ["ops", "describe", "action.execute", "--json"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["name"] == "action.execute"


def test_cli_ops_call_merges_common_arguments(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_api_request(
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        **_kwargs: object,
    ) -> dict[str, Any]:
        calls.append((method, path, body))
        return {"ok": True, "arguments": body["arguments"] if body else {}}

    input_path = tmp_path / "input.json"
    input_path.write_text(
        json.dumps(
            {
                "action_ref": "utils.sitemap.fetch",
                "input_json": {"urls": ["https://example.com/sitemap.xml"]},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_module, "_api_request", fake_api_request)

    result = CliRunner().invoke(
        app,
        [
            "ops",
            "call",
            "action.execute",
            "--input",
            str(input_path),
            "--project",
            "7",
            "--run-token",
            "run-token",
            "--idempotency-key",
            "idem-1",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert calls == [
        (
            "POST",
            "/api/v1/operations/action.execute/call",
            {
                "arguments": {
                    "action_ref": "utils.sitemap.fetch",
                    "input_json": {"urls": ["https://example.com/sitemap.xml"]},
                    "project_id": 7,
                    "run_token": "run-token",
                    "idempotency_key": "idem-1",
                }
            },
        )
    ]
    assert json.loads(result.stdout)["ok"] is True


def test_cli_ops_call_forwards_communication_bot_profile_setup(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_api_request(
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        **_kwargs: object,
    ) -> dict[str, Any]:
        calls.append((method, path, body))
        return {"data": {"key": body["arguments"]["key"] if body else None}}

    input_path = tmp_path / "telegram-profile.json"
    input_path.write_text(
        json.dumps(
            {
                "key": "support-bot",
                "auth_profile_key": "support",
                "identity": {
                    "display_name": "Support Bot",
                    "purpose": "Handle support requests from approved Telegram users.",
                    "voice": "Concise and calm.",
                },
                "access_policy": {
                    "dm_mode": "allowlist",
                    "group_mode": "allowlist",
                    "user_mode": "allowlist",
                    "allowed_chat_refs": ["telegram-chat:999"],
                    "allowed_user_refs": ["telegram-user:555"],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_module, "_api_request", fake_api_request)

    result = CliRunner().invoke(
        app,
        [
            "ops",
            "call",
            "communicationBotProfile.upsert",
            "--input",
            str(input_path),
            "--project",
            "7",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert calls == [
        (
            "POST",
            "/api/v1/operations/communicationBotProfile.upsert/call",
            {
                "arguments": {
                    "key": "support-bot",
                    "auth_profile_key": "support",
                    "identity": {
                        "display_name": "Support Bot",
                        "purpose": "Handle support requests from approved Telegram users.",
                        "voice": "Concise and calm.",
                    },
                    "access_policy": {
                        "dm_mode": "allowlist",
                        "group_mode": "allowlist",
                        "user_mode": "allowlist",
                        "allowed_chat_refs": ["telegram-chat:999"],
                        "allowed_user_refs": ["telegram-user:555"],
                    },
                    "project_id": 7,
                }
            },
        )
    ]
    assert json.loads(result.stdout)["data"]["key"] == "support-bot"


def test_cli_actions_describe_alias_calls_operation(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_api_request(
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        **_kwargs: object,
    ) -> dict[str, Any]:
        calls.append((method, path, body))
        return {"manifest": {"action_ref": body["arguments"]["action_ref"] if body else None}}

    monkeypatch.setattr(cli_module, "_api_request", fake_api_request)

    result = CliRunner().invoke(
        app,
        ["actions", "describe", "utils.sitemap.fetch", "--project", "7"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert calls == [
        (
            "POST",
            "/api/v1/operations/action.describe/call",
            {"arguments": {"action_ref": "utils.sitemap.fetch", "project_id": 7}},
        )
    ]
    assert json.loads(result.stdout)["manifest"]["action_ref"] == "utils.sitemap.fetch"


def test_cli_run_plans_create_alias_calls_operation(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_api_request(
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        **_kwargs: object,
    ) -> dict[str, Any]:
        calls.append((method, path, body))
        return {"data": {"id": 9}}

    input_path = tmp_path / "plan.json"
    input_path.write_text(
        json.dumps(
            {
                "schema_version": "stackos.run-plan.v1",
                "key": "manual.review.run",
                "title": "Manual review",
                "steps": [{"id": "review", "title": "Review"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_module, "_api_request", fake_api_request)

    result = CliRunner().invoke(
        app,
        [
            "run-plans",
            "create",
            "--project",
            "7",
            "--input",
            str(input_path),
            "--created-by",
            "cli",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert calls[0][0] == "POST"
    assert calls[0][1] == "/api/v1/operations/runPlan.create/call"
    assert calls[0][2] is not None
    arguments = calls[0][2]["arguments"]
    assert arguments["project_id"] == 7
    assert arguments["created_by"] == "cli"
    assert arguments["run_plan_json"]["key"] == "manual.review.run"
    assert json.loads(result.stdout)["data"]["id"] == 9


def test_cli_run_plans_record_step_alias_merges_result(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_api_request(
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        **_kwargs: object,
    ) -> dict[str, Any]:
        calls.append((method, path, body))
        return {"data": {"status": "completed"}}

    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({"summary": "done"}), encoding="utf-8")
    monkeypatch.setattr(cli_module, "_api_request", fake_api_request)

    result = CliRunner().invoke(
        app,
        [
            "run-plans",
            "record-step",
            "9",
            "--step-id",
            "review",
            "--status",
            "success",
            "--result",
            str(result_path),
            "--run-token",
            "token",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert calls == [
        (
            "POST",
            "/api/v1/operations/runPlan.recordStep/call",
            {
                "arguments": {
                    "run_plan_id": 9,
                    "step_id": "review",
                    "status": "success",
                    "result_json": {"summary": "done"},
                    "run_token": "token",
                }
            },
        )
    ]
    assert json.loads(result.stdout)["data"]["status"] == "completed"


def test_cli_agent_requests_claim_alias_calls_operation(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_api_request(
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        **_kwargs: object,
    ) -> dict[str, Any]:
        calls.append((method, path, body))
        return {"data": {"id": body["arguments"]["request_id"], "claim_token": "claim-token"}}

    monkeypatch.setattr(cli_module, "_api_request", fake_api_request)

    result = CliRunner().invoke(
        app,
        [
            "agent-requests",
            "claim",
            "42",
            "--project",
            "7",
            "--claimed-by",
            "codex",
            "--idempotency-key",
            "claim-agent-request-42",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert calls == [
        (
            "POST",
            "/api/v1/operations/agentRequest.claim/call",
            {
                "arguments": {
                    "request_id": 42,
                    "claimed_by": "codex",
                    "lease_seconds": 600,
                    "project_id": 7,
                    "idempotency_key": "claim-agent-request-42",
                }
            },
        )
    ]
    assert json.loads(result.stdout)["data"]["claim_token"] == "claim-token"


def test_cli_agent_requests_prepare_run_plan_alias_calls_operation(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_api_request(
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        **_kwargs: object,
    ) -> dict[str, Any]:
        calls.append((method, path, body))
        return {"data": {"claim_token": "claim-token"}}

    input_path = tmp_path / "plan.json"
    input_path.write_text(
        json.dumps(
            {
                "schema_version": "stackos.run-plan.v1",
                "key": "handle.request.run",
                "title": "Handle request",
                "steps": [{"id": "handle", "title": "Handle request"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_module, "_api_request", fake_api_request)

    result = CliRunner().invoke(
        app,
        [
            "agent-requests",
            "prepare-run-plan",
            "42",
            "--project",
            "7",
            "--claimed-by",
            "codex",
            "--idempotency-key",
            "prepare-42",
            "--input",
            str(input_path),
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert calls == [
        (
            "POST",
            "/api/v1/operations/agentRequest.prepareRunPlan/call",
            {
                "arguments": {
                    "request_id": 42,
                    "claimed_by": "codex",
                    "lease_seconds": 86_400,
                    "run_plan_json": {
                        "schema_version": "stackos.run-plan.v1",
                        "key": "handle.request.run",
                        "title": "Handle request",
                        "steps": [{"id": "handle", "title": "Handle request"}],
                    },
                    "project_id": 7,
                    "idempotency_key": "prepare-42",
                }
            },
        )
    ]
    assert json.loads(result.stdout)["data"]["claim_token"] == "claim-token"
