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


async def _call[T](fn: Callable[..., T], *args: str | int) -> T:
    try:
        return await to_thread(fn, *args)
    except HostAccountError as exc:
        error = (
            ConflictError
            if exc.code in {"stale_selection", "enrollment_pending", "default_profile_changed"}
            else ValidationError
        )
        data = {"code": exc.code}
        if exc.status is not None:
            data["identity_status"] = exc.status
        raise error("Harness account action could not complete.", data=data) from None


async def _list(
    inp: AccountConsoleInput, ctx: MCPContext, _p: ProgressEmitter
) -> HostAccountInventory:
    return await _call(_accounts(inp, ctx).inventory)


async def _adopt(
    inp: AccountHostInput, ctx: MCPContext, _p: ProgressEmitter
) -> WriteEnvelope[AccountView]:
    return WriteEnvelope(data=await _call(_accounts(inp, ctx).adopt, inp.host_key))


async def _add(
    inp: AccountEnrollmentInput, ctx: MCPContext, _p: ProgressEmitter
) -> WriteEnvelope[EnrollmentView]:
    data = await _call(_accounts(inp, ctx).add, inp.host_key, inp.enrollment_ref)
    return WriteEnvelope(data=data)


async def _complete(
    inp: AccountEnrollmentInput, ctx: MCPContext, _p: ProgressEmitter
) -> WriteEnvelope[EnrollmentView]:
    data = await _call(_accounts(inp, ctx).complete, inp.host_key, inp.enrollment_ref)
    return WriteEnvelope(data=data)


async def _cancel(
    inp: AccountEnrollmentInput, ctx: MCPContext, _p: ProgressEmitter
) -> WriteEnvelope[EnrollmentView]:
    data = await _call(_accounts(inp, ctx).cancel, inp.host_key, inp.enrollment_ref)
    return WriteEnvelope(data=data)


async def _select(
    inp: AccountSelectInput, ctx: MCPContext, _p: ProgressEmitter
) -> WriteEnvelope[DesiredAccount]:
    data = await _call(
        _accounts(inp, ctx).select, inp.host_key, inp.account_ref, inp.expected_revision
    )
    return WriteEnvelope(data=data)


async def _refresh(
    inp: AccountRefInput, ctx: MCPContext, _p: ProgressEmitter
) -> WriteEnvelope[AccountView]:
    return WriteEnvelope(
        data=await _call(_accounts(inp, ctx).refresh, inp.host_key, inp.account_ref)
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
            "Verify and save a desired account at the expected revision.",
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
