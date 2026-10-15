"""Thin local backup/recovery adapters; private key input stays on this host."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError

from stackos import backups
from stackos.config import get_settings

from .app import app, backups_app


def _emit(value: dict[str, Any]) -> None:
    typer.echo(json.dumps(value, indent=2, default=str))


def _fail(exc: Exception) -> None:
    if isinstance(exc, backups.BackupError):
        typer.echo(f"backup: {exc.code}: {exc.message}", err=True)
        typer.echo(exc.next_action, err=True)
    else:
        typer.echo("backup: invalid or inaccessible local backup configuration.", err=True)
        typer.echo("Check the paths, policy limits, and X25519 public key file.", err=True)
    raise typer.Exit(code=1)


def _public_key(path: Path) -> str:
    # Only public PEM travels through this adapter. The service validates type.
    if path.stat().st_size > 4096:
        raise ValueError("public key file is too large")
    return path.read_text(encoding="utf-8")


@app.command()
def backup(
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="New encrypted archive path.")
    ] = None,
    public_key_file: Annotated[
        Path | None, typer.Option("--public-key-file", help="Recipient X25519 public PEM.")
    ] = None,
) -> None:
    """Encrypt StackOS state and every explicitly enrolled external folder."""
    try:
        _emit(
            backups.create_backup(
                get_settings(),
                output=output,
                recipient_public_key=_public_key(public_key_file) if public_key_file else None,
            )
        )
    except (backups.BackupError, OSError, ValueError) as exc:
        _fail(exc)


@backups_app.command("keygen")
def keygen(
    private_key_file: Annotated[Path, typer.Option(help="New independent private PEM path.")],
    public_key_file: Annotated[Path, typer.Option(help="New recipient public PEM path.")],
) -> None:
    """Generate a recovery key pair; store the private file separately/off-device."""
    try:
        _emit(backups.generate_recovery_key(get_settings(), private_key_file, public_key_file))
    except (backups.BackupError, OSError, ValueError) as exc:
        _fail(exc)


@backups_app.command("configure")
def configure(
    enabled: Annotated[bool | None, typer.Option("--enabled/--disabled")] = None,
    public_key_file: Annotated[Path | None, typer.Option()] = None,
    destination: Annotated[Path | None, typer.Option()] = None,
    interval_hours: Annotated[int | None, typer.Option(min=1, max=168)] = None,
    keep_last: Annotated[int | None, typer.Option(min=1, max=365)] = None,
    target_id: Annotated[
        str | None, typer.Option(help="Stable external-folder ID to add/update.")
    ] = None,
    target_path: Annotated[
        Path | None, typer.Option(help="Entire external folder to include.")
    ] = None,
    target_label: Annotated[str | None, typer.Option()] = None,
    target_lock: Annotated[
        Path | None, typer.Option(help="Existing persistent writer lock.")
    ] = None,
) -> None:
    """Patch local policy. Automatic backups remain off until --enabled."""
    try:
        settings = get_settings()
        fields: dict[str, Any] = {}
        target = None
        for name, value in (
            ("enabled", enabled),
            ("destination", destination),
            ("interval_hours", interval_hours),
            ("keep_last", keep_last),
        ):
            if value is not None:
                fields[name] = value
        if public_key_file is not None:
            fields["recipient_public_key"] = _public_key(public_key_file)
        if any(value is not None for value in (target_id, target_path, target_label, target_lock)):
            if target_id is None or target_path is None:
                raise ValueError("target-id and target-path are required together")
            target = backups.BackupTarget(
                id=target_id,
                label=target_label or target_id,
                path=target_path,
                lock_file=target_lock,
            )
        backups.update_policy(settings, fields, upsert_target=target)
        _emit(backups.backup_status(settings))
    except (backups.BackupError, OSError, ValueError, ValidationError) as exc:
        _fail(exc)


@backups_app.command("status")
def status() -> None:
    """Show safe coverage, opt-in state, and the latest backup outcomes."""
    try:
        _emit(backups.backup_status(get_settings()))
    except (backups.BackupError, OSError, ValueError) as exc:
        _fail(exc)


@app.command()
def restore(
    file: Annotated[Path, typer.Argument(help="Encrypted StackOS archive.")],
    private_key_file: Annotated[Path, typer.Option(help="Recovery private PEM file on this host.")],
    output_dir: Annotated[
        Path, typer.Option(help="New, inert recovery directory; never live state.")
    ],
) -> None:
    """Authenticate and verify a backup into a new directory without activating it."""
    try:
        _emit(backups.restore_backup(get_settings(), file, private_key_file, output_dir))
    except (backups.BackupError, OSError, ValueError) as exc:
        _fail(exc)
