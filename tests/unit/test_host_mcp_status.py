"""Host status reads do not repair registrations or rewrite restart markers."""

import asyncio
import json
import subprocess
import tomllib
from dataclasses import asdict, replace
from pathlib import Path
from threading import get_ident

import pytest
from typer.testing import CliRunner

from stackos import claude_mcp
from stackos.cli import app
from stackos.cli.doctor_commands import _check_mcp_hosts
from stackos.host_mcp import restart_state, service
from stackos.host_mcp.adapters import antigravity, claude_desktop, codex, grok, hermes
from stackos.host_mcp.result import HostMcpResult
from stackos.install import copy_managed_stackos_skill_to
from stackos.operations.system import HostMcpStatusInput, host_mcp_status


def test_current_native_ids_are_shared_selection_choices():
    choices = {
        "codex": ("gpt-6.1-sol", "gpt-6-astra"),
        "claude-code": ("claude-fable-5-2",),
        "grok": ("grok-4.7", "grok-4.7-build-fast"),
        "antigravity": (
            "gemini-4-neon-xhigh",
            "gemini-4-argon-high",
            "gemini-4-hydrogen-high",
            "claude-opus-5-5-high",
            "claude-sonnet-5-5-medium",
        ),
    }
    for key, models in choices.items():
        definition = service._definition_by_key(key)
        for model in models:
            assert model in definition.policy.agent_execution.models
            assert (
                service.agent_selection_error(key, sandbox="workspace-write", model=model) is None
            )
    assert (
        service.agent_selection_error(
            "claude-code",
            sandbox="workspace-write",
            model="claude-fable-5-1",
            model_reasoning_effort="high",
            retained=True,
        )
        is None
    )
    assert (
        service.agent_selection_error(
            "claude-code",
            sandbox="workspace-write",
            model="claude-fable-5-1",
        )
        is not None
    )


def test_connected_broken_shim_is_not_runnable(tmp_path, monkeypatch):
    from dataclasses import replace

    shim = tmp_path / "hermes"
    shim.write_text("#!/bin/sh\nexit 127\n")
    shim.chmod(0o755)
    definition = replace(service._definition_by_key("hermes"), cli=lambda _home: str(shim))
    catalog = service.execution_catalog(definition, home=tmp_path)
    assert catalog["runnable"] == "unavailable"
    assert catalog["cli_version"] is None
    assert catalog["model_access"] == "not_checked"
    connected = service.normalize_result(
        HostMcpResult("hermes", "fixture", "registered_current", "Connected", True, True),
        definition.policy,
        execution=catalog,
    )
    assert connected.connection_state == "connected"
    assert connected.agent_execution["runnable"] == "unavailable"
    assert connected.ok  # An optional execution capability is not an MCP repair failure.
    assert connected.advisory


def test_native_catalog_controls_status_and_fresh_validation(tmp_path, monkeypatch):
    from dataclasses import replace

    cli = tmp_path / "agy"
    cli.write_text("#!/bin/sh\nprintf '1.5.14\\n'\n")
    cli.chmod(0o755)
    definition = replace(
        service._definition_by_key("antigravity"),
        cli=lambda _home: str(cli),
        native_models=lambda _cli: ("gemini-4-neon-xhigh",),
    )
    monkeypatch.setattr(service, "_definition_by_key", lambda _key: definition)
    catalog = service.execution_catalog(definition, home=tmp_path)
    assert catalog["models"] == ["gemini-4-neon-xhigh"]
    assert catalog["model_access"] == "observed"
    assert catalog["cli_version"] == "1.5.14"
    assert (
        service.agent_selection_error(
            "antigravity", sandbox="workspace-write", model="gemini-4-neon-xhigh", catalog=catalog
        )
        is None
    )
    assert service.agent_selection_error(
        "antigravity", sandbox="workspace-write", model="gemini-4-argon-high", catalog=catalog
    )


@pytest.mark.parametrize(
    "text,code,runnable,version",
    [
        ("native version unknown", 0, "available", None),
        ("codex-cli 0.171.0", 0, "available", "0.171.0"),
        ("1.0.0", 127, "unavailable", None),
    ],
)
def test_runnable_and_version_are_independent(tmp_path, text, code, runnable, version):
    from stackos.host_mcp.discovery import probe_cli_version

    cli = tmp_path / "native"
    cli.write_text(f"#!/bin/sh\nprintf '%s\\n' '{text}'\nexit {code}\n")
    cli.chmod(0o755)
    assert probe_cli_version(str(cli)) == (runnable, version)


@pytest.mark.parametrize("include_models", [False, True])
def test_antigravity_inspection_reuses_version_and_only_lists_requested_models(
    tmp_path, monkeypatch, include_models
):
    cli, calls, version = (tmp_path / name for name in ("agy", "calls", "version"))
    cli.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{calls}"\n'
        'case "$1" in\n'
        f'  --version) cat "{version}" ;;\n'
        "  models) printf 'gemini-4-neon-xhigh\\tGemini 4 Neon\\n' ;;\n"
        "  *) exit 2 ;;\nesac\n"
    )
    cli.chmod(0o755)
    monkeypatch.setattr(antigravity, "iter_cli_bins", lambda *_a, **_k: iter([str(cli)]))
    current = HostMcpResult("antigravity", "fixture", "registered_current", "Connected", True, True)
    monkeypatch.setattr(antigravity, "_inspect_assets", lambda *_a, **_k: current)

    def duplicate_resolution(_home):
        pytest.fail("Catalog must reuse the executable resolved by inspection")

    definition = replace(service._definition_by_key("antigravity"), cli=duplicate_resolution)
    monkeypatch.setattr(service, "_definitions", lambda: [definition])
    monkeypatch.setattr(service, "_catalog_account", lambda *_a, **_k: None)
    for native_version in ("1.5.14", "1.5.15"):
        version.write_text(native_version + "\n")
        result = service.inspect_all(home=tmp_path, include_models=include_models).results[0]
        catalog = result.agent_execution
        assert result.connection_state == "connected"
        assert catalog["runnable"] == "available" and catalog["cli_version"] == native_version
        assert catalog["model_access"] == ("observed" if include_models else "not_checked")
        if include_models:
            assert catalog["models"] == ["gemini-4-neon-xhigh"]
        assert str(cli) not in json.dumps(result.to_info())
    assert calls.read_text().splitlines() == (
        ["--version", "models"] * 2 if include_models else ["--version"] * 2
    )


def test_absent_host_is_discovered_once_per_inspection(tmp_path, monkeypatch):
    calls = []

    def absent():
        calls.append("discovery")
        return None

    monkeypatch.setattr(hermes, "resolve_hermes_bin", absent)
    definition = service._definition_by_key("hermes")
    monkeypatch.setattr(service, "_definitions", lambda: [definition])
    for _ in range(2):
        result = service.inspect_all(home=tmp_path, include_models=False).results[0]
        assert result.available is False and result.agent_execution["runnable"] == "unavailable"
    assert calls == ["discovery", "discovery"]


def test_reused_antigravity_version_keeps_its_original_executable_fence(tmp_path, monkeypatch):
    cli = tmp_path / "agy"
    cli.write_text("old executable")
    monkeypatch.setattr(antigravity, "iter_cli_bins", lambda *_a, **_k: iter([str(cli)]))

    def version(argv, **_kwargs):
        cli.write_text("replacement executable")
        return subprocess.CompletedProcess(argv, 0, "1.5.14\n", "")

    monkeypatch.setattr(antigravity.subprocess, "run", version)
    result = service._inspect_definition(service._definition_by_key("antigravity"), tmp_path)
    assert result.agent_execution["probe_status"] == "stale"
    assert result.agent_execution["runnable"] == "unknown"
    assert result.agent_execution["cli_version"] is None


@pytest.mark.parametrize("include_models", [False, True])
@pytest.mark.parametrize("host", [None, "hermes"])
def test_cli_status_propagates_model_demand(tmp_path, monkeypatch, include_models, host):
    calls = []
    result = service.normalize_result(
        HostMcpResult("hermes", "fixture", "absent", "Absent", True, False),
        service._definition_by_key("hermes").policy,
    )

    def inspect_all(**kwargs):
        calls.append(kwargs["include_models"])
        return service.HostMcpAggregate(ok=True, results=[result])

    def inspect_host(_host, **kwargs):
        calls.append(kwargs["include_models"])
        return result

    monkeypatch.setattr("stackos.host_mcp.inspect_all", inspect_all)
    monkeypatch.setattr("stackos.host_mcp.inspect_host", inspect_host)
    arguments = ["mcp-host-status", "--json"]
    if not include_models:
        arguments.append("--no-include-models")
    if host:
        arguments.extend(["--host", host])
    response = CliRunner().invoke(app, arguments, catch_exceptions=False)
    assert response.exit_code == 0 and calls == [include_models]


@pytest.mark.parametrize("include_models", [False, True])
def test_account_change_while_sibling_is_held_invalidates_only_that_catalog(
    tmp_path, monkeypatch, include_models
):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from threading import Event

    from stackos.host_mcp.accounts import DesiredAccount

    cli = tmp_path / "native"
    cli.write_text("#!/bin/sh\nprintf '1.5.14\\n'\n")
    cli.chmod(0o755)
    entered, release = Event(), Event()
    revision = [0]
    definitions = [
        replace(
            service._definition_by_key(key),
            cli=lambda _home: str(cli),
            native_models=lambda _cli: ("grok-4.7",),
        )
        for key in ("grok", "antigravity")
    ]

    definitions[1] = replace(definitions[1], native_models=lambda _cli: ("gemini-4-neon-high",))
    monkeypatch.setattr(service, "_definitions", lambda: definitions)
    monkeypatch.setattr(
        service,
        "_catalog_account",
        lambda d, **_kwargs: DesiredAccount(
            revision=revision[0] if d.policy.host_key == "grok" else 0
        ),
    )

    def inspect(_call, _home, key):
        if key == "antigravity":
            entered.set()
            assert release.wait(5)
        return HostMcpResult(key, "fixture", "registered_current", "Connected", True, True)

    monkeypatch.setattr(service, "_call_adapter", inspect)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.inspect_all, home=tmp_path, include_models=include_models)
        try:
            assert entered.wait(5)
            revision[0] = 2  # A -> B -> A still invalidates the original read.
        finally:
            release.set()
        result = pending.result(timeout=5)
    catalogs = {r.host_key: r.agent_execution for r in result.results}
    assert catalogs["grok"]["probe_status"] == "stale"
    assert catalogs["grok"]["model_access"] == "not_checked"
    assert catalogs["antigravity"]["runnable"] == "available"
    assert catalogs["antigravity"]["model_access"] == (
        "observed" if include_models else "not_checked"
    )
    if include_models:
        assert catalogs["antigravity"]["models"] == ["gemini-4-neon-high"]


def test_native_host_replacement_discards_probe(tmp_path):
    from dataclasses import replace

    cli = tmp_path / "agy"
    cli.write_text("#!/bin/sh\nprintf '1.5.14\\n'\n")
    cli.chmod(0o755)

    def models(_cli):
        cli.write_text("#!/bin/sh\nprintf '2.0.0\\n'\n")
        return ("unaccepted-model",)

    catalog = service.execution_catalog(
        replace(
            service._definition_by_key("antigravity"),
            cli=lambda _home: str(cli),
            native_models=models,
        ),
        home=tmp_path,
    )
    assert catalog["probe_status"] == "stale"
    assert catalog["model_access"] == "not_checked"
    assert "unaccepted-model" not in catalog["models"]


@pytest.mark.parametrize("transition", ["replace", "delete"])
def test_host_replacement_while_sibling_is_held_is_not_published(tmp_path, monkeypatch, transition):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from threading import Event

    fast_cli, slow_cli = tmp_path / "agy", tmp_path / "grok"
    for cli in (fast_cli, slow_cli):
        cli.write_text("#!/bin/sh\nprintf '1.0.92\\n'\n")
        cli.chmod(0o755)
    completed, release = Event(), Event()

    def held(_cli):
        assert release.wait(5)
        return ("sibling-model",)

    definitions = (
        replace(
            service._definition_by_key("antigravity"),
            cli=lambda _home: str(fast_cli),
            native_models=lambda _cli: ("old-host-model",),
        ),
        replace(
            service._definition_by_key("grok"), cli=lambda _home: str(slow_cli), native_models=held
        ),
    )
    inspect = service._inspect_definition

    def observed(definition, home, **kwargs):
        result = inspect(definition, home, **kwargs)
        if definition.policy.host_key == "antigravity":
            completed.set()
        return result

    monkeypatch.setattr(service, "_definitions", lambda: definitions)
    monkeypatch.setattr(service, "_catalog_account", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_inspect_definition", observed)
    monkeypatch.setattr(
        service,
        "_call_adapter",
        lambda _fn, _home, key: HostMcpResult(
            key, "fixture", "registered_current", "Connected", True, True
        ),
    )
    # Exercise the normalized console consumer, including its safe field allowlist.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            lambda: asyncio.run(host_mcp_status(HostMcpStatusInput(), None, None)).model_dump(
                mode="json"
            )
        )
        try:
            assert completed.wait(5)
            if transition == "replace":
                fast_cli.write_text("#!/bin/sh\nprintf '2.0.0\\n'\n")
            else:
                fast_cli.unlink()
        finally:
            release.set()
        output = pending.result(timeout=5)
    rows = {row["host_key"]: row for row in output["items"]}
    stale = rows["antigravity"]["agent_execution"]
    assert stale["probe_status"] == "stale"
    assert stale["runnable"] == "unknown" and stale["cli_version"] is None
    assert stale["model_access"] == "not_checked" and "old-host-model" not in stale["models"]
    assert rows["antigravity"]["connection_state"] == "connected"
    sibling = rows["grok"]["agent_execution"]
    assert sibling["probe_status"] == sibling["model_access"] == "observed"
    assert sibling["cli_version"] == "1.0.92" and sibling["models"] == ["sibling-model"]
    assert str(fast_cli) not in json.dumps(output)


@pytest.fixture
def native_registrations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Real saved host formats; substitute native discovery/CLI reads only."""
    for key in ("STACKOS_MCP_BRIDGE_COMMAND", "STACKOS_WORKSPACE_ROOT", "GROK_HOME", "HERMES_HOME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("STACKOS_HOME", str(tmp_path))
    launcher = tmp_path / "stackos"
    launcher.write_text("#!/bin/sh\nexit 0\n")
    launcher.chmod(0o755)
    monkeypatch.setenv("STACKOS_PACKAGED_CLI", str(launcher))
    desktop = tmp_path / "claude_desktop_config.json"
    monkeypatch.setattr(claude_desktop, "config_path", lambda _home: desktop)
    monkeypatch.setattr(claude_desktop, "_available", lambda _path: True)
    monkeypatch.setattr(codex, "resolve_codex_bins", lambda: ["/fixture/codex"])
    monkeypatch.setattr(claude_mcp, "resolve_claude_bin", lambda _bin: "/fixture/claude")
    monkeypatch.setattr(grok, "resolve_grok_bin", lambda _home: "/fixture/grok")
    monkeypatch.setattr(antigravity, "resolve_antigravity_bin", lambda **_kwargs: None)
    monkeypatch.setattr(hermes, "resolve_hermes_bin", lambda: "/fixture/hermes")
    copy_managed_stackos_skill_to(tmp_path / ".grok/skills")
    codex_config = tmp_path / ".codex/config.toml"
    grok_config = tmp_path / ".grok/config.toml"
    claude_config = tmp_path / ".claude.json"
    paths = [codex_config, grok_config, claude_config, desktop]

    def write(extra=(), *, executable=None, runtime=None):
        for host in ("codex", "grok", "claude-code", "claude-desktop"):
            options = {"--host": "127.0.0.1", "--port": "5180", "--runtime": runtime or host}
            options.update(zip(extra[::2], extra[1::2], strict=True))
            command = [
                executable or str(launcher),
                "mcp-bridge",
                *(part for pair in options.items() for part in pair),
            ]
            entry = {"command": command[0], "args": command[1:]}
            if host in {"codex", "grok"}:
                path = codex_config if host == "codex" else grok_config
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    "[mcp_servers.stackos]\n"
                    f"command = {json.dumps(entry['command'])}\n"
                    f"args = {json.dumps(entry['args'])}\n"
                )
            else:
                path = claude_config if host == "claude-code" else desktop
                path.write_text(json.dumps({"mcpServers": {"stackos": entry}}))
        return [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]

    def codex_read(_binary, args):
        assert args == ["mcp", "get", "stackos", "--json"], "unexpected CLI mutation"
        entry = tomllib.loads(codex_config.read_text())["mcp_servers"]["stackos"]
        payload = {"name": "stackos", "enabled": True, "transport": {"type": "stdio", **entry}}
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    def grok_help(_binary, args, *, home):
        assert home == tmp_path
        assert args == ["mcp", "add", "--help"], "unexpected CLI mutation"
        return subprocess.CompletedProcess(args, 0, "--scope user --transport stdio", "")

    monkeypatch.setattr(codex, "_run_codex", codex_read)
    monkeypatch.setattr(grok, "_run_grok", grok_help)
    return tmp_path, write, paths


@pytest.mark.parametrize("context", ["omitted", "host-port", "all"])
def test_default_bridge_context_agrees_across_status_callers(
    native_registrations, monkeypatch: pytest.MonkeyPatch, context: str
) -> None:
    home, write, paths = native_registrations
    before = write()
    if context != "omitted":
        monkeypatch.setenv("STACKOS_HOST", "127.0.0.1")
        monkeypatch.setenv("STACKOS_PORT", "5180")
    if context == "all":
        monkeypatch.setenv("STACKOS_DATA_DIR", str(home / ".local/share/stackos"))
        monkeypatch.setenv("STACKOS_STATE_DIR", str(home / ".local/state/stackos"))
    ok, doctor_rows = _check_mcp_hosts(home)
    cli = CliRunner().invoke(app, ["mcp-host-status", "--json"], catch_exceptions=False)
    daemon = asyncio.run(host_mcp_status(HostMcpStatusInput(), None, None)).model_dump()
    assert ok, [(row["host_key"], row["status"]) for row in doctor_rows]
    assert cli.exit_code == 0
    assert daemon["ok"]
    for rows in (doctor_rows, json.loads(cli.stdout)["hosts"], daemon["items"]):
        states = {row["host_key"]: row for row in rows}
        for key in ("codex", "claude-code", "claude-desktop", "grok"):
            assert states[key]["connection_state"] == "connected", states[key]
            assert not states[key]["blocking"]
        assert states["antigravity"]["connection_state"] == "unavailable"
        assert states["hermes"]["connection_state"] == "available"
        assert not states["antigravity"]["blocking"] and not states["hermes"]["blocking"]
    assert [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths] == before


@pytest.mark.parametrize(
    "change", ["launcher", "runtime", "workspace", "host", "port", "data", "state", "foreign"]
)
def test_host_context_mismatches_stay_actionable(native_registrations, change: str) -> None:
    home, write, paths = native_registrations
    changes = {
        "launcher": {"executable": "/old/stackos"},
        "runtime": {"runtime": "wrong-host"},
        "workspace": {"extra": ["--workspace-root", str(home)]},
        "host": {"extra": ["--host", "localhost"]},
        "port": {"extra": ["--port", "5199"]},
        "data": {"extra": ["--data-dir", str(home / "custom-data")]},
        "state": {"extra": ["--state-dir", str(home / "custom-state")]},
        "foreign": {"executable": "/foreign/server"},
    }
    before = write(**changes[change])
    ok, rows = _check_mcp_hosts(home)
    assert not ok
    for row in rows:
        if row["host_key"] in {"codex", "claude-code", "claude-desktop", "grok"}:
            assert row["connection_state"] == (
                "review_required" if change == "foreign" else "repair_needed"
            ), row
            assert row["repair"] and row["blocking"]
    assert [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths] == before


def test_restart_marker_retains_equivalent_default_context(native_registrations, monkeypatch):
    home, write, _paths = native_registrations
    write()
    desktop = claude_desktop.config_path(home)
    entry = json.loads(desktop.read_text())["mcpServers"]["stackos"]
    restart_state.mark_restart_required(
        "claude-desktop",
        surface="desktop-json",
        config_path=str(desktop),
        command=[entry["command"], *entry["args"]],
        home=home,
    )
    marker = restart_state.state_path(home)
    before = marker.read_bytes(), marker.stat().st_mtime_ns
    monkeypatch.setattr(claude_desktop, "_claude_desktop_running", lambda: "running")
    monkeypatch.setattr(claude_desktop, "_claude_desktop_started_after", lambda _at: False)
    monkeypatch.setenv("STACKOS_DATA_DIR", str(home / ".local/share/stackos"))
    monkeypatch.setenv("STACKOS_STATE_DIR", str(home / ".local/state/stackos"))
    result = claude_desktop.inspect(home)
    assert result.status == "restart_required" and result.needs_restart
    assert (marker.read_bytes(), marker.stat().st_mtime_ns) == before


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

    def inspect(**_kwargs):
        inspected_threads.append(get_ident())
        return service.HostMcpAggregate(ok=True, results=[])

    monkeypatch.setattr(service, "inspect_all", inspect)
    result = asyncio.run(host_mcp_status(HostMcpStatusInput(), None, None))
    assert result.model_dump() == {"ok": True, "items": []}
    assert len(inspected_threads) == 1
    assert inspected_threads[0] != caller_thread


def test_host_status_projects_shared_execution_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    definitions = service._definitions()
    aggregate = service.HostMcpAggregate(
        ok=True,
        results=[
            service.normalize_result(
                HostMcpResult(d.policy.host_key, "fixture", "absent", "Not installed", True, False),
                d.policy,
            )
            for d in definitions
        ],
    )
    monkeypatch.setattr(service, "inspect_all", lambda **_kwargs: aggregate)

    result = asyncio.run(host_mcp_status(HostMcpStatusInput(), None, None)).model_dump(mode="json")

    for row, definition in zip(result["items"], definitions, strict=True):
        expected = asdict(definition.policy.agent_execution)
        expected.pop("auth_status_command")
        expected.update(credential_status="not_checked", model_access="not_checked")
        assert row["agent_execution"] == json.loads(json.dumps(expected))
        assert row["available"] is False
        assert row["agent_execution"]["credential_status"] == "not_checked"
        assert row["agent_execution"]["model_access"] == "not_checked"


def test_resolve_cli_bin_skips_login_shell_when_path_hits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stackos.host_mcp import discovery

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "demo-cli"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    def forbidden_login_shell(*_args, **_kwargs):
        raise AssertionError("login shell must not run when PATH already resolves the binary")

    monkeypatch.setattr(discovery, "discover_with_login_shell", forbidden_login_shell)
    assert discovery.resolve_cli_bin("demo-cli") == str(exe)


def test_inspect_all_runs_fresh_inspection_when_refresh_overlaps_earlier_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    first_entered = threading.Event()
    release_first = threading.Event()
    calls = 0
    lock = threading.Lock()

    class ChangingAdapter:
        HOST_KEY = "codex"

        @staticmethod
        def inspect(_home: Path) -> HostMcpResult:
            nonlocal calls
            with lock:
                calls += 1
                current = calls
            if current == 1:
                first_entered.set()
                assert release_first.wait(2.0)
                return HostMcpResult(
                    "codex", "fixture", "available_unregistered", "before-change", True, True
                )
            return HostMcpResult(
                "codex", "fixture", "registered_current", "after-change", True, True
            )

    monkeypatch.setattr(
        service,
        "_definitions",
        lambda: [
            service.HostMcpDefinition(
                adapter=ChangingAdapter,
                policy=service.HostMcpPolicy("codex", "ChatGPT / Codex", "automatic"),
            )
        ],
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        earlier = pool.submit(service.inspect_all, home=tmp_path)
        assert first_entered.wait(2.0)
        # External registration change happens while `earlier` is still in flight;
        # a subsequent refresh must run fresh rather than joining `earlier`.
        refresh = pool.submit(service.inspect_all, home=tmp_path)
        r_refresh = refresh.result(timeout=5.0)
        release_first.set()
        r_earlier = earlier.result(timeout=5.0)
    assert calls == 2
    assert r_earlier.results[0].message == "before-change"
    assert r_refresh.results[0].message == "after-change"


def test_unchanged_launcher_script_reprobes_when_backend_changes(tmp_path: Path) -> None:
    import sys

    from stackos.host_mcp.subscription_claude import read_claude

    backend_state = tmp_path / "backend-state.txt"
    agy_wrapper = tmp_path / "agy"
    agy_wrapper.write_text(
        f"#!{sys.executable}\n"
        f"import pathlib, sys\n"
        f"val = pathlib.Path({str(backend_state)!r}).read_text().strip()\n"
        f"if val == 'ok':\n"
        f"    print('1.2.3')\n"
        f"    sys.exit(0)\n"
        f"sys.exit(1)\n",
        encoding="utf-8",
    )
    agy_wrapper.chmod(0o755)
    backend_state.write_text("ok", encoding="utf-8")
    assert antigravity.resolve_antigravity_bin(explicit=str(agy_wrapper)) == str(agy_wrapper)
    # Backend breaks while wrapper metadata (realpath/mtime/size) stays identical.
    backend_state.write_text("missing", encoding="utf-8")
    assert antigravity.resolve_antigravity_bin(explicit=str(agy_wrapper)) is None

    claude_state = tmp_path / "claude-version.txt"
    claude_wrapper = tmp_path / "claude"
    claude_wrapper.write_text(
        f"#!{sys.executable}\n"
        f"import json, pathlib, sys\n"
        f"ver = pathlib.Path({str(claude_state)!r}).read_text().strip()\n"
        f"if sys.argv[1:] == ['--version']:\n"
        f"    print(ver)\n"
        f"elif sys.argv[1:3] == ['auth', 'status']:\n"
        f"    print(json.dumps({{'loggedIn': True, 'authMethod': 'claude.ai',\n"
        f"                      'subscriptionType': 'pro'}}))\n"
        f"else:\n"
        f"    print('')\n",
        encoding="utf-8",
    )
    claude_wrapper.chmod(0o755)
    claude_state.write_text("2.1.280 (Claude Code)", encoding="utf-8")
    assert read_claude(str(claude_wrapper)) == {"plan": "Claude Pro", "meters": []}
    # Wrapper now dispatches to an unsupported older Claude backend without changing wrapper stat.
    claude_state.write_text("2.1.20 (Claude Code)", encoding="utf-8")
    assert read_claude(str(claude_wrapper)) is None
