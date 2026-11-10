"""Explicit local operator recovery, never an alternative agent permission route."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from sqlmodel import Session, create_engine

from stackos.backups.models import BackupError
from stackos.config import get_settings
from stackos.plugins.finance_recovery import FinanceRecovery
from stackos.repositories.base import RepositoryError

from .app import app
from .daemon_processes import _tcp_can_connect


@app.command("finance-recover")
def finance_recover(
    project_id: Annotated[int, typer.Option(help="Permanent destination project ID.")],
    source: Annotated[
        Path, typer.Option(help="Existing Finance directory in its bound workspace.")
    ],
    apply: Annotated[bool, typer.Option("--apply", help="Apply the reviewed preview.")] = False,
    confirmation_sha256: Annotated[
        str | None, typer.Option(help="Exact confirmation_sha256 from the preview.")
    ] = None,
) -> None:
    """Preview/import an unmapped ledger offline; preserves originals and Finance enablement.

    Local operator recovery only. Never use this command to work around a denied
    agent operation. Stop StackOS before --apply; review the JSON preview first.
    """
    settings = get_settings()
    if not settings.db_path.is_file():
        typer.echo("finance-recover: existing StackOS database required", err=True)
        raise typer.Exit(1)
    if apply and (not confirmation_sha256 or _tcp_can_connect(settings.host, settings.port)):
        typer.echo(
            "finance-recover: stop StackOS and supply the reviewed --confirmation-sha256", err=True
        )
        raise typer.Exit(1)
    if not apply and confirmation_sha256:
        typer.echo("finance-recover: confirmation requires --apply", err=True)
        raise typer.Exit(1)
    # Recovery does not alter SQLite state, plugin enablement or grants.
    engine = create_engine(f"sqlite:///file:{settings.db_path}?mode=ro&uri=true")
    try:
        with Session(engine) as session:
            recovery = FinanceRecovery(session, settings)
            result = (
                recovery.apply(project_id, source, confirmation_sha256 or "")
                if apply
                else recovery.preview(project_id, source)
            )
            typer.echo(json.dumps(result, indent=2, sort_keys=True))
    except (RepositoryError, BackupError, OSError, ValueError) as exc:
        code = (getattr(exc, "data", None) or {}).get("finance_code", "recovery_requires_repair")
        typer.echo(f"finance-recover: {code}; no existing ledger was overwritten", err=True)
        raise typer.Exit(1) from None
    finally:
        engine.dispose()
