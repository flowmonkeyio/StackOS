"""Attach the stock Codex TUI to its verified, invocation-owned native backend."""

from __future__ import annotations

import tomllib
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from time import sleep
from typing import Any

from stackos.host_mcp.accounts import NativeIdentity
from stackos.host_mcp.adapters.codex_accounts import (
    AuthExchange,
    CodexAccountError,
    _home,
    _identity,
    exact_uuid,
    ordinary_launch,
    selected_launch,
)
from stackos.host_mcp.adapters.codex_app_server import CodexProtocolError, open_app_server
from stackos.host_mcp.adapters.codex_tui import TuiAttachment
from stackos.host_mcp.agent_execution import ManagedProcessControl
from stackos.host_mcp.foreground import (
    ForegroundError,
    ForegroundLaunch,
    ForegroundObservation,
    ForegroundRequest,
)

_VALUE_FLAGS = {
    "-m": "model",
    "--model": "model",
    "-s": "sandbox",
    "--sandbox": "sandbox",
    "-a": "approvalPolicy",
    "--ask-for-approval": "approvalPolicy",
}
_AUTO_REVIEW = {
    "sandbox": "workspace-write",
    "approvalPolicy": "on-request",
    "approvalsReviewer": "auto_review",
}
_COMPATIBLE_CONFIG = {
    "model_reasoning_effort",
    "model_reasoning_summary",
    "model_verbosity",
    "web_search",
    "mcp_servers",
}


def _native_flags(args: tuple[str, ...]) -> tuple[tuple[str, ...], dict[str, Any]]:
    """Only retain options that cannot substitute credentials, the backend, or the thread."""
    flags = iter(args)
    tui: list[str] = []
    native: dict[str, Any] = {}
    for token in flags:
        flag, equal, value = token.partition("=")
        if token == "--no-alt-screen":
            tui.append(token)
            continue
        if token == "--search":
            native.setdefault("config", {})["web_search"] = "live"
            continue
        if flag in ("-c", "--config"):
            expression = value if equal else next(flags, "")
            key, separator, raw = expression.partition("=")
            if separator and key.strip().split(".", 1)[0] in _COMPATIBLE_CONFIG and raw:
                try:
                    parsed = tomllib.loads("value = " + raw)
                except tomllib.TOMLDecodeError:
                    parsed = {"value": raw}
                if set(parsed) == {"value"}:
                    native.setdefault("config", {})[key.strip()] = parsed["value"]
                    continue
        if token == "--approve-for-me" and not (_AUTO_REVIEW.keys() & native.keys()):
            native.update(_AUTO_REVIEW)
            continue
        if flag in _VALUE_FLAGS:
            operand = value if equal else next(flags, "")
            key = _VALUE_FLAGS[flag]
            if operand and not operand.startswith("-") and key not in native:
                native[key] = operand
                continue
        raise ForegroundError("A Codex option conflicts with managed account or session routing.")
    return tuple(tui), native


class CodexForeground:
    def prepare(self, request: ForegroundRequest) -> ForegroundLaunch:
        flags, settings = _native_flags(request.native_args)
        try:
            resumed = exact_uuid(request.session_id) if request.session_id is not None else None
        except CodexAccountError as exc:
            raise ForegroundError(str(exc)) from None
        resources = ExitStack()
        try:
            selection = request.selection
            if selection.desired.account_ref is not None:
                env, fd = resources.enter_context(
                    selected_launch(request.executable, request.cwd, selection)
                )
            else:
                env, fd = resources.enter_context(ordinary_launch(request.executable))
            lease_fds = (fd,)
            writers = resources.enter_context(ExitStack())
            control = ManagedProcessControl()
            server, endpoint = resources.enter_context(
                open_app_server(request.executable, request.cwd, env, control, pass_fds=lease_fds)
            )

            principal = None
            if selection.principal_key is not None:
                identity = _identity(
                    server.call("account/read", {"refreshToken": False}),
                    server.call("accountSession/list", {"refreshWorkspaceMetadata": False}),
                )
                if not isinstance(identity, NativeIdentity) or (
                    identity.principal_key != selection.principal_key
                ):
                    raise ForegroundError(
                        "The private Codex backend did not confirm the selected account."
                    )
                principal = identity.principal_key

            if resumed is not None:
                saved = server.call("thread/read", {"threadId": resumed, "includeTurns": False})
                thread = saved.get("thread") or {}
                if thread.get("id") != resumed:
                    raise ForegroundError("The exact native Codex conversation was not found.")
                if principal is not None and thread.get("modelProvider") != "openai":
                    raise ForegroundError(
                        "The saved Codex conversation uses a different model provider."
                    )
            credential_home = AuthExchange(_home(env), lambda _: None)

            @contextmanager
            def own_thread(thread_id: str) -> Iterator[int]:
                with credential_home.thread_lease(thread_id) as lease:
                    assert server.retain_lease is not None
                    try:
                        server.retain_lease(lease)
                    except CodexProtocolError as exc:
                        raise CodexAccountError(str(exc)) from None
                    yield lease

            attachment = TuiAttachment(endpoint, settings, own_thread, resumed)
            writers.callback(attachment.writers.close)
            if resumed is not None:
                attachment.hold(resumed)
            tui_endpoint = resources.enter_context(attachment.listen())

            def settled() -> bool:
                active, visited = attachment.snapshot()
                for thread_id in visited:
                    try:
                        record = server.call(
                            "thread/read",
                            {"threadId": thread_id, "includeTurns": False},
                        ).get("thread")
                    except Exception:
                        if thread_id == active:
                            raise
                        # A dismissed attachment may have no saved transcript.
                        if thread_id not in server.call("thread/loaded/list", {}).get("data", []):
                            continue
                        raise
                    if not isinstance(record, dict) or record.get("id") != thread_id:
                        raise ForegroundError(
                            "The private Codex backend lost a managed conversation."
                        )
                    state = record.get("status")
                    if not isinstance(state, dict) or state.get("type") not in {
                        "idle",
                        "notLoaded",
                        "systemError",
                    }:
                        return False
                return True

            def wait_for_settlement() -> None:
                try:
                    # The remote TUI can exit before the backend publishes the
                    # final turn status. Keep the account and writer leases
                    # until the native conversation is idle.
                    while not settled():
                        sleep(0.25)
                except Exception:
                    raise ForegroundError("Unable to confirm Codex work has settled.") from None

            def observe(_pid: int, exit_code: int | None) -> ForegroundObservation:
                if exit_code is not None:
                    attachment.disconnected.wait(0.5)
                active, _ = attachment.snapshot()
                retired = attachment.admitted.is_set() and exit_code is not None and exit_code >= 0
                if retired:
                    wait_for_settlement()
                return ForegroundObservation(active, principal, retired)

            def close() -> None:
                try:
                    if attachment.admitted.is_set():
                        wait_for_settlement()
                finally:
                    resources.close()

            return ForegroundLaunch(
                (
                    request.executable,
                    "--remote",
                    tui_endpoint,
                    *(("resume", *flags, resumed) if resumed is not None else flags),
                ),
                env,
                observe,
                attachment.admit,
                close=close,
                inherited_fds=lease_fds,
                tracks_navigation=True,
            )
        except Exception as exc:
            resources.close()
            if isinstance(exc, ForegroundError):
                raise
            if isinstance(exc, CodexAccountError):
                raise ForegroundError(str(exc)) from None
            raise ForegroundError(
                "Unable to prepare a private Codex backend and exact conversation."
            ) from None
