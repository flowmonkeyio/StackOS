"""Synthetic Messages envelopes for shared native-runner contract tests.

The values supplied by each test remain explicit. Current captured wire samples
live in tests/fixtures/claude_grok_streams_20261122.json.
"""

import json


def messages_output(host, value):
    if host not in {"claude-code", "grok"}:
        return json.dumps(value)
    terminal = dict(value)
    if host == "grok":
        terminal["session_id"] = terminal.pop("sessionId", None)
        terminal["stop_reason"] = terminal.pop("stopReason", None)
        terminal["result"] = terminal.pop("text", None)
        if terminal.get("error"):
            terminal["is_error"] = True
            terminal["errors"] = [terminal["error"]]
    terminal["type"] = "result"
    terminal.setdefault("subtype", "success")
    return "\n".join(
        map(
            json.dumps,
            (
                {"type": "system", "subtype": "init", "session_id": terminal.get("session_id")},
                terminal,
            ),
        )
    )
