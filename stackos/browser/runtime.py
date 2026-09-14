"""Daemon-owned lifecycle for upstream gstack browser sessions.

StackOS intentionally owns only profile/session custody and the gstack process
lifecycle. The upstream ``browse`` executable owns every browser command and
its browser semantics.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import os
import re
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.error import URLError
from urllib.request import urlopen

from stackos.repositories.base import ConflictError, ValidationError

_SAFE_KEY_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
_PROFILE_LOCKS: dict[str, asyncio.Lock] = {}
_IDENTITY_START_TOLERANCE = timedelta(minutes=2)
_STOP_POLL_SECONDS = 0.05
_STOP_TIMEOUT_SECONDS = 5.0

BROWSER_PROVIDER = "gstack"
# Kept literally so existing persistent profiles are reused in place.
BROWSER_PROFILE_DIRNAME = "playwright-chromium"
GSTACK_RUNTIME_DIRNAME = "browser-runtime"
GSTACK_RUNTIME_REPAIR = "Run `stackos install` to install the StackOS gstack runtime."


def safe_browser_key(value: str) -> str:
    """Normalize a profile/session key for refs and daemon-owned paths."""
    clean = _SAFE_KEY_RE.sub("-", value.strip()).strip(".-").lower()
    if not clean:
        raise ValidationError("browser key must contain at least one safe character")
    return clean[:160]


def browser_profile_dir(root: Path, *, project_id: int, profile_key: str) -> Path:
    """Return the stable daemon-private profile directory for one profile."""
    return (
        Path(root)
        / "browser-profiles"
        / BROWSER_PROFILE_DIRNAME
        / f"project-{project_id}"
        / safe_browser_key(profile_key)
    )


def packaged_stackos_root() -> Path | None:
    """Return the embedded StackOS payload root when running from the macOS app."""
    executable = Path(sys.executable).resolve()
    if executable.parent.name != "bin" or executable.parent.parent.name != ".venv":
        return None
    root = executable.parent.parent.parent
    if (
        root.name != "stackos"
        or root.parent.name != "Resources"
        or root.parent.parent.name != "Contents"
        or root.parent.parent.parent.suffix != ".app"
    ):
        return None
    return root


def _data_dir(data_dir: Path | None) -> Path:
    if data_dir is not None:
        return Path(data_dir)
    from stackos.config import get_settings

    return Path(get_settings().data_dir)


def gstack_runtime_root(data_dir: Path | None = None) -> Path:
    """Return the packaged or data-dir gstack runtime root, without installing it."""
    packaged_root = packaged_stackos_root()
    if packaged_root is not None:
        return packaged_root / GSTACK_RUNTIME_DIRNAME
    return _data_dir(data_dir) / GSTACK_RUNTIME_DIRNAME


def gstack_executable_path(data_dir: Path | None = None) -> Path | None:
    path = gstack_runtime_root(data_dir) / "gstack" / "browse" / "dist" / "browse"
    return path if path.is_file() and os.access(path, os.X_OK) else None


def gstack_bun_path(data_dir: Path | None = None) -> Path | None:
    path = gstack_runtime_root(data_dir) / "bin" / "bun"
    return path if path.is_file() and os.access(path, os.X_OK) else None


def gstack_server_path(data_dir: Path | None = None) -> Path | None:
    path = gstack_runtime_root(data_dir) / "gstack" / "browse" / "src" / "server.ts"
    return path if path.is_file() else None


def gstack_browser_assets_path(data_dir: Path | None = None) -> Path | None:
    path = gstack_runtime_root(data_dir) / "browsers"
    return path if path.is_dir() else None


def gstack_manifest_path(data_dir: Path | None = None) -> Path:
    return gstack_runtime_root(data_dir) / "manifest.json"


def gstack_state_file(
    data_dir: Path,
    *,
    project_id: int,
    profile_key: str,
    session_key: str,
) -> Path:
    """Return deterministic upstream state under daemon-owned storage."""
    return (
        Path(data_dir)
        / "browser-state"
        / "gstack"
        / f"project-{project_id}"
        / safe_browser_key(profile_key)
        / safe_browser_key(session_key)
        / ".gstack"
        / "browse.json"
    )


def gstack_session_cwd(state_file: Path) -> Path:
    """Return gstack's project directory derived from ``BROWSE_STATE_FILE``."""
    return Path(state_file).parent.parent


@dataclass(frozen=True)
class ProcessIdentity:
    """Safe process identity used to reject a reused PID."""

    pid: int
    started_at: datetime
    command: str


class ProcessInspector(Protocol):
    def inspect(self, pid: int) -> ProcessIdentity | None: ...


class SystemProcessInspector:
    """Read a narrow process identity without shell invocation."""

    def inspect(self, pid: int) -> ProcessIdentity | None:
        if pid <= 0:
            return None
        try:
            started = subprocess.run(
                ["ps", "-p", str(pid), "-o", "lstart="],
                check=False,
                capture_output=True,
                text=True,
            )
            command = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return None
        if started.returncode != 0 or command.returncode != 0:
            return None
        try:
            started_at = datetime.strptime(started.stdout.strip(), "%a %b %d %H:%M:%S %Y")
        except ValueError:
            return None
        raw_command = command.stdout.strip()
        if not raw_command:
            return None
        local_zone = datetime.now().astimezone().tzinfo
        return ProcessIdentity(
            pid=pid,
            started_at=started_at.replace(tzinfo=local_zone).astimezone(UTC),
            command=raw_command,
        )


@dataclass(frozen=True)
class NativeProcessResult:
    stdout: bytes
    stderr: bytes
    exit_code: int


@dataclass(frozen=True)
class NativeCliContext:
    executable: str
    cwd: str
    env: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {"executable": self.executable, "cwd": self.cwd, "env": dict(self.env)}


@dataclass(frozen=True)
class NativeCliResult:
    stdout: str
    stderr: str
    exit_code: int
    encoding: Literal["utf-8", "base64"]

    @classmethod
    def from_process(cls, result: NativeProcessResult) -> NativeCliResult:
        try:
            stdout = result.stdout.decode("utf-8", errors="strict")
            stderr = result.stderr.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return cls(
                stdout=base64.b64encode(result.stdout).decode("ascii"),
                stderr=base64.b64encode(result.stderr).decode("ascii"),
                exit_code=result.exit_code,
                encoding="base64",
            )
        return cls(stdout=stdout, stderr=stderr, exit_code=result.exit_code, encoding="utf-8")

    def to_dict(self) -> dict[str, Any]:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "encoding": self.encoding,
        }

    def decode_stdout(self) -> bytes:
        return (
            self.stdout.encode("utf-8")
            if self.encoding == "utf-8"
            else base64.b64decode(self.stdout)
        )

    def decode_stderr(self) -> bytes:
        return (
            self.stderr.encode("utf-8")
            if self.encoding == "utf-8"
            else base64.b64decode(self.stderr)
        )


@dataclass(frozen=True)
class NativeSessionState:
    session_ref: str
    profile_ref: str
    status: Literal["running", "stale", "stopped", "failed"]
    owned: bool
    healthy: bool
    pid: int | None
    repair: str | None = None
    native_cli: NativeCliContext | None = None

    def to_safe_dict(self, *, include_native_cli: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "status": self.status,
            "owned": self.owned,
            "healthy": self.healthy,
            "pid": self.pid,
            "repair": self.repair,
        }
        if include_native_cli and self.native_cli is not None:
            data["native_cli"] = self.native_cli.to_dict()
        return data


@dataclass(frozen=True)
class RuntimeStatus:
    provider: str
    package_installed: bool
    package_version: str | None
    browser_downloaded: bool
    live_session_refs: list[str]
    repair: str | None = None

    def to_dict(self, *, project_id: int | None = None) -> dict[str, Any]:
        marker = f":project-{project_id}:" if project_id is not None else None
        live = (
            self.live_session_refs
            if marker is None
            else [ref for ref in self.live_session_refs if marker in ref]
        )
        return {
            "provider": self.provider,
            "package_installed": self.package_installed,
            "package_version": self.package_version,
            "browser_downloaded": self.browser_downloaded,
            "browser_path_present": self.browser_downloaded,
            "live_session_refs": sorted(live),
            "repair": self.repair,
        }


@dataclass(frozen=True)
class _GstackStateFile:
    pid: int
    port: int
    started_at: datetime
    server_path: Path


def _parse_started_at(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def _read_state_file(path: Path) -> _GstackStateFile | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    pid, port = raw.get("pid"), raw.get("port")
    server_path, started_at = raw.get("serverPath"), _parse_started_at(raw.get("startedAt"))
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 0
        or isinstance(port, bool)
        or not isinstance(port, int)
        or not 1 <= port <= 65535
        or not isinstance(server_path, str)
        or not server_path
        or started_at is None
    ):
        return None
    return _GstackStateFile(
        pid=pid, port=port, started_at=started_at, server_path=Path(server_path).resolve()
    )


def _manifest_version(data_dir: Path) -> str | None:
    try:
        raw = json.loads(gstack_manifest_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    for key in ("gstack_version", "version"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _runtime_paths_ready(data_dir: Path) -> bool:
    # install owns the immutable distribution contract, including the pinned
    # manifest and upstream-selected Chromium marker/executable. Import lazily:
    # install imports this module for path helpers during its own initialization.
    from stackos.install import verify_gstack_runtime

    verified, _reason = verify_gstack_runtime(data_dir=data_dir)
    return verified


def _profile_lock(profile_dir: Path) -> asyncio.Lock:
    key = str(profile_dir.resolve())
    lock = _PROFILE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _PROFILE_LOCKS[key] = lock
    return lock


async def _default_health_probe(port: int) -> bool:
    def _probe() -> bool:
        try:
            with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                if response.status != 200:
                    return False
                body = json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        return isinstance(body, dict) and body.get("status") == "healthy"

    return await asyncio.to_thread(_probe)


async def _default_command_runner(
    executable: Path, argv: list[str], cwd: Path, env: dict[str, str], stdin: bytes
) -> NativeProcessResult:
    child_env = os.environ.copy()
    # An owned lifecycle must not inherit upstream controls that can select a
    # different server, profile, proxy, or browser installation. Keep ordinary
    # process context such as HOME and TMPDIR, then apply the deterministic
    # context selected for this session below.
    for key in tuple(child_env):
        if key.startswith(("BROWSE_", "GSTACK_", "CHROMIUM_", "PLAYWRIGHT_")):
            child_env.pop(key)
    child_env.update(env)
    process = await asyncio.create_subprocess_exec(
        str(executable),
        *argv,
        cwd=str(cwd),
        env=child_env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate(stdin)
    return NativeProcessResult(stdout=stdout, stderr=stderr, exit_code=int(process.returncode or 0))


HealthProbe = Callable[[int], bool | Awaitable[bool]]
CommandRunner = Callable[
    [Path, list[str], Path, dict[str, str], bytes], Awaitable[NativeProcessResult]
]


class BrowserRuntime:
    """Inspect and manage gstack state without interpreting browser commands."""

    def __init__(
        self,
        *,
        data_dir: Path | None = None,
        process_inspector: ProcessInspector | None = None,
        health_probe: HealthProbe | None = None,
        command_runner: CommandRunner | None = None,
        stop_timeout_seconds: float = _STOP_TIMEOUT_SECONDS,
    ) -> None:
        self._data_dir = Path(data_dir) if data_dir is not None else None
        self._process_inspector = process_inspector or SystemProcessInspector()
        self._health_probe = health_probe or _default_health_probe
        self._command_runner = command_runner or _default_command_runner
        self._stop_timeout_seconds = stop_timeout_seconds

    def status(self, *, data_dir: Path | None = None) -> RuntimeStatus:
        root = self._resolve_data_dir(data_dir)
        ready = _runtime_paths_ready(root)
        return RuntimeStatus(
            provider=BROWSER_PROVIDER,
            package_installed=ready,
            package_version=_manifest_version(root),
            browser_downloaded=gstack_browser_assets_path(root) is not None,
            live_session_refs=[],
            repair=None if ready else GSTACK_RUNTIME_REPAIR,
        )

    async def inspect_session(
        self,
        *,
        session_ref: str,
        profile_ref: str,
        profile_dir: Path,
        state_file: Path,
        data_dir: Path | None = None,
    ) -> NativeSessionState:
        root = self._resolve_data_dir(data_dir)
        state = _read_state_file(state_file)
        if state is None:
            return self._stale(session_ref, profile_ref, "gstack state is missing or invalid")
        identity = self._process_inspector.inspect(state.pid)
        if not self._matches_owned_identity(identity, state, root):
            return self._stale(
                session_ref, profile_ref, "gstack state no longer owns a matching process"
            )
        healthy = await self._is_healthy(state.port)
        context = self._native_context(
            profile_dir=profile_dir, state_file=state_file, data_dir=root, include_no_autostart=True
        )
        return NativeSessionState(
            session_ref=session_ref,
            profile_ref=profile_ref,
            status="running" if healthy else "failed",
            owned=True,
            healthy=healthy,
            pid=state.pid,
            repair=None
            if healthy
            else (
                "The owned gstack server is busy or unavailable; do not start another "
                "session for this profile."
            ),
            native_cli=context if healthy else None,
        )

    async def start_session(
        self,
        *,
        session_ref: str,
        profile_ref: str,
        profile_dir: Path,
        state_file: Path,
        data_dir: Path | None = None,
    ) -> NativeSessionState:
        root = self._resolve_data_dir(data_dir)
        async with _profile_lock(profile_dir):
            current = await self.inspect_session(
                session_ref=session_ref,
                profile_ref=profile_ref,
                profile_dir=profile_dir,
                state_file=state_file,
                data_dir=root,
            )
            if current.owned:
                if current.healthy:
                    return current
                raise ConflictError(
                    "the selected gstack session still owns this profile but is unavailable",
                    data={"session_ref": session_ref, "repair": current.repair},
                )
            owner = await self._other_profile_owner(
                session_ref=session_ref,
                profile_ref=profile_ref,
                profile_dir=profile_dir,
                state_file=state_file,
                data_dir=root,
            )
            if owner is not None:
                raise ConflictError(
                    "another gstack session owns this persistent profile",
                    data={
                        "owner_session_ref": owner.session_ref,
                        "owner_healthy": owner.healthy,
                        "repair": (
                            "Stop the existing owner and wait for retirement before using another "
                            "session key."
                        ),
                    },
                )
            self._require_runtime(root)
            self._prepare_owned_paths(profile_dir=profile_dir, state_file=state_file)
            process = await self._command_runner(
                self._require_executable(root),
                ["status"],
                gstack_session_cwd(state_file),
                self._native_env(
                    profile_dir=profile_dir,
                    state_file=state_file,
                    data_dir=root,
                    include_no_autostart=False,
                ),
                b"",
            )
            if process.exit_code != 0:
                raise ValidationError(
                    "gstack did not start a usable browser session",
                    data={
                        "provider": BROWSER_PROVIDER,
                        "exit_code": process.exit_code,
                        "repair": GSTACK_RUNTIME_REPAIR,
                    },
                )
            started = await self.inspect_session(
                session_ref=session_ref,
                profile_ref=profile_ref,
                profile_dir=profile_dir,
                state_file=state_file,
                data_dir=root,
            )
            if not started.owned or not started.healthy:
                raise ValidationError(
                    "gstack start completed without a healthy owned session",
                    data={"session_ref": session_ref, "repair": started.repair},
                )
            return started

    async def stop_session(
        self,
        *,
        session_ref: str,
        profile_ref: str,
        profile_dir: Path,
        state_file: Path,
        data_dir: Path | None = None,
    ) -> NativeSessionState:
        root = self._resolve_data_dir(data_dir)
        async with _profile_lock(profile_dir):
            before = _read_state_file(state_file)
            current = await self.inspect_session(
                session_ref=session_ref,
                profile_ref=profile_ref,
                profile_dir=profile_dir,
                state_file=state_file,
                data_dir=root,
            )
            if not current.owned or before is None:
                return NativeSessionState(
                    session_ref=session_ref,
                    profile_ref=profile_ref,
                    status="stale",
                    owned=False,
                    healthy=False,
                    pid=None,
                    repair="gstack session was already retired or could not be verified",
                )
            self._require_runtime(root)
            result = await self._command_runner(
                self._require_executable(root),
                ["stop"],
                gstack_session_cwd(state_file),
                self._native_env(
                    profile_dir=profile_dir,
                    state_file=state_file,
                    data_dir=root,
                    include_no_autostart=False,
                ),
                b"",
            )
            if result.exit_code != 0:
                raise ValidationError(
                    "gstack stop command failed",
                    data={"session_ref": session_ref, "exit_code": result.exit_code},
                )
            deadline = asyncio.get_running_loop().time() + self._stop_timeout_seconds
            while True:
                if self._state_retired(state_file, before) and not self._identity_is_alive(
                    before, root
                ):
                    return NativeSessionState(
                        session_ref=session_ref,
                        profile_ref=profile_ref,
                        status="stopped",
                        owned=False,
                        healthy=False,
                        pid=None,
                    )
                if asyncio.get_running_loop().time() >= deadline:
                    raise ValidationError(
                        "gstack acknowledged stop but the owned process has not retired",
                        data={
                            "session_ref": session_ref,
                            "pid": before.pid,
                            "repair": (
                                "Wait for the owned gstack process and state to retire; StackOS "
                                "will not kill it or clear profile locks."
                            ),
                        },
                    )
                await asyncio.sleep(_STOP_POLL_SECONDS)

    async def run_native(
        self,
        *,
        session_ref: str,
        profile_ref: str,
        profile_dir: Path,
        state_file: Path,
        data_dir: Path | None = None,
        argv: list[str],
        stdin: str | None = None,
    ) -> NativeCliResult:
        if not all(isinstance(arg, str) for arg in argv):
            raise ValidationError("browser argv must contain only strings")
        root = self._resolve_data_dir(data_dir)
        current = await self.inspect_session(
            session_ref=session_ref,
            profile_ref=profile_ref,
            profile_dir=profile_dir,
            state_file=state_file,
            data_dir=root,
        )
        if not current.owned or not current.healthy:
            raise ValidationError(
                "browser session is not available for native commands",
                data={"session_ref": session_ref, "repair": current.repair},
            )
        if current.native_cli is None:
            raise ValidationError(
                "browser session is missing its verified native CLI context",
                data={"session_ref": session_ref},
            )
        result = await self._command_runner(
            Path(current.native_cli.executable),
            list(argv),
            Path(current.native_cli.cwd),
            dict(current.native_cli.env),
            b"" if stdin is None else stdin.encode("utf-8"),
        )
        return NativeCliResult.from_process(result)

    async def discover_sessions(
        self, *, project_id: int, data_dir: Path | None = None
    ) -> dict[str, NativeSessionState]:
        """Inspect deterministic StackOS state only; ambient gstack is never adopted."""
        root = self._resolve_data_dir(data_dir)
        project_root = root / "browser-state" / "gstack" / f"project-{project_id}"
        if not project_root.is_dir():
            return {}
        found: dict[str, NativeSessionState] = {}
        for state_file in project_root.glob("*/*/.gstack/browse.json"):
            profile_key = safe_browser_key(state_file.parents[2].name)
            session_key = safe_browser_key(state_file.parents[1].name)
            profile_ref = f"browser-profile:project-{project_id}:{profile_key}"
            session_ref = f"browser-session:project-{project_id}:{profile_key}:{session_key}"
            found[session_ref] = await self.inspect_session(
                session_ref=session_ref,
                profile_ref=profile_ref,
                profile_dir=browser_profile_dir(
                    root, project_id=project_id, profile_key=profile_key
                ),
                state_file=state_file,
                data_dir=root,
            )
        return found

    def _resolve_data_dir(self, data_dir: Path | None) -> Path:
        return Path(data_dir) if data_dir is not None else _data_dir(self._data_dir)

    def _require_runtime(self, data_dir: Path) -> None:
        if not _runtime_paths_ready(data_dir):
            raise ValidationError(
                "StackOS gstack runtime is not installed",
                data={"provider": BROWSER_PROVIDER, "repair": GSTACK_RUNTIME_REPAIR},
            )

    def _require_executable(self, data_dir: Path) -> Path:
        executable = gstack_executable_path(data_dir)
        if executable is None:
            self._require_runtime(data_dir)
            raise AssertionError("gstack runtime readiness did not provide an executable")
        return executable

    async def _is_healthy(self, port: int) -> bool:
        result = self._health_probe(port)
        return bool(await result) if inspect.isawaitable(result) else bool(result)

    def _matches_owned_identity(
        self, identity: ProcessIdentity | None, state: _GstackStateFile, data_dir: Path
    ) -> bool:
        expected_server = gstack_server_path(data_dir)
        if identity is None or expected_server is None:
            return False
        if (
            state.server_path != expected_server.resolve()
            or str(expected_server.resolve()) not in identity.command
        ):
            return False
        offset = state.started_at - identity.started_at.astimezone(UTC)
        return timedelta(seconds=-5) <= offset <= _IDENTITY_START_TOLERANCE

    def _identity_is_alive(self, state: _GstackStateFile, data_dir: Path) -> bool:
        return self._matches_owned_identity(
            self._process_inspector.inspect(state.pid), state, data_dir
        )

    async def _other_profile_owner(
        self,
        *,
        session_ref: str,
        profile_ref: str,
        profile_dir: Path,
        state_file: Path,
        data_dir: Path,
    ) -> NativeSessionState | None:
        profile_state_root = state_file.parents[2]
        if not profile_state_root.is_dir():
            return None
        prefix, _separator, _session_key = session_ref.rpartition(":")
        for candidate in profile_state_root.glob("*/.gstack/browse.json"):
            if candidate == state_file:
                continue
            owner = await self.inspect_session(
                session_ref=f"{prefix}:{safe_browser_key(candidate.parents[1].name)}",
                profile_ref=profile_ref,
                profile_dir=profile_dir,
                state_file=candidate,
                data_dir=data_dir,
            )
            if owner.owned:
                return owner
        return None

    def _native_context(
        self, *, profile_dir: Path, state_file: Path, data_dir: Path, include_no_autostart: bool
    ) -> NativeCliContext:
        return NativeCliContext(
            executable=str(self._require_executable(data_dir)),
            cwd=str(gstack_session_cwd(state_file)),
            env=self._native_env(
                profile_dir=profile_dir,
                state_file=state_file,
                data_dir=data_dir,
                include_no_autostart=include_no_autostart,
            ),
        )

    def _native_env(
        self, *, profile_dir: Path, state_file: Path, data_dir: Path, include_no_autostart: bool
    ) -> dict[str, str]:
        root = gstack_runtime_root(data_dir)
        env = {
            "BROWSE_STATE_FILE": str(state_file),
            "CHROMIUM_PROFILE": str(profile_dir),
            "BROWSE_HEADED": "1",
            "BROWSE_PARENT_PID": "0",
            "GSTACK_HOME": str(gstack_session_cwd(state_file) / "gstack-home"),
            "PLAYWRIGHT_BROWSERS_PATH": str(root / "browsers"),
            "PATH": os.pathsep.join([str(root / "bin"), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
        }
        if include_no_autostart:
            env["BROWSE_NO_AUTOSTART"] = "1"
        return env

    @staticmethod
    def _prepare_owned_paths(*, profile_dir: Path, state_file: Path) -> None:
        for path in (profile_dir, gstack_session_cwd(state_file), state_file.parent):
            path.mkdir(parents=True, exist_ok=True)
            os.chmod(path, 0o700)

    def _state_retired(self, state_file: Path, before: _GstackStateFile) -> bool:
        current = _read_state_file(state_file)
        if current is None:
            return True
        return not (
            current.pid == before.pid
            and current.started_at == before.started_at
            and current.server_path == before.server_path
        )

    @staticmethod
    def _stale(session_ref: str, profile_ref: str, repair: str) -> NativeSessionState:
        return NativeSessionState(
            session_ref=session_ref,
            profile_ref=profile_ref,
            status="stale",
            owned=False,
            healthy=False,
            pid=None,
            repair=repair,
        )


_RUNTIME = BrowserRuntime()


def get_browser_runtime() -> BrowserRuntime:
    return _RUNTIME


__all__ = [
    "BROWSER_PROFILE_DIRNAME",
    "BROWSER_PROVIDER",
    "GSTACK_RUNTIME_DIRNAME",
    "GSTACK_RUNTIME_REPAIR",
    "BrowserRuntime",
    "NativeCliContext",
    "NativeCliResult",
    "NativeProcessResult",
    "NativeSessionState",
    "ProcessIdentity",
    "RuntimeStatus",
    "browser_profile_dir",
    "get_browser_runtime",
    "gstack_browser_assets_path",
    "gstack_bun_path",
    "gstack_executable_path",
    "gstack_manifest_path",
    "gstack_runtime_root",
    "gstack_server_path",
    "gstack_session_cwd",
    "gstack_state_file",
    "packaged_stackos_root",
    "safe_browser_key",
]
