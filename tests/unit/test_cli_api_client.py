from __future__ import annotations

import json

from stackos.cli.api_client import _format_daemon_api_error


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
