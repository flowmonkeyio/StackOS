"""Typer app assembly for the StackOS CLI."""

from __future__ import annotations

from typing import Annotated

import typer

from stackos import __milestone__, __version__

app = typer.Typer(
    name="stackos",
    help=(
        "StackOS daemon CLI — `serve` runs the daemon, `doctor` diagnoses an "
        "install, and operation aliases call the shared dispatcher."
    ),
    no_args_is_help=True,
    add_completion=False,
)

ops_app = typer.Typer(
    name="ops",
    help="Inspect and call registered StackOS operations through the daemon REST adapter.",
    no_args_is_help=True,
)
app.add_typer(ops_app, name="ops")

actions_app = typer.Typer(
    name="actions",
    help="Describe, validate, and execute action operations through the shared dispatcher.",
    no_args_is_help=True,
)
app.add_typer(actions_app, name="actions")

run_plans_app = typer.Typer(
    name="run-plans",
    help="Create, start, inspect, and step through StackOS run plans.",
    no_args_is_help=True,
)
app.add_typer(run_plans_app, name="run-plans")

agent_requests_app = typer.Typer(
    name="agent-requests",
    help="List, claim, and resolve generic StackOS agent request queue items.",
    no_args_is_help=True,
)
app.add_typer(agent_requests_app, name="agent-requests")

tracker_app = typer.Typer(
    name="tracker",
    help="Inspect and update the project task tracker through shared operations.",
    no_args_is_help=True,
)
app.add_typer(tracker_app, name="tracker")

autostart_app = typer.Typer(
    name="autostart",
    help="Install, inspect, or remove local daemon autostart.",
    no_args_is_help=True,
)
app.add_typer(autostart_app, name="autostart")

backups_app = typer.Typer(
    name="backups",
    help="Configure encrypted local backups and independent recovery-key custody.",
    no_args_is_help=True,
)
app.add_typer(backups_app, name="backups")


def _exit(code: int) -> None:
    """Exit via typer.Exit so context managers (e.g. uvicorn) unwind cleanly."""
    raise typer.Exit(code=code)


def _print_version(value: bool) -> None:
    """Eager `--version` callback — short-circuits before subcommand resolution."""
    if value:
        typer.echo(f"stackos {__version__} ({__milestone__})")
        raise typer.Exit(code=0)


@app.callback()
def _root(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            help="Print version and exit",
            callback=_print_version,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Top-level options. `--version` short-circuits the help-on-no-args flow."""
    _ = ctx
    _ = version


__all__ = [
    "_exit",
    "actions_app",
    "agent_requests_app",
    "app",
    "autostart_app",
    "ops_app",
    "run_plans_app",
    "tracker_app",
]
