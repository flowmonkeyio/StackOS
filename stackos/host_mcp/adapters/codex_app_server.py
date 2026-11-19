"""One owned Codex app-server connection for first-party browser login."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep

from websockets.sync.client import unix_connect
from websockets.sync.connection import Connection

from stackos.host_mcp.agent_execution import ManagedProcessControl


class CodexProtocolError(Exception):
    """A safe native transport diagnostic."""


class CodexAppServer:
    def __init__(self, connection: Connection):
        self.connection = connection
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
    # AF_UNIX paths have a small byte limit on macOS. The private directory
    # provides an isolated endpoint, including when the shared daemon is active.
    with TemporaryDirectory(prefix="stackos-codex-", dir="/tmp") as temporary:
        socket = Path(temporary) / "rpc.sock"
        endpoint = f"unix://{socket}"
        process = control.spawn(
            [binary, "app-server", "--listen", endpoint, *overrides],
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=pass_fds,
        )
        try:
            deadline = monotonic() + 5
            while not socket.exists():
                if process.poll() is not None or monotonic() >= deadline:
                    raise CodexProtocolError("The private Codex backend could not start.")
                sleep(0.02)
            with unix_connect(
                str(socket), proxy=None, open_timeout=3, close_timeout=1, max_size=1_048_576
            ) as connection:
                session = CodexAppServer(connection)
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
            if control.stop() != "stopped":
                raise CodexProtocolError("The private Codex backend could not be retired.")
