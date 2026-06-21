from __future__ import annotations

import json
from typing import Any

import stackos.cli.api_client as cli_api_client
from stackos.cli.api_client import _api_request, _format_daemon_api_error


def test_format_daemon_api_error_preserves_provider_error_data() -> None:
    rendered = _format_daemon_api_error(
        409,
        {
            "detail": "action connector failed",
            "code": -32008,
            "retryable": False,
            "data": {
                "status": "failed",
                "action_ref": "trackbooth.api.account_listaccounts",
                "provider_status_code": 429,
                "provider_error": {
                    "code": "agent_api_concurrency_limit_exceeded",
                    "message": "Agent API concurrency limit exceeded",
                    "retry_after_ms": 29988,
                    "operation_id": "AccountController.listAccounts",
                },
            },
        },
    )

    assert rendered.startswith("daemon API error 409: ")
    payload = json.loads(rendered.split(": ", 1)[1])
    assert payload["detail"] == "action connector failed"
    assert payload["data"]["provider_status_code"] == 429
    assert payload["data"]["provider_error"]["retry_after_ms"] == 29988


def test_api_request_marks_cli_client_surface(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: dict[str, Any] = {}

    class _Settings:
        host = "127.0.0.1"
        port = 5180

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"ok": true}'

    def fake_urlopen(request: Any, *, timeout: int) -> _Response:
        seen["timeout"] = timeout
        seen["surface"] = request.get_header("X-stackos-client-surface")
        return _Response()

    monkeypatch.setattr(cli_api_client, "get_settings", lambda: _Settings())
    monkeypatch.setattr(cli_api_client, "_read_daemon_token", lambda _settings: "daemon-token")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    assert _api_request("POST", "/api/v1/operations/action.run/call", body={"arguments": {}}) == {
        "ok": True
    }
    assert seen == {"timeout": 30, "surface": "cli"}
