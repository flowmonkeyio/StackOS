"""Observe the stock TUI's own attachment and apply its admitted native options."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager, suppress
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Any

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import unix_connect
from websockets.sync.server import ServerConnection, unix_serve

from stackos.host_mcp.adapters.codex_accounts import CodexAccountError, exact_uuid

_ATTACH = {"thread/start", "thread/resume", "thread/fork"}
_STARTUP = _ATTACH | {
    "initialize",
    "initialized",
    "account/read",
    "accountSession/list",
    "model/list",
    "config/read",
    "configRequirements/read",
    "collaborationMode/list",
    "thread/read",
    "thread/list",
    "thread/loaded/list",
    "skills/list",
    "plugin/list",
    "plugin/installed",
    "app/installed",
    "hooks/list",
    "account/gatewayOAuth/read",
    "account/bedrock/checkGovCloudRequirements",
    "experimentalFeature/list",
    "mcpServerStatus/list",
    "app/list",
    "account/rateLimits/read",
    "account/getRateLimits",
    "thread/items/list",
    "thread/turns/list",
    "thread/goal/get",
    "thread/backgroundTerminals/list",
}
_CREDENTIAL_WRITES = {
    "account/login/start",
    "account/logout",
    "accountSession/switch",
    "account/login/cancel",
    "account/bedrock/setup",
}
_FRAME_LIMIT = 16 * 1024 * 1024


class TuiAttachment:
    """Track attachments acknowledged to one private TUI connection."""

    def __init__(
        self,
        endpoint: str,
        settings: dict[str, Any],
        reserve: Callable[[str], AbstractContextManager[int]],
        initial_thread: str | None = None,
    ) -> None:
        self.endpoint = endpoint.removeprefix("unix://")
        self.settings = settings
        self.reserve = reserve
        self.initial_thread = initial_thread
        self.guard = RLock()
        self.admitted = Event()
        self.closing = Event()
        self.disconnected = Event()
        self.claimed = False
        self.active: str | None = None
        self.threads: set[str] = set()
        self.ephemeral: set[str] = set()
        self.pending: dict[str | int, tuple[str | None, bool]] = {}
        self.writers = ExitStack()

    def snapshot(self) -> tuple[str | None, tuple[str, ...]]:
        with self.guard:
            return self.active, tuple(self.threads)

    def hold(self, thread: str) -> None:
        with self.guard:
            if thread not in self.threads:
                self.writers.enter_context(self.reserve(thread))
                self.threads.add(thread)

    def admit(self, allowed: bool) -> None:
        (self.admitted if allowed else self.closing).set()

    def request(self, message: dict) -> None:
        method = message.get("method")
        request_id = message.get("id")
        if method in _CREDENTIAL_WRITES:
            raise CodexAccountError("Change the Codex account through StackOS.")
        if (
            method in _ATTACH
            and isinstance(request_id, (str, int))
            and not isinstance(request_id, bool)
        ):
            params = message.setdefault("params", {})
            if not isinstance(params, dict):
                raise CodexAccountError("The Codex attachment request is invalid.")
            exact = None
            if method == "thread/resume":
                exact = exact_uuid(params.get("threadId"))
            ephemeral = params.get("ephemeral") is True
            with self.guard:
                if not self.admitted.is_set() and (
                    self.active is not None
                    or self.pending
                    or ephemeral
                    or exact != self.initial_thread
                    or (exact is None and method != "thread/start")
                ):
                    raise CodexAccountError("Wait for the initial Codex conversation to attach.")
                if len(self.pending) >= 128 or request_id in self.pending:
                    raise CodexAccountError("The Codex attachment queue is unavailable.")
                if exact is not None:
                    self.hold(exact)
                self.pending[request_id] = (exact, ephemeral)
            config = self.settings.get("config")
            if config:
                params["config"] = {**(params.get("config") or {}), **config}
            if "sandbox" in self.settings:
                # Codex accepts only one sandbox representation per request.
                params.pop("permissions", None)
            params.update({key: value for key, value in self.settings.items() if key != "config"})
        elif method == "turn/start":
            params = message.get("params")
            if isinstance(params, dict):
                thread_id = exact_uuid(params.get("threadId"))
                with self.guard:
                    if thread_id not in self.ephemeral:
                        self.hold(thread_id)
                for key in ("approvalPolicy", "approvalsReviewer"):
                    if key in self.settings:
                        params[key] = self.settings[key]
                if "sandbox" in self.settings:
                    # A client-local default cannot override an explicitly
                    # admitted thread sandbox on its next turn.
                    params.pop("sandboxPolicy", None)
                    params.pop("permissions", None)

    def response(self, message: dict) -> None:
        request_id = message.get("id")
        if (
            not isinstance(request_id, (str, int))
            or isinstance(request_id, bool)
            or "method" in message
        ):
            return
        with self.guard:
            pending = self.pending.pop(request_id, None)
            if pending is None or "error" in message:
                return
            result = message.get("result")
            thread = result.get("thread") if isinstance(result, dict) else None
            exact = exact_uuid(thread.get("id") if isinstance(thread, dict) else None)
            if pending[0] is not None and exact != pending[0]:
                raise CodexAccountError("The Codex backend returned a different conversation.")
            if pending[1]:
                # The stock TUI uses temporary native conversations for
                # housekeeping such as generation of conversation titles.
                # They have no transcript to adopt or reopen.
                self.ephemeral.add(exact)
                return
            self.hold(exact)
            self.active = exact

    @staticmethod
    def _deny(connection, request_id: object, error: CodexAccountError) -> None:
        if type(request_id) in (int, str):
            connection.send(
                json.dumps(
                    {
                        "id": request_id,
                        "error": {"code": -32000, "message": str(error)},
                    }
                )
            )

    def _handle(self, client: ServerConnection) -> None:
        with self.guard:
            if self.claimed:
                client.close()
                return
            self.claimed = True
        try:
            with unix_connect(
                self.endpoint,
                proxy=None,
                open_timeout=3,
                close_timeout=1,
                max_size=_FRAME_LIMIT,
            ) as backend:

                def replies() -> None:
                    try:
                        for raw in backend:
                            message = json.loads(raw)
                            if isinstance(message, dict):
                                try:
                                    self.response(message)
                                except CodexAccountError as exc:
                                    self._deny(client, message.get("id"), exc)
                                    continue
                            client.send(raw)
                    except (ConnectionClosed, OSError, ValueError):
                        pass
                    finally:
                        client.close()

                reader = Thread(target=replies, daemon=True, name="stackos-codex-tui-replies")
                reader.start()
                try:
                    for raw in client:
                        message = json.loads(raw)
                        if not isinstance(message, dict):
                            break
                        method = message.get("method")
                        if method not in _STARTUP:
                            while not self.admitted.wait(0.1):
                                if self.closing.is_set():
                                    return
                        try:
                            self.request(message)
                        except CodexAccountError as exc:
                            self._deny(client, message.get("id"), exc)
                            continue
                        backend.send(json.dumps(message, separators=(",", ":")))
                except (ConnectionClosed, OSError, ValueError):
                    pass
                finally:
                    backend.close()
                    reader.join()
        except (ConnectionClosed, OSError, TimeoutError):
            pass
        finally:
            self.disconnected.set()

    @contextmanager
    def listen(self) -> Iterator[str]:
        socket = Path(self.endpoint).with_name("tui.sock")
        with unix_serve(
            self._handle, str(socket), close_timeout=1, max_size=_FRAME_LIMIT
        ) as server:
            worker = Thread(target=server.serve_forever, daemon=True, name="stackos-codex-tui")
            worker.start()
            try:
                yield f"unix://{socket}"
            finally:
                self.closing.set()
                server.shutdown()
                worker.join()
                with suppress(FileNotFoundError):
                    socket.unlink()
