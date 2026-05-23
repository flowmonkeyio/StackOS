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
