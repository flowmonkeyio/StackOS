"""One owned Codex app-server connection for first-party browser login."""

from __future__ import annotations

import json
import os
import select
import signal
import socket as sockets
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep

from websockets.sync.client import unix_connect
from websockets.sync.connection import Connection

from stackos.host_mcp.agent_execution import ManagedProcessControl


def _watch_backend(argv: list[str], inherited: tuple[int, ...], lease_channel: int) -> int:
    """Retire the exact child when its supervisor's private pipe closes."""
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)
    control = ManagedProcessControl()
    process = control.spawn(argv, stdin=subprocess.DEVNULL, pass_fds=inherited)
    with sockets.socket(fileno=lease_channel) as channel, ExitStack() as retained:
        channel_fd = channel.fileno()
        try:
            while not stopping and process.poll() is None:
                ready, _, _ = select.select([0, channel_fd], [], [], 0.1)
                if 0 in ready and not os.read(0, 1):
                    break
                if channel_fd in ready:
                    data, descriptors, _, _ = sockets.recv_fds(channel, 1, 1)
                    for descriptor in descriptors:
                        retained.callback(os.close, descriptor)
                    if data != b"p" or len(descriptors) != 1:
                        break
                    channel.sendall(b"p")
        finally:
            retired = control.stop()
        return 0 if retired == "stopped" else 1


class CodexProtocolError(Exception):
    """A safe native transport diagnostic."""


class CodexAppServer:
    def __init__(
        self,
        connection: Connection,
        retain_lease: Callable[[int], None] | None = None,
    ):
        self.connection = connection
        self.retain_lease = retain_lease
        self.request_id = 0
        self.notifications: list[dict] = []

    def _receive(self, timeout: float) -> dict:
        try:
            message = json.loads(self.connection.recv(timeout=max(0, timeout)))
        except ValueError:
            raise CodexProtocolError("Invalid response from the native Codex backend.") from None
        if not isinstance(message, dict):
            raise CodexProtocolError("Invalid response from the native Codex backend.")
        return message

    def call(self, method: str, params: dict, timeout: float = 10) -> dict:
        self.request_id += 1
        expected_id = self.request_id
        self.connection.send(json.dumps({"id": expected_id, "method": method, "params": params}))
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            message = self._receive(deadline - monotonic())
            if message.get("id") == expected_id and "method" not in message:
                result = message.get("result")
                if isinstance(result, dict):
                    return result
                raise CodexProtocolError("The native Codex backend refused the request.")
            if "method" in message:
                self.notifications.append(message)
                del self.notifications[:-32]
        raise CodexProtocolError("The native Codex backend did not answer in time.")

    def wait_for_login(self, login_id: str, *, timeout: float) -> bool:
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            pending = (
                self.notifications.pop(0)
                if self.notifications
                else self._receive(deadline - monotonic())
            )
            params = pending.get("params")
            if (
                pending.get("method") == "account/login/completed"
                and isinstance(params, dict)
                and params.get("loginId") == login_id
            ):
                return params.get("success") is True
        return False


@contextmanager
def open_app_server(
    binary: str,
    cwd: Path,
    env: dict[str, str],
    control: ManagedProcessControl,
    *,
    overrides: tuple[str, ...] = (),
    pass_fds: tuple[int, ...] = (),
) -> Iterator[tuple[CodexAppServer, str]]:
    parent, child = sockets.socketpair()
    with parent, child:
        yield from _launch_server(binary, cwd, env, control, overrides, pass_fds, parent, child)


def _launch_server(
    binary: str,
    cwd: Path,
    env: dict[str, str],
    control: ManagedProcessControl,
    overrides: tuple[str, ...],
    pass_fds: tuple[int, ...],
    parent: sockets.socket,
    child: sockets.socket,
) -> Iterator[tuple[CodexAppServer, str]]:
    # AF_UNIX paths have a small byte limit on macOS. The private directory
    # provides an isolated endpoint, including when the shared daemon is active.
    with TemporaryDirectory(prefix="stackos-codex-", dir="/tmp") as temporary:
        socket = Path(temporary) / "rpc.sock"
        endpoint = f"unix://{socket}"
        process = control.spawn(
            [
                sys.executable,
                "-E",
                str(Path(__file__).resolve()),
                ",".join(map(str, pass_fds)),
                str(child.fileno()),
                binary,
                "app-server",
                "--listen",
                endpoint,
                *overrides,
            ],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(*pass_fds, child.fileno()),
        )
        child.close()
        parent.settimeout(3)

        def retain_lease(fd: int) -> None:
            try:
                sockets.send_fds(parent, [b"p"], [fd])
                if parent.recv(1) == b"p":
                    return
            except OSError:
                pass
            raise CodexProtocolError("The native lease owner could not retain the conversation.")

        try:
            deadline = monotonic() + 5
            while not socket.exists():
                if process.poll() is not None or monotonic() >= deadline:
                    raise CodexProtocolError("The private Codex backend could not start.")
                sleep(0.02)
            with unix_connect(
                str(socket), proxy=None, open_timeout=3, close_timeout=1, max_size=1_048_576
            ) as connection:
                session = CodexAppServer(connection, retain_lease)
                session.call(
                    "initialize",
                    {
                        "clientInfo": {"name": "stackos_codex_accounts", "version": "1"},
                        "capabilities": {"experimentalApi": True},
                    },
                )
                connection.send('{"method":"initialized"}')
                yield session, endpoint
        finally:
            assert process.stdin is not None
            process.stdin.close()
            try:
                result = process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                raise CodexProtocolError(
                    "The private Codex backend could not be retired."
                ) from None
            if result != 0 or control.retirement_status() != "stopped":
                raise CodexProtocolError("The private Codex backend could not be retired.")


if __name__ == "__main__":
    descriptors = tuple(int(item) for item in sys.argv[1].split(",") if item)
    raise SystemExit(_watch_backend(sys.argv[3:], descriptors, int(sys.argv[2])))
