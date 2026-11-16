"""Explicit installation-wide managed upgrades, never project-agent authority."""

import json
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.errors import ToolNotGrantedError
from stackos.operations.spec import (
    OperationExample,
    OperationResponsePolicy,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.base import ValidationError
from stackos.repositories.managed_upgrades import ManagedUpgradeRepository
from stackos.task_delivery import _admission_id, finish_managed_upgrade, prepare_managed_upgrade


class UpgradeStatusInput(MCPInput):
    model_config = ConfigDict(extra="forbid")
    response_mode: Literal["compact", "raw", "standard", "verbose"] = "compact"
    upgrade_id: str | None = Field(default=None, min_length=1, max_length=160)


class PrepareUpgradeInput(UpgradeStatusInput):
    upgrade_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,160}$")
    source_identity: str = Field(min_length=1, max_length=240)
    target_identity: str = Field(min_length=1, max_length=240)
    expected_revision: int = Field(ge=0, strict=True)


class FinishUpgradeInput(UpgradeStatusInput):
    upgrade_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,160}$")
    target_identity: str = Field(min_length=1, max_length=240)
    expected_revision: int = Field(ge=0, strict=True)
    daemon_generation: str = Field(min_length=1, max_length=160)
    doctor_exit_code: int = Field(strict=True)
    doctor_evidence_ref: str = Field(min_length=1, max_length=500)


def upgrade_runtime(scheduler):
    """Freeze the actual target package/source identity once per daemon generation."""
    import stackos
    from stackos.mcp.bridge.diagnostics import _build_identity

    identity = getattr(scheduler, "managed_upgrade_identity", None)
    if identity is None:
        build = _build_identity()
        digest = build["build_info_sha256"]
        kind = "build-info"
        if digest is None:
            kind = "python-source"
            fingerprint = sha256()
            root = Path(stackos.__file__).parent
            for path in sorted(root.rglob("*.py")):
                fingerprint.update(json.dumps(str(path.relative_to(root))).encode())
                fingerprint.update(sha256(path.read_bytes()).digest())
            digest = fingerprint.hexdigest()
        identity = f"{build['version']}:{kind}:sha256:{digest}"
        scheduler.managed_upgrade_identity = identity
    return {"running_identity": identity, "daemon_generation": _admission_id(scheduler)}


def _runtime_result(result, ctx):
    return {**result, "runtime": upgrade_runtime(ctx.extras["scheduler"])}


def _authorize(inp, ctx):
    if (
        ctx.extras.get("surface") != "rest"
        or ctx.extras.get("auth_scope") != "daemon-admin"
        or ctx.extras.get("trusted_local_admin") is not True
        or inp.run_token is not None
    ):
        raise ToolNotGrantedError(
            "Managed upgrade requires explicit installation administrator authority",
            data={"code": "local_admin_required", "next_action": "use_local_admin_rest_or_cli"},
        )


async def upgrade_status(inp, ctx, _emitter):
    _authorize(inp, ctx)
    return _runtime_result(ManagedUpgradeRepository(ctx.session).status(inp.upgrade_id), ctx)


async def prepare_upgrade(inp, ctx, _emitter):
    _authorize(inp, ctx)
    scheduler = ctx.extras.get("scheduler")
    factory = ctx.extras.get("session_factory")
    if scheduler is None or factory is None:
        raise ValidationError("Upgrade preparation requires the running local daemon")
    result = await run_in_threadpool(
        prepare_managed_upgrade,
        session_factory=factory,
        scheduler=scheduler,
        upgrade_id=inp.upgrade_id,
        source_identity=inp.source_identity,
        target_identity=inp.target_identity,
        expected_revision=inp.expected_revision,
    )
    return WriteEnvelope(data=_runtime_result(result, ctx))


async def finish_upgrade(inp, ctx, _emitter):
    _authorize(inp, ctx)
    from stackos.api.health import _check_db

    scheduler = ctx.extras.get("scheduler")
    factory = ctx.extras.get("session_factory")
    if scheduler is None or factory is None:
        raise ValidationError("Upgrade continuation requires the running local daemon")
    runtime = upgrade_runtime(scheduler)
    result = await run_in_threadpool(
        finish_managed_upgrade,
        session_factory=factory,
        scheduler=scheduler,
        upgrade_id=inp.upgrade_id,
        target_identity=inp.target_identity,
        expected_revision=inp.expected_revision,
        postflight={
            "target_identity": runtime["running_identity"],
            "daemon_generation": inp.daemon_generation,
            "health_ok": bool(scheduler.running and _check_db(ctx.session.get_bind()) == "ok"),
            "doctor_exit_code": inp.doctor_exit_code,
            "doctor_evidence_ref": inp.doctor_evidence_ref,
        },
    )
    return WriteEnvelope(data=_runtime_result(result, ctx))


def operation_specs():
    return [
        OperationSpec(
            name=name,
            summary=summary,
            input_model=model,
            output_model=WriteEnvelope[dict] if mutating else dict,
            handler=handler,
            mutating=mutating,
            grant_policy="local-admin-maintenance",
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=False),
                rest=OperationSurface(
                    enabled=True, browser_safe=False, path=f"/api/v1/operations/{name}/call"
                ),
                cli=OperationSurface(enabled=True, command=f"ops call {name}"),
            ),
            response_policy=OperationResponsePolicy(
                default_mode="compact",
                allowed_modes=("compact", "raw"),
                ack_safe=False,
                compact_notes=(
                    "Retain build identity, revision, fence, checkpoint refs and repair context.",
                ),
            ),
            purpose=(
                "Prepare, inspect or explicitly finish a managed upgrade through one owner. "
                "Finish requires the intended running target, current daemon generation, healthy "
                "database/scheduler and local-admin doctor exit/evidence. Safe checkpoints rejoin "
                "the existing queue once at their retained phase; unsafe checkpoints stay held. "
                "Startup alone never resumes work."
            ),
            prerequisites=("Explicit installation administrator authority; no project/run token.",),
            returns=(
                "Upgrade identity/revision, fence status and task-owned checkpoint references.",
            ),
            examples=(OperationExample(title="Inspect active upgrade", arguments={}),)
            if not mutating
            else (
                OperationExample(
                    title="Finish managed upgrade"
                    if name.endswith("finishUpgrade")
                    else "Prepare managed upgrade",
                    arguments={
                        "upgrade_id": "upgrade-123",
                        "target_identity": "<intended target runtime identity>",
                        "expected_revision": 2,
                        "daemon_generation": "<target upgradeStatus runtime generation>",
                        "doctor_exit_code": 0,
                        "doctor_evidence_ref": "<target doctor command receipt>",
                    }
                    if name.endswith("finishUpgrade")
                    else {
                        "upgrade_id": "upgrade-123",
                        "source_identity": "source-build",
                        "target_identity": "target-build",
                        "expected_revision": 0,
                    },
                ),
            ),
        )
        for name, summary, model, handler, mutating in (
            (
                "maintenance.upgradeStatus",
                "Inspect managed-upgrade preparation.",
                UpgradeStatusInput,
                upgrade_status,
                False,
            ),
            (
                "maintenance.prepareUpgrade",
                "Fence and quiesce task delivery for a managed upgrade.",
                PrepareUpgradeInput,
                prepare_upgrade,
                True,
            ),
            (
                "maintenance.finishUpgrade",
                "Continue validated managed-upgrade checkpoints through the existing queue.",
                FinishUpgradeInput,
                finish_upgrade,
                True,
            ),
        )
    ]
