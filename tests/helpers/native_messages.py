"""Synthetic Messages envelopes for shared native-runner contract tests.

The values supplied by each test remain explicit. Current captured wire samples
live in tests/fixtures/claude_grok_streams_20261122.json.
"""

import json
from pathlib import Path


def messages_output(host, value, command=None):
    if host == "hermes":
        # Synthetic -z stdout plus the separate v0.23.3 usage-file report.
        if command is not None:
            Path(command[command.index("--usage-file") + 1]).write_text(
                json.dumps({key: item for key, item in value.items() if key != "text"})
            )
        return value.get("text", json.dumps(value))
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
