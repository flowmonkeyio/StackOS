"""Local operator commands for a native foreground harness."""

from __future__ import annotations

import os
from typing import Annotated

import typer

from stackos.cli.app import app
from stackos.config import Settings
from stackos.host_mcp import service
from stackos.host_mcp.accounts import HostAccountError
from stackos.host_mcp.foreground import (
    ForegroundError,
    ForegroundSlot,
    require_foreground_terminal,
    run_foreground,
)

harness_app = typer.Typer(help="Run and inspect a native foreground harness.", no_args_is_help=True)
app.add_typer(harness_app, name="harness")

HostKey = Annotated[str, typer.Argument(help="Native harness: codex, claude-code, or grok.")]


@harness_app.command("run")
def harness_run(
    host: HostKey,
    session_id: Annotated[
        str | None, typer.Option("--session", help="Adopt this exact existing native conversation.")
    ] = None,
    native_args: Annotated[
        list[str] | None, typer.Argument(help="Native configuration options after --.")
    ] = None,
) -> None:
    """Start the stock CLI in this terminal and working directory."""
    try:
        definition = service.foreground_host(host)
        if definition is None or definition.foreground_binary is None:
            raise ForegroundError("Choose one of codex, claude-code, or grok.")
        require_foreground_terminal()
        executable = definition.foreground_binary()
        if executable is None:
            raise ForegroundError("The native CLI was not found by host discovery.")
        settings = Settings()
        accounts = service.host_accounts(settings.data_dir)
        args = tuple(native_args or ())
        adapter = definition.foreground_adapter
        if adapter is None:
            if session_id is not None or accounts.resolve(host).desired.account_ref is not None:
                raise ForegroundError("Managed exact-session launch is unavailable for this host.")
            os.execv(executable, (executable, *args))
        code = run_foreground(
            host_key=host,
            executable=executable,
            adapter=adapter,
            accounts=accounts,
            slot=ForegroundSlot(settings.data_dir, host),
            native_args=args,
            session_id=session_id,
            notice=lambda message: typer.echo(message, err=True),
        )
    except (ForegroundError, HostAccountError, OSError) as exc:
        _fail(exc)
    else:
        raise typer.Exit(code)


@harness_app.command("status")
def harness_status(host: HostKey) -> None:
    """Show the requested revision and last confirmation away from the native screen."""
    try:
        if service.foreground_host(host) is None:
            raise ForegroundError("Choose one of codex, claude-code, or grok.")
        settings = Settings()
        selection = service.host_accounts(settings.data_dir).resolve(host)
        status = ForegroundSlot(settings.data_dir, host).status(selection.desired.revision)
        typer.echo(status.model_dump_json())
    except (ForegroundError, HostAccountError, OSError) as exc:
        _fail(exc)


def _fail(error: ForegroundError | HostAccountError | OSError) -> None:
    if isinstance(error, OSError):
        message = "Unable to access foreground state or start the native CLI."
    elif isinstance(error, HostAccountError):
        message = f"Unable to resolve local harness state ({error.code})."
    else:
        message = str(error)
    typer.echo(message, err=True)
    raise typer.Exit(1)
