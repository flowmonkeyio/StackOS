"""Focused gstack lifecycle tests at the daemon runtime boundary."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel

from stackos import install as installer
from stackos.browser.runtime import (
    BROWSER_PROFILE_DIRNAME,
    BROWSER_PROVIDER,
    BrowserRuntime,
    NativeProcessResult,
    ProcessIdentity,
    browser_profile_dir,
    gstack_state_file,
)
from stackos.db.connection import make_memory_engine
from stackos.db.models import BrowserProfile, BrowserSession, Project
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.browser import BrowserRepository


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


@pytest.mark.asyncio
async def test_reconstructed_runtime_discovers_only_owned_healthy_state(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    executable, server = _runtime_layout(data_dir / "browser-runtime")
    started_at = datetime.now(tz=UTC) - timedelta(seconds=2)
    state_file = gstack_state_file(
        data_dir,
        project_id=7,
        profile_key="persisted",
        session_key="primary",
    )
    _state(state_file, pid=9123, server=server, started_at=started_at)
    inspector = _Inspector(
        {9123: ProcessIdentity(pid=9123, started_at=started_at, command=f"bun run {server}")}
    )

    runtime = BrowserRuntime(
        data_dir=data_dir,
        process_inspector=inspector,
        health_probe=_healthy,
    )
    result = await runtime.inspect_session(
        session_ref="browser-session:project-7:persisted:primary",
        profile_ref="browser-profile:project-7:persisted",
        profile_dir=browser_profile_dir(data_dir, project_id=7, profile_key="persisted"),
        state_file=state_file,
        data_dir=data_dir,
    )

    assert BROWSER_PROVIDER == "gstack"
    assert BROWSER_PROFILE_DIRNAME == "playwright-chromium"
    assert executable == Path(result.native_cli.executable)
    assert result.owned is True
    assert result.healthy is True
    assert result.status == "running"
    assert result.native_cli is not None
    assert result.native_cli.env["BROWSE_NO_AUTOSTART"] == "1"
    assert "must-never-escape" not in json.dumps(result.to_safe_dict(include_native_cli=True))


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

    assert runtime.status(data_dir=data_dir).package_installed is False
    with pytest.raises(ValidationError, match="gstack runtime is not installed"):
        await runtime.start_session(
            session_ref="browser-session:project-7:persisted:primary",
            profile_ref="browser-profile:project-7:persisted",
            profile_dir=browser_profile_dir(data_dir, project_id=7, profile_key="persisted"),
            state_file=gstack_state_file(
                data_dir,
                project_id=7,
                profile_key="persisted",
                session_key="primary",
            ),
            data_dir=data_dir,
        )
    assert invocations == []


@pytest.mark.asyncio
async def test_alive_unhealthy_owner_fences_profile_and_never_launches_competitor(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    _executable, server = _runtime_layout(data_dir / "browser-runtime")
    started_at = datetime.now(tz=UTC) - timedelta(seconds=2)
    owner_state = gstack_state_file(
        data_dir,
        project_id=7,
        profile_key="persisted",
        session_key="owner",
    )
    _state(owner_state, pid=9124, server=server, started_at=started_at)
    calls: list[tuple[list[str], bytes]] = []

    async def unhealthy(_port: int) -> bool:
        return False

    async def runner(
        _executable: Path,
        argv: list[str],
        _cwd: Path,
        _env: dict[str, str],
        stdin: bytes,
    ) -> NativeProcessResult:
        calls.append((argv, stdin))
        return NativeProcessResult(stdout=b"", stderr=b"", exit_code=0)

    runtime = BrowserRuntime(
        data_dir=data_dir,
        process_inspector=_Inspector(
            {9124: ProcessIdentity(pid=9124, started_at=started_at, command=f"bun run {server}")}
        ),
        health_probe=unhealthy,
        command_runner=runner,
    )
    profile_dir = browser_profile_dir(data_dir, project_id=7, profile_key="persisted")
    candidate_state = gstack_state_file(
        data_dir,
        project_id=7,
        profile_key="persisted",
        session_key="candidate",
    )

    with pytest.raises(ConflictError) as raised:
        await runtime.start_session(
            session_ref="browser-session:project-7:persisted:candidate",
            profile_ref="browser-profile:project-7:persisted",
            profile_dir=profile_dir,
            state_file=candidate_state,
            data_dir=data_dir,
        )

    assert raised.value.data["owner_session_ref"] == "browser-session:project-7:persisted:owner"
    assert calls == []


@pytest.mark.asyncio
async def test_owned_unhealthy_session_is_failed_but_remains_profile_fence(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _executable, server = _runtime_layout(data_dir / "browser-runtime")
    started_at = datetime.now(tz=UTC) - timedelta(seconds=2)
    state_file = gstack_state_file(
        data_dir,
        project_id=7,
        profile_key="persisted",
        session_key="primary",
    )
    _state(state_file, pid=9129, server=server, started_at=started_at)

    async def unhealthy(_port: int) -> bool:
        return False

    runtime = BrowserRuntime(
        data_dir=data_dir,
        process_inspector=_Inspector(
            {9129: ProcessIdentity(pid=9129, started_at=started_at, command=f"bun run {server}")}
        ),
        health_probe=unhealthy,
    )
    state = await runtime.inspect_session(
        session_ref="browser-session:project-7:persisted:primary",
        profile_ref="browser-profile:project-7:persisted",
        profile_dir=browser_profile_dir(data_dir, project_id=7, profile_key="persisted"),
        state_file=state_file,
        data_dir=data_dir,
    )

    assert state.status == "failed"
    assert state.owned is True
    assert state.healthy is False
    assert state.native_cli is None

    engine = make_memory_engine()
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            project = Project(
                slug="native-runtime",
                name="Native runtime",
                domain="example.test",
                locale="en-US",
                is_active=True,
            )
            session.add(project)
            session.commit()
            session.refresh(project)
            assert project.id is not None
            profile = BrowserProfile(
                project_id=project.id,
                profile_key="persisted",
                name="Persisted",
                profile_ref="browser-profile:project-7:persisted",
            )
            session.add(profile)
            session.commit()
            session.refresh(profile)
            assert profile.id is not None
            browser_session = BrowserSession(
                project_id=project.id,
                profile_id=profile.id,
                session_ref="browser-session:project-7:persisted:primary",
                status="running",
            )
            session.add(browser_session)
            session.commit()

            repository = BrowserRepository(session)
            assert repository.reconcile_sessions(
                project_id=project.id, states={browser_session.session_ref: state}
            ) == [browser_session.session_ref]
            row, persisted_profile = repository.get_session(
                project_id=project.id, session_ref=browser_session.session_ref
            )
            result = repository.session_out(
                row, persisted_profile, state=state, include_native_cli=True
            )

            assert row.status == result.status == "failed"
            assert row.ended_at is None
            assert result.healthy is False
            assert result.native_cli is None
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_native_runner_preserves_stream_bytes_and_invokes_once(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _executable, server = _runtime_layout(data_dir / "browser-runtime")
    started_at = datetime.now(tz=UTC) - timedelta(seconds=2)
    state_file = gstack_state_file(
        data_dir,
        project_id=7,
        profile_key="persisted",
        session_key="primary",
    )
    _state(state_file, pid=9125, server=server, started_at=started_at)
    received: list[tuple[list[str], bytes, dict[str, str]]] = []

    async def runner(
        _executable: Path,
        argv: list[str],
        _cwd: Path,
        env: dict[str, str],
        stdin: bytes,
    ) -> NativeProcessResult:
        received.append((argv, stdin, env))
        return NativeProcessResult(stdout=b"ok\x00\xff\n", stderr=b"warn\n", exit_code=17)

    runtime = BrowserRuntime(
        data_dir=data_dir,
        process_inspector=_Inspector(
            {9125: ProcessIdentity(pid=9125, started_at=started_at, command=f"bun run {server}")}
        ),
        health_probe=_healthy,
        command_runner=runner,
    )
    result = await runtime.run_native(
        session_ref="browser-session:project-7:persisted:primary",
        profile_ref="browser-profile:project-7:persisted",
        profile_dir=browser_profile_dir(data_dir, project_id=7, profile_key="persisted"),
        state_file=state_file,
        data_dir=data_dir,
        argv=["unknown command", "quotes' and spaces"],
        stdin="line\x00\n",
    )

    assert result.exit_code == 17
    assert result.encoding == "base64"
    assert received == [(["unknown command", "quotes' and spaces"], b"line\x00\n", received[0][2])]
    assert received[0][2]["BROWSE_NO_AUTOSTART"] == "1"
    assert result.decode_stdout() == b"ok\x00\xff\n"
    assert result.decode_stderr() == b"warn\n"


@pytest.mark.asyncio
async def test_native_runner_strips_ambient_browser_controls_and_keeps_owned_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    executable, server = _runtime_layout(data_dir / "browser-runtime")
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os\n"
        "keys = [\n"
        "    'HOME', 'BROWSE_SERVER_SCRIPT', 'BROWSE_TUNNEL', 'BROWSE_PROXY_URL',\n"
        "    'BROWSE_STATE_FILE', 'BROWSE_NO_AUTOSTART', 'BROWSE_HEADED',\n"
        "    'BROWSE_PARENT_PID', 'GSTACK_HOME', 'GSTACK_PROXY_MODE',\n"
        "    'CHROMIUM_PROFILE', 'CHROMIUM_PROXY_SERVER',\n"
        "    'PLAYWRIGHT_BROWSERS_PATH', 'PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD',\n"
        "]\n"
        "print(json.dumps({key: os.environ.get(key) for key in keys}, sort_keys=True))\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    started_at = datetime.now(tz=UTC) - timedelta(seconds=2)
    state_file = gstack_state_file(
        data_dir,
        project_id=7,
        profile_key="persisted",
        session_key="primary",
    )
    _state(state_file, pid=9130, server=server, started_at=started_at)
    monkeypatch.setenv("HOME", "/ordinary/home")
    monkeypatch.setenv("BROWSE_SERVER_SCRIPT", "/ambient/server.ts")
    monkeypatch.setenv("BROWSE_TUNNEL", "ambient-tunnel")
    monkeypatch.setenv("BROWSE_PROXY_URL", "http://ambient-proxy.test")
    monkeypatch.setenv("GSTACK_HOME", "/ambient/gstack")
    monkeypatch.setenv("GSTACK_PROXY_MODE", "ambient-proxy-mode")
    monkeypatch.setenv("CHROMIUM_PROFILE", "/ambient/chromium-profile")
    monkeypatch.setenv("CHROMIUM_PROXY_SERVER", "ambient-chromium-proxy")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/ambient/browsers")
    monkeypatch.setenv("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "1")
    runtime = BrowserRuntime(
        data_dir=data_dir,
        process_inspector=_Inspector(
            {9130: ProcessIdentity(pid=9130, started_at=started_at, command=f"bun run {server}")}
        ),
        health_probe=_healthy,
    )

    result = await runtime.run_native(
        session_ref="browser-session:project-7:persisted:primary",
        profile_ref="browser-profile:project-7:persisted",
        profile_dir=browser_profile_dir(data_dir, project_id=7, profile_key="persisted"),
        state_file=state_file,
        data_dir=data_dir,
        argv=["status"],
    )
    environment = json.loads(result.stdout)

    assert result.exit_code == 0
    assert result.encoding == "utf-8"
    assert environment["HOME"] == "/ordinary/home"
    for key in (
        "BROWSE_SERVER_SCRIPT",
        "BROWSE_TUNNEL",
        "BROWSE_PROXY_URL",
        "GSTACK_PROXY_MODE",
        "CHROMIUM_PROXY_SERVER",
        "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD",
    ):
        assert environment[key] is None
    assert environment["BROWSE_STATE_FILE"] == str(state_file)
    assert environment["BROWSE_NO_AUTOSTART"] == "1"
    assert environment["BROWSE_HEADED"] == "1"
    assert environment["BROWSE_PARENT_PID"] == "0"
    assert environment["GSTACK_HOME"] == str(state_file.parent.parent / "gstack-home")
    assert environment["CHROMIUM_PROFILE"] == str(
        browser_profile_dir(data_dir, project_id=7, profile_key="persisted")
    )
    assert environment["PLAYWRIGHT_BROWSERS_PATH"] == str(data_dir / "browser-runtime" / "browsers")


@pytest.mark.asyncio
async def test_pid_reuse_record_is_stale_and_is_never_adopted(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _executable, server = _runtime_layout(data_dir / "browser-runtime")
    state_started = datetime.now(tz=UTC) - timedelta(minutes=5)
    state_file = gstack_state_file(
        data_dir, project_id=7, profile_key="persisted", session_key="primary"
    )
    _state(state_file, pid=9126, server=server, started_at=state_started)
    runtime = BrowserRuntime(
        data_dir=data_dir,
        process_inspector=_Inspector(
            {
                # The command alone matches; a newer OS process start proves
                # the recorded PID belongs to a different process.
                9126: ProcessIdentity(
                    pid=9126,
                    started_at=datetime.now(tz=UTC),
                    command=f"bun run {server}",
                )
            }
        ),
        health_probe=_healthy,
    )

    result = await runtime.inspect_session(
        session_ref="browser-session:project-7:persisted:primary",
        profile_ref="browser-profile:project-7:persisted",
        profile_dir=browser_profile_dir(data_dir, project_id=7, profile_key="persisted"),
        state_file=state_file,
        data_dir=data_dir,
    )

    assert result.status == "stale"
    assert result.owned is False
    assert result.native_cli is None


@pytest.mark.asyncio
async def test_same_profile_start_race_launches_once_and_uses_plain_status(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _executable, server = _runtime_layout(data_dir / "browser-runtime")
    started_at = datetime.now(tz=UTC) - timedelta(seconds=1)
    state_file = gstack_state_file(
        data_dir, project_id=7, profile_key="persisted", session_key="primary"
    )
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
        _state(state_file, pid=9127, server=server, started_at=started_at)
        return NativeProcessResult(stdout=b"started\n", stderr=b"", exit_code=0)

    runtime = BrowserRuntime(
        data_dir=data_dir,
        process_inspector=inspector,
        health_probe=_healthy,
        command_runner=runner,
    )
    args = {
        "session_ref": "browser-session:project-7:persisted:primary",
        "profile_ref": "browser-profile:project-7:persisted",
        "profile_dir": browser_profile_dir(data_dir, project_id=7, profile_key="persisted"),
        "state_file": state_file,
        "data_dir": data_dir,
    }

    first, second = await asyncio.gather(
        runtime.start_session(**args), runtime.start_session(**args)
    )

    assert first.owned is True and second.owned is True
    assert [argv for argv, _env in invocations] == [["status"]]
    assert "BROWSE_NO_AUTOSTART" not in invocations[0][1]


@pytest.mark.asyncio
async def test_stop_ack_without_retirement_keeps_profile_fenced(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _executable, server = _runtime_layout(data_dir / "browser-runtime")
    started_at = datetime.now(tz=UTC) - timedelta(seconds=2)
    state_file = gstack_state_file(
        data_dir, project_id=7, profile_key="persisted", session_key="primary"
    )
    _state(state_file, pid=9128, server=server, started_at=started_at)
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
        await runtime.stop_session(
            session_ref="browser-session:project-7:persisted:primary",
            profile_ref="browser-profile:project-7:persisted",
            profile_dir=browser_profile_dir(data_dir, project_id=7, profile_key="persisted"),
            state_file=state_file,
            data_dir=data_dir,
        )

    assert calls == [["stop"]]
    assert state_file.is_file()
    assert inspector.inspect(9128) is not None
