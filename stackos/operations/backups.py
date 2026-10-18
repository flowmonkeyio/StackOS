"""Local operator backup controls. No archive bytes or recovery key cross APIs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from stackos import backups
from stackos.config import Settings
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.errors import ToolNotGrantedError
from stackos.mcp.streaming import ProgressEmitter
from stackos.operations.spec import (
    OperationResponsePolicy,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.base import ValidationError


class BackupStatusInput(MCPInput):
    pass


class BackupConfigureInput(MCPInput):
    enabled: bool | None = None
    interval_hours: int | None = Field(default=None, ge=1, le=168)
    keep_last: int | None = Field(default=None, ge=1, le=365)
    destination: str | None = None
    recipient_public_key: str | None = Field(default=None, max_length=4096)
    targets: list[backups.BackupTarget] | None = None


class BackupCreateInput(MCPInput):
    output: str | None = None


class BackupStatusOut(BaseModel):
    project_ids: list[int] | None = None
    configured: bool
    ready: bool
    enabled: bool
    interval_hours: int
    keep_last: int
    destination: str
    targets: list[dict[str, Any]]
    recipient_fingerprint: str | None
    last_attempt: dict[str, Any] | None
    last_success: dict[str, Any] | None
    last_error: dict[str, Any] | None


def _settings(inp: MCPInput, ctx: MCPContext) -> Settings:
    if (
        ctx.extras.get("surface") != "rest"
        or ctx.extras.get("trusted_local_admin") is not True
        or inp.run_token is not None
    ):
        raise ToolNotGrantedError("Backup controls require an explicit local operator session.")
    settings = ctx.extras.get("settings")
    if not isinstance(settings, Settings):
        raise ValidationError("Local backup settings are unavailable.")
    return settings


async def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except backups.BackupError as exc:
        raise ValidationError(exc.message, data=exc.as_dict()) from exc
    except (OSError, ValueError) as exc:
        raise ValidationError(
            "Backup configuration or local paths are invalid or inaccessible.",
            data={
                "code": "backup-configuration",
                "next_action": "Check backup paths and the public recipient key.",
            },
        ) from exc


async def status(inp: BackupStatusInput, ctx: MCPContext, _p: ProgressEmitter) -> BackupStatusOut:
    value = await _call(backups.backup_status, _settings(inp, ctx))
    return BackupStatusOut.model_validate(value)


async def configure(
    inp: BackupConfigureInput,
    ctx: MCPContext,
    _p: ProgressEmitter,
) -> WriteEnvelope[BackupStatusOut]:
    settings = _settings(inp, ctx)

    def patch() -> dict[str, Any]:
        fields: dict[str, Any] = {}
        for name in (
            "enabled",
            "interval_hours",
            "keep_last",
            "destination",
            "recipient_public_key",
            "targets",
        ):
            if name in inp.model_fields_set:
                # Optional null is not a reset command; omissions always preserve.
                value = getattr(inp, name)
                if value is None:
                    raise ValueError("null policy fields are not supported")
                fields[name] = value
        backups.update_policy(settings, fields)
        return backups.backup_status(settings)

    value = await _call(patch)
    return WriteEnvelope(data=BackupStatusOut.model_validate(value))


async def create(
    inp: BackupCreateInput,
    ctx: MCPContext,
    _p: ProgressEmitter,
) -> WriteEnvelope[dict[str, Any]]:
    settings = _settings(inp, ctx)
    value = await _call(
        backups.create_backup, settings, output=Path(inp.output) if inp.output else None
    )
    return WriteEnvelope(data=value)


def operation_specs() -> list[OperationSpec]:
    common: dict[str, Any] = dict(
        purpose=(
            "Back up managed project data and explicitly enrolled external folders "
            "in encrypted recovery archives."
        ),
        prerequisites=("Explicit local operator authority; no workflow/run-token authority.",),
        returns=("Safe policy, coverage and outcome metadata; no private keys or archive bytes.",),
        secret_policy="public-key-input-no-secret-output",
        category="system",
        response_policy=OperationResponsePolicy(
            default_mode="raw",
            allowed_modes=("raw",),
            ack_safe=False,
            raw_only_reason="Local setup retains full safe coverage and repair metadata.",
        ),
    )
    specs = []
    for name, summary, input_model, output_model, handler, mutating in (
        (
            "backup.status",
            "Inspect local backup coverage and safe outcomes.",
            BackupStatusInput,
            BackupStatusOut,
            status,
            False,
        ),
        (
            "backup.configure",
            "Explicitly configure enrolled folders and opt-in automatic backups.",
            BackupConfigureInput,
            WriteEnvelope[BackupStatusOut],
            configure,
            True,
        ),
        (
            "backup.create",
            "Create an encrypted manual backup of all enrolled local folders.",
            BackupCreateInput,
            WriteEnvelope[dict[str, Any]],
            create,
            True,
        ),
    ):
        specs.append(
            OperationSpec(
                name=name,
                summary=summary,
                input_model=input_model,
                output_model=output_model,
                handler=handler,
                mutating=mutating,
                grant_policy="local-admin-backup-write" if mutating else "local-admin-backup-read",
                surfaces=OperationSurfaces(
                    mcp=OperationSurface(
                        enabled=False, notes="Local filesystem backup controls are operator-only."
                    ),
                    rest=OperationSurface(
                        enabled=True, browser_safe=True, path=f"/api/v1/operations/{name}/call"
                    ),
                    cli=OperationSurface(enabled=True, command=f"ops call {name}"),
                ),
                **common,
            )
        )
    return specs
