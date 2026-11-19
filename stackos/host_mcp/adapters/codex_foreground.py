"""Attach the stock Codex TUI to its verified, invocation-owned native backend."""

from __future__ import annotations

from contextlib import ExitStack
from time import sleep
from typing import Any
from uuid import UUID

from stackos.host_mcp.accounts import NativeIdentity
from stackos.host_mcp.adapters.codex_accounts import (
    CodexAccountError,
    _identity,
    ordinary_launch,
    selected_launch,
)
from stackos.host_mcp.adapters.codex_app_server import open_app_server
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
            native["config"] = {"web_search": "live"}
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


def _thread_id(value: object) -> str:
    if isinstance(value, str):
        try:
            if str(UUID(value)) == value:
                return value
        except ValueError:
            pass
    raise ForegroundError("Supply the exact native Codex conversation UUID.")


class CodexForeground:
    def prepare(self, request: ForegroundRequest) -> ForegroundLaunch:
        flags, settings = _native_flags(request.native_args)
        resumed = _thread_id(request.session_id) if request.session_id is not None else None
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
            thread_id = resumed
            admitted = False

            def prepare_client() -> tuple[tuple[str, ...], ForegroundObservation]:
                nonlocal thread_id
                parameters = {"cwd": str(request.cwd), **settings}
                if resumed is None:
                    reply = server.call("thread/start", {**parameters, "ephemeral": False})
                else:
                    reply = server.call(
                        "thread/resume", {**parameters, "threadId": resumed, "excludeTurns": True}
                    )
                thread_id = _thread_id((reply.get("thread") or {}).get("id"))
                if resumed is not None and thread_id != resumed:
                    raise ForegroundError("The private Codex backend returned a different thread.")
                return (
                    (request.executable, "--remote", endpoint, "resume", *flags, thread_id),
                    ForegroundObservation(thread_id, principal),
                )

            def decide_input(allowed: bool) -> None:
                nonlocal admitted
                admitted = allowed

            def settled() -> bool:
                if thread_id is None:
                    return True
                record = server.call(
                    "thread/read", {"threadId": thread_id, "includeTurns": False}
                ).get("thread")
                if not isinstance(record, dict) or record.get("id") != thread_id:
                    raise ForegroundError("The private Codex backend lost the exact conversation.")
                state = record.get("status")
                return isinstance(state, dict) and state.get("type") in {
                    "idle",
                    "notLoaded",
                    "systemError",
                }

            def observe(_pid: int, exit_code: int | None) -> ForegroundObservation:
                # The child can only attach to this private endpoint at the
                # exact UUID. Query the same native backend on retirement.
                return ForegroundObservation(
                    thread_id, principal, admitted and exit_code is not None and settled()
                )

            def close() -> None:
                try:
                    if admitted:
                        # Stock remote TUI may disconnect while a native turn is
                        # still running. Keep ownership and the credential lease
                        # until it settles rather than interrupting pending work.
                        while not settled():
                            sleep(0.25)
                except Exception:
                    raise ForegroundError("Unable to confirm Codex work has settled.") from None
                finally:
                    resources.close()

            return ForegroundLaunch(
                (request.executable,),
                env,
                observe,
                decide_input,
                before_spawn=prepare_client,
                close=close,
                inherited_fds=lease_fds,
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
