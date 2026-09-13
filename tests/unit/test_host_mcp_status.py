"""Host status reads do not repair registrations or rewrite restart markers."""

import asyncio
import json
from pathlib import Path
from threading import get_ident

import pytest

from stackos.host_mcp import restart_state, service
from stackos.host_mcp.adapters import claude_desktop
from stackos.operations.system import HostMcpStatusInput, host_mcp_status


@pytest.mark.parametrize("state", ["pending", "closed", "restarted", "expired"])
def test_claude_desktop_status_preserves_config_and_restart_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    config = tmp_path / "claude_desktop_config.json"
    command = ["stackos", "mcp-bridge", "--runtime", "claude-desktop"]
    config.write_text(
        json.dumps({"mcpServers": {"stackos": {"command": command[0], "args": command[1:]}}})
    )
    monkeypatch.delenv("STACKOS_STATE_DIR", raising=False)
    marker = restart_state.state_path(tmp_path)
    marker.parent.mkdir(parents=True)
    marker.write_text(
        json.dumps(
            {
                "claude-desktop": {
                    "config_path": str(config),
                    "command": command,
                    "marked_at": 1 if state == "expired" else 10_000,
                }
            }
        )
    )
    original = [(path.read_bytes(), path.stat().st_mtime_ns) for path in (config, marker)]
    monkeypatch.setattr(restart_state.time, "time", lambda: 10_100)
    monkeypatch.setattr(claude_desktop, "config_path", lambda _home: config)
    monkeypatch.setattr(claude_desktop, "_available", lambda _path: True)
    monkeypatch.setattr(claude_desktop, "resolve_bridge_command", lambda **_kwargs: command)
    monkeypatch.setattr(
        claude_desktop,
        "_claude_desktop_running",
        lambda: "not_running" if state == "closed" else "running",
    )
    monkeypatch.setattr(
        claude_desktop, "_claude_desktop_started_after", lambda _at: state == "restarted"
    )

    result = claude_desktop.inspect(tmp_path)

    assert result.status == ("restart_required" if state == "pending" else "registered_current")
    assert result.needs_restart is (state == "pending")
    assert [(path.read_bytes(), path.stat().st_mtime_ns) for path in (config, marker)] == original


def test_host_inspection_does_not_return_raw_exception_material(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BrokenAdapter:
        HOST_KEY = "codex"

        @staticmethod
        def inspect(_home: Path):
            raise RuntimeError("private-config-fragment api_key=synthetic-secret")

    monkeypatch.setattr(
        service,
        "_definitions",
        lambda: [
            service.HostMcpDefinition(
                adapter=BrokenAdapter,
                policy=service.HostMcpPolicy("codex", "ChatGPT / Codex", "automatic"),
            )
        ],
    )
    result = service.inspect_all(home=tmp_path)
    assert result.ok is False
    assert result.results[0].status == "register_failed"
    assert "RuntimeError" in result.results[0].message
    assert "private-config-fragment" not in str(result.to_info())
    assert "synthetic-secret" not in str(result.to_info())


def test_host_status_offloads_inspection_without_context_or_filesystem_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller_thread = get_ident()
    inspected_threads: list[int] = []

    def inspect():
        inspected_threads.append(get_ident())
        return service.HostMcpAggregate(ok=True, results=[])

    monkeypatch.setattr(service, "inspect_all", inspect)
    result = asyncio.run(host_mcp_status(HostMcpStatusInput(), None, None))
    assert result.model_dump() == {"ok": True, "items": []}
    assert len(inspected_threads) == 1
    assert inspected_threads[0] != caller_thread
