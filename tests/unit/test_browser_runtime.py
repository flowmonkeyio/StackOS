"""Focused gstack lifecycle tests at the daemon runtime boundary."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stackos import install as installer
from stackos.browser.runtime import (
    BROWSER_PROFILE_DIRNAME,
    BROWSER_PROVIDER,
    BrowserRuntime,
    NativeProcessResult,
    ProcessIdentity,
    browser_profile_dir,
    gstack_state_file,
    native_process_env,
)
from stackos.repositories.base import ConflictError, ValidationError


def _runtime_layout(root: Path) -> tuple[Path, Path]:
    executable = root / "gstack" / "browse" / "dist" / "browse"
    server = root / "gstack" / "browse" / "src" / "server.ts"
    bun = root / "bin" / "bun"
    chromium = (
        root
        / "browsers"
        / "chromium-1234"
        / "chrome-mac-arm64"
        / "Google Chrome for Testing.app"
        / "Contents"
        / "MacOS"
        / "Google Chrome for Testing"
    )
    browser_marker = root / "browsers" / "chromium-1234" / "INSTALLATION_COMPLETE"
    vendor_metadata = root / "gstack" / "node_modules" / "playwright-core" / "browsers.json"
    for path in (executable, server, bun, chromium, browser_marker, vendor_metadata):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    executable.chmod(0o700)
    bun.chmod(0o700)
    chromium.chmod(0o700)
    (root / "gstack" / "LICENSE").write_text("MIT", encoding="utf-8")
    (root / "gstack" / "NOTICE.md").write_text("notice", encoding="utf-8")
    vendor_metadata.write_text(
        json.dumps(
            {"browsers": [{"name": "chromium", "revision": "1234", "installByDefault": True}]}
        ),
        encoding="utf-8",
    )
    (root / "manifest.json").write_text(
        json.dumps(installer._runtime_manifest()),
        encoding="utf-8",
    )
    return executable, server


class _Inspector:
    def __init__(self, identities: dict[int, ProcessIdentity]) -> None:
        self.identities = identities

    def inspect(self, pid: int) -> ProcessIdentity | None:
        return self.identities.get(pid)


async def _healthy(_port: int) -> bool:
    return True


def _state(path: Path, *, pid: int, server: Path, started_at: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pid": pid,
                "port": 32123,
                "token": "must-never-escape",
                "startedAt": started_at.isoformat().replace("+00:00", "Z"),
                "serverPath": str(server),
            }
        ),
        encoding="utf-8",
    )


def _session_args(data_dir: Path, session_key: str = "primary") -> dict[str, object]:
    return {
        "session_ref": f"browser-session:project-7:persisted:{session_key}",
        "profile_ref": "browser-profile:project-7:persisted",
        "profile_dir": browser_profile_dir(data_dir, project_id=7, profile_key="persisted"),
        "state_file": gstack_state_file(
            data_dir,
            project_id=7,
            profile_key="persisted",
            session_key=session_key,
        ),
        "data_dir": data_dir,
    }


@pytest.mark.asyncio
async def test_selected_context_is_separate_from_healthy_state(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    executable, server = _runtime_layout(data_dir / "browser-runtime")
    started_at = datetime.now(tz=UTC) - timedelta(seconds=2)
    args = _session_args(data_dir)
    _state(args["state_file"], pid=9123, server=server, started_at=started_at)  # type: ignore[arg-type]
    runtime = BrowserRuntime(
        data_dir=data_dir,
        process_inspector=_Inspector(
            {9123: ProcessIdentity(pid=9123, started_at=started_at, command=f"bun run {server}")}
        ),
        health_probe=_healthy,
    )

    observed = await runtime.inspect_session(**args)  # type: ignore[arg-type]
    context = await runtime.session_context(**args, observed=observed)  # type: ignore[arg-type]

    assert BROWSER_PROVIDER == "gstack"
    assert BROWSER_PROFILE_DIRNAME == "playwright-chromium"
    assert observed.status == "running"
    assert observed.owned is True
    assert observed.healthy is True
    assert Path(context.executable) == executable
    assert context.env["BROWSE_STATE_FILE"] == str(args["state_file"])
    assert "BROWSE_NO_AUTOSTART" not in context.env
    assert "must-never-escape" not in json.dumps(context.to_dict())


@pytest.mark.asyncio
async def test_unhealthy_owner_and_stopped_session_receive_native_context(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _executable, server = _runtime_layout(data_dir / "browser-runtime")
    started_at = datetime.now(tz=UTC) - timedelta(seconds=2)
    owner_args = _session_args(data_dir, "owner")
    _state(owner_args["state_file"], pid=9124, server=server, started_at=started_at)  # type: ignore[arg-type]

    async def unhealthy(_port: int) -> bool:
        return False

    runtime = BrowserRuntime(
        data_dir=data_dir,
        process_inspector=_Inspector(
            {9124: ProcessIdentity(pid=9124, started_at=started_at, command=f"bun run {server}")}
        ),
        health_probe=unhealthy,
    )
    failed = await runtime.inspect_session(**owner_args)  # type: ignore[arg-type]
    failed_context = await runtime.session_context(**owner_args, observed=failed)  # type: ignore[arg-type]
    stopped_args = _session_args(data_dir, "stopped")
    stopped = await runtime.inspect_session(**stopped_args)  # type: ignore[arg-type]

    assert failed.status == "failed"
    assert failed.owned is True
    assert failed.healthy is False
    assert "BROWSE_NO_AUTOSTART" not in failed_context.env
    assert stopped.status == "stale"
    with pytest.raises(ConflictError) as raised:
        await runtime.session_context(**stopped_args, observed=stopped)  # type: ignore[arg-type]
    assert raised.value.data["owner_session_ref"] == "browser-session:project-7:persisted:owner"


@pytest.mark.asyncio
async def test_stopped_session_context_is_available_without_another_owner(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    executable, _server = _runtime_layout(data_dir / "browser-runtime")
    args = _session_args(data_dir, "stopped")
    runtime = BrowserRuntime(
        data_dir=data_dir, process_inspector=_Inspector({}), health_probe=_healthy
    )

    observed = await runtime.inspect_session(**args)  # type: ignore[arg-type]
    context = await runtime.session_context(**args, observed=observed)  # type: ignore[arg-type]

    assert observed.status == "stale"
    assert observed.owned is False
    assert Path(context.executable) == executable
    assert "BROWSE_NO_AUTOSTART" not in context.env


def test_native_process_env_strips_ambient_browser_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", "/ordinary/home")
    monkeypatch.setenv("BROWSE_SERVER_SCRIPT", "/ambient/server.ts")
    monkeypatch.setenv("BROWSE_TUNNEL", "ambient-tunnel")
    monkeypatch.setenv("GSTACK_PROXY_MODE", "ambient-proxy-mode")
    monkeypatch.setenv("CHROMIUM_PROFILE", "/ambient/chromium-profile")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/ambient/browsers")

    environment = native_process_env(
        {
            "BROWSE_STATE_FILE": "/owned/state.json",
            "CHROMIUM_PROFILE": "/owned/profile",
            "PLAYWRIGHT_BROWSERS_PATH": "/owned/browsers",
        }
    )

    assert environment["HOME"] == "/ordinary/home"
    assert environment["BROWSE_STATE_FILE"] == "/owned/state.json"
    assert environment["CHROMIUM_PROFILE"] == "/owned/profile"
    assert environment["PLAYWRIGHT_BROWSERS_PATH"] == "/owned/browsers"
    for key in ("BROWSE_SERVER_SCRIPT", "BROWSE_TUNNEL", "GSTACK_PROXY_MODE"):
        assert key not in environment


@pytest.mark.asyncio
async def test_runtime_status_and_start_reject_incomplete_browser_payload(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _executable, _server = _runtime_layout(data_dir / "browser-runtime")
    (data_dir / "browser-runtime" / "browsers" / "chromium-1234" / "INSTALLATION_COMPLETE").unlink()
    invocations: list[list[str]] = []

    async def runner(
        _executable: Path,
        argv: list[str],
        _cwd: Path,
        _env: dict[str, str],
        _stdin: bytes,
    ) -> NativeProcessResult:
        invocations.append(argv)
        return NativeProcessResult(stdout=b"", stderr=b"", exit_code=0)

    runtime = BrowserRuntime(data_dir=data_dir, health_probe=_healthy, command_runner=runner)
    args = _session_args(data_dir)

    assert runtime.status(data_dir=data_dir).package_installed is False
    with pytest.raises(ValidationError, match="gstack runtime is not installed"):
        await runtime.session_context(**args)  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="gstack runtime is not installed"):
        await runtime.start_session(**args)  # type: ignore[arg-type]
    assert invocations == []


@pytest.mark.asyncio
async def test_same_profile_start_race_launches_once_and_uses_plain_status(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _executable, server = _runtime_layout(data_dir / "browser-runtime")
    started_at = datetime.now(tz=UTC) - timedelta(seconds=1)
    args = _session_args(data_dir)
    state_file = args["state_file"]
    inspector = _Inspector({})
    invocations: list[tuple[list[str], dict[str, str]]] = []

    async def runner(
        _executable: Path,
        argv: list[str],
        _cwd: Path,
        env: dict[str, str],
        _stdin: bytes,
    ) -> NativeProcessResult:
        invocations.append((argv, env))
        await asyncio.sleep(0)
        inspector.identities[9127] = ProcessIdentity(
            pid=9127, started_at=started_at, command=f"bun run {server}"
        )
        _state(state_file, pid=9127, server=server, started_at=started_at)  # type: ignore[arg-type]
        return NativeProcessResult(stdout=b"started\n", stderr=b"", exit_code=0)

    runtime = BrowserRuntime(
        data_dir=data_dir,
        process_inspector=inspector,
        health_probe=_healthy,
        command_runner=runner,
    )
    first, second = await asyncio.gather(
        runtime.start_session(**args),
        runtime.start_session(**args),  # type: ignore[arg-type]
    )

    assert first.owned is True and second.owned is True
    assert [argv for argv, _env in invocations] == [["status"]]
    assert "BROWSE_NO_AUTOSTART" not in invocations[0][1]


@pytest.mark.asyncio
async def test_stop_ack_without_retirement_keeps_profile_fenced(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _executable, server = _runtime_layout(data_dir / "browser-runtime")
    started_at = datetime.now(tz=UTC) - timedelta(seconds=2)
    args = _session_args(data_dir)
    state_file = args["state_file"]
    _state(state_file, pid=9128, server=server, started_at=started_at)  # type: ignore[arg-type]
    inspector = _Inspector(
        {9128: ProcessIdentity(pid=9128, started_at=started_at, command=f"bun run {server}")}
    )
    calls: list[list[str]] = []

    async def runner(
        _executable: Path,
        argv: list[str],
        _cwd: Path,
        _env: dict[str, str],
        _stdin: bytes,
    ) -> NativeProcessResult:
        calls.append(argv)
        return NativeProcessResult(stdout=b"ack\n", stderr=b"", exit_code=0)

    runtime = BrowserRuntime(
        data_dir=data_dir,
        process_inspector=inspector,
        health_probe=_healthy,
        command_runner=runner,
        stop_timeout_seconds=0.01,
    )
    with pytest.raises(ValidationError, match="has not retired"):
        await runtime.stop_session(**args)  # type: ignore[arg-type]

    assert calls == [["stop"]]
    assert state_file.is_file()  # type: ignore[union-attr]
    assert inspector.inspect(9128) is not None
