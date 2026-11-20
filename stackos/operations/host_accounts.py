"""Private local-console operations for native harness accounts."""

from __future__ import annotations

from asyncio import to_thread
from collections.abc import Callable
from typing import Any, Literal

from pydantic import Field

from stackos.config import Settings
from stackos.host_mcp import service
from stackos.host_mcp.accounts import (
    AccountRef,
    AccountView,
    DesiredAccount,
    EnrollmentView,
    HostAccountError,
    HostAccountInventory,
    HostAccounts,
)
from stackos.logging import get_logger
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.errors import ToolNotGrantedError
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.spec import (
    OperationExample,
    OperationHandler,
    OperationResponsePolicy,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.base import ConflictError, ValidationError

_log = get_logger(__name__)
_HOST_NAMES = {"codex": "Codex", "claude-code": "Claude Code", "grok": "Grok"}
_IDENTITY_MESSAGES = {
    "needs_sign_in": (
        "Sign in using the native client, then refresh accounts. "
        "If the saved profile is missing, add the account again."
    ),
    "expired": "Native sign-in has expired. Sign in again, then refresh accounts.",
    "missing_email": (
        "Native verification did not provide an email. Complete native sign-in, "
        "then refresh accounts."
    ),
    "conflicting_credentials": (
        "Conflicting native credentials prevent verification. Resolve competing "
        "credential settings in the native client, then refresh accounts."
    ),
    "unavailable": (
        "Native account verification is unavailable. Check that the native client "
        "is available and supports the account protocol, then refresh accounts."
    ),
    "identity_changed": (
        "The native account identity changed. Refresh accounts and verify the intended "
        "sign-in before selecting it again."
    ),
}
_ACCOUNT_MESSAGES = {
    "identity_unverified": (
        "The native account could not be verified. Sign in, then refresh accounts."
    ),
    "activation_failed": (
        "Could not save or activate the native account. Check native credential storage "
        "and sign-in, then refresh accounts before retrying."
    ),
    "selection_restore_failed": (
        "Native account restoration could not be confirmed. Check the native client's "
        "current sign-in before retrying or starting work."
    ),
    "profile_missing": (
        "The saved account profile is missing. Add the account again before selecting it."
    ),
    "unsafe_profile": (
        "The saved account profile failed its safety checks. Check its ownership and "
        "permissions before retrying."
    ),
    "account_store_unavailable": (
        "Saved accounts could not be read or updated safely. Check local account-store "
        "access and permissions, then refresh accounts."
    ),
    "unsupported_host": "Account management is not supported for this host. Use its native client.",
    "unknown_account": (
        "The saved account was not found. Refresh accounts and select an enrolled account."
    ),
    "unknown_enrollment": (
        "The sign-in attempt was not found. Refresh accounts before starting sign-in again."
    ),
    "stale_selection": "The selected account changed. Refresh accounts before selecting again.",
    "enrollment_pending": (
        "A sign-in attempt is already pending. Complete or cancel it before adding an account."
    ),
    "default_profile_changed": (
        "The native default identity changed. Refresh accounts and verify the intended "
        "sign-in before adopting it again."
    ),
    "account_switch_busy": "An account switch is already in progress. Try again shortly.",
    "account_switch_unresumable_worker": (
        "An automated Codex process cannot yet resume its saved conversation. "
        "Let it finish before switching accounts."
    ),
    "account_switch_retirement_unconfirmed": (
        "An automated Codex process has not stopped. The account was not changed. "
        "Check the running task before trying again."
    ),
}


class AccountConsoleInput(MCPInput):
    response_mode: Literal["raw", "standard", "verbose"] = "raw"


class AccountHostInput(AccountConsoleInput):
    host_key: str = Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")


class AccountEnrollmentInput(AccountHostInput):
    enrollment_ref: AccountRef


class AccountRefInput(AccountHostInput):
    account_ref: AccountRef


class AccountSelectInput(AccountRefInput):
    expected_revision: int = Field(ge=0, strict=True)


def _accounts(inp: AccountConsoleInput, ctx: MCPContext) -> HostAccounts:
    if (
        ctx.extras.get("surface") != "rest"
        or ctx.extras.get("trusted_local_admin") is not True
        or ctx.extras.get("client_surface") not in {None, "rest"}
        or inp.run_token is not None
    ):
        raise ToolNotGrantedError("Harness accounts require a local operator REST session.")
    settings = ctx.extras.get("settings")
    if not isinstance(settings, Settings):
        raise ValidationError("Local harness account settings are unavailable.")
    return service.host_accounts(settings.data_dir)


async def _call[T](
    fn: Callable[..., T], *args: Any, operation: str, host_key: str | None = None
) -> T:
    try:
        return await to_thread(fn, *args)
    except HostAccountError as exc:
        error = (
            ConflictError
            if exc.code
            in {
                "stale_selection",
                "enrollment_pending",
                "default_profile_changed",
                "account_switch_busy",
                "account_switch_unresumable_worker",
                "account_switch_retirement_unconfirmed",
            }
            else ValidationError
        )
        # Only closed diagnostics cross this boundary, never native exception text.
        code = exc.code if exc.code in _ACCOUNT_MESSAGES else "account_error"
        status = exc.status if exc.status in {*_IDENTITY_MESSAGES, "verified"} else None
        data = {"code": code}
        if status is not None:
            data["identity_status"] = status
        message = _ACCOUNT_MESSAGES.get(
            code, "Account action failed. Refresh accounts before retrying."
        )
        if code == "identity_unverified" and status is not None:
            message = _IDENTITY_MESSAGES.get(status, message)
        label = _HOST_NAMES.get(host_key or "", "Harness accounts")
        message = f"{label}: {message}"
        _log.info(
            "host_account.rejected",
            operation=operation,
            host_key=host_key if host_key in _HOST_NAMES else None,
            reason=code,
            identity_status=status,
            http_status=error.http_status,
        )
        raise error(message, data=data) from None


async def _list(
    inp: AccountConsoleInput, ctx: MCPContext, _p: ProgressEmitter
) -> HostAccountInventory:
    return await _call(_accounts(inp, ctx).inventory, operation="hostMcp.listAccounts")


async def _adopt(
    inp: AccountHostInput, ctx: MCPContext, _p: ProgressEmitter
) -> WriteEnvelope[AccountView]:
    return WriteEnvelope(
        data=await _call(
            _accounts(inp, ctx).adopt,
            inp.host_key,
            operation="hostMcp.adoptAccount",
            host_key=inp.host_key,
        )
    )


async def _add(
    inp: AccountEnrollmentInput, ctx: MCPContext, _p: ProgressEmitter
) -> WriteEnvelope[EnrollmentView]:
    data = await _call(
        _accounts(inp, ctx).add,
        inp.host_key,
        inp.enrollment_ref,
        operation="hostMcp.addAccount",
        host_key=inp.host_key,
    )
    return WriteEnvelope(data=data)


async def _complete(
    inp: AccountEnrollmentInput, ctx: MCPContext, _p: ProgressEmitter
) -> WriteEnvelope[EnrollmentView]:
    data = await _call(
        _accounts(inp, ctx).complete,
        inp.host_key,
        inp.enrollment_ref,
        operation="hostMcp.completeAccountEnrollment",
        host_key=inp.host_key,
    )
    return WriteEnvelope(data=data)


async def _cancel(
    inp: AccountEnrollmentInput, ctx: MCPContext, _p: ProgressEmitter
) -> WriteEnvelope[EnrollmentView]:
    data = await _call(
        _accounts(inp, ctx).cancel,
        inp.host_key,
        inp.enrollment_ref,
        operation="hostMcp.cancelAccountEnrollment",
        host_key=inp.host_key,
    )
    return WriteEnvelope(data=data)


async def _select(
    inp: AccountSelectInput, ctx: MCPContext, _p: ProgressEmitter
) -> WriteEnvelope[DesiredAccount]:
    data = await _call(
        service.select_account,
        _accounts(inp, ctx),
        inp.host_key,
        inp.account_ref,
        inp.expected_revision,
        operation="hostMcp.selectAccount",
        host_key=inp.host_key,
    )
    return WriteEnvelope(data=data)


async def _refresh(
    inp: AccountRefInput, ctx: MCPContext, _p: ProgressEmitter
) -> WriteEnvelope[AccountView]:
    return WriteEnvelope(
        data=await _call(
            _accounts(inp, ctx).refresh,
            inp.host_key,
            inp.account_ref,
            operation="hostMcp.refreshAccount",
            host_key=inp.host_key,
        )
    )


def _spec(
    verb: str,
    summary: str,
    input_model: type[AccountConsoleInput],
    output_model: type,
    handler: OperationHandler,
    arguments: dict[str, Any],
    *,
    mutating: bool = True,
) -> OperationSpec:
    name = f"hostMcp.{verb}"
    return OperationSpec(
        name=name,
        summary=summary,
        input_model=input_model,
        output_model=output_model,
        handler=handler,
        mutating=mutating,
        category="system",
        grant_policy="local-admin-console-write" if mutating else "local-admin-console-read",
        surfaces=OperationSurfaces(
            mcp=OperationSurface(enabled=False),
            cli=OperationSurface(enabled=False),
            rest=OperationSurface(
                enabled=True, browser_safe=True, path=f"/api/v1/operations/{name}/call"
            ),
        ),
        response_policy=OperationResponsePolicy(
            default_mode="raw",
            allowed_modes=("raw",),
            raw_only_reason="The local console uses the full allowlisted state.",
        ),
        idempotency_policy="handler",
        purpose="Manage native harness accounts through the private local operator console.",
        examples=(OperationExample(title=summary, arguments=arguments),),
    )


def operation_specs() -> list[OperationSpec]:
    host = {"host_key": "codex"}
    enrollment = {**host, "enrollment_ref": "01866872-5b2a-4eb9-913a-00317acaf362"}
    account = {**host, "account_ref": "a6f3f6f5-4c8c-4330-aff7-fd43342f3c24"}
    return [
        _spec(
            "listAccounts",
            "List switch-capable hosts and local enrolled accounts.",
            AccountConsoleInput,
            HostAccountInventory,
            _list,
            {},
            mutating=False,
        ),
        _spec(
            "adoptAccount",
            "Verify and enroll the native default account without logging out.",
            AccountHostInput,
            WriteEnvelope[AccountView],
            _adopt,
            host,
        ),
        _spec(
            "addAccount",
            "Begin native sign-in in an isolated provisional profile.",
            AccountEnrollmentInput,
            WriteEnvelope[EnrollmentView],
            _add,
            enrollment,
        ),
        _spec(
            "completeAccountEnrollment",
            "Check native sign-in and enroll a verified identity.",
            AccountEnrollmentInput,
            WriteEnvelope[EnrollmentView],
            _complete,
            enrollment,
        ),
        _spec(
            "cancelAccountEnrollment",
            "Close a provisional enrollment and retire its native login.",
            AccountEnrollmentInput,
            WriteEnvelope[EnrollmentView],
            _cancel,
            enrollment,
        ),
        _spec(
            "selectAccount",
            "Activate the saved login and resume affected automated Codex conversations.",
            AccountSelectInput,
            WriteEnvelope[DesiredAccount],
            _select,
            {**account, "expected_revision": 0},
        ),
        _spec(
            "refreshAccount",
            "Recheck the enrolled identity and update its display email if matched.",
            AccountRefInput,
            WriteEnvelope[AccountView],
            _refresh,
            account,
        ),
    ]
