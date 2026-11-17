"""Thin lifecycle adapter for the daemon-owned managed-upgrade transaction."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import Annotated, NoReturn
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

import typer
from sqlalchemy import create_engine, func, select
from sqlmodel import Session, col

from stackos.config import Settings, get_settings

from .api_client import DaemonAPIError, _api_request, _echo_json
from .app import app
from .daemon_processes import _daemon_health_ok, _tcp_can_connect


def _fail(message: str) -> NoReturn:
    typer.echo(f"Managed upgrade: {message}", err=True)
    raise typer.Exit(code=1)


def _call(settings: Settings, name: str, arguments: dict | None = None, *, probe=False):
    try:
        return _api_request(
            "POST",
            f"/api/v1/operations/maintenance.{name}/call",
            body={"arguments": arguments or {}},
            settings=settings,
            allow_not_found=probe,
            allow_unavailable=probe,
            quiet_errors=True,
        )
    except DaemonAPIError as exc:
        detail = (
            exc.payload.get("detail", "request refused")
            if isinstance(exc.payload, dict)
            else exc.payload
        )
        typer.echo(
            f"Managed upgrade {name} failed: {detail}. "
            "Inspect `stackos ops call maintenance.upgradeStatus` for the current fence "
            "and repair context before retrying.",
            err=True,
        )
        if isinstance(exc.payload, dict) and exc.payload.get("data"):
            typer.echo(json.dumps(exc.payload["data"], sort_keys=True), err=True)
        raise


def _offline_state(settings: Settings):
    """Read only existing scalar owners, including the pre-handshake schema.

    This is a refusal/bootstrap check, never a legacy checkpoint coordinator.
    Unknown schemas/owners fail closed. No prompts or credentials are read.
    """
    from stackos.db.models import TrackerTask
    from stackos.repositories.agent_requests import AgentRequestRepository
    from stackos.repositories.managed_upgrades import ManagedUpgradeRepository
    from stackos.repositories.project_setup import ProjectSetupRepository
    from stackos.repositories.tracker.utils import TERMINAL_TRACKER_STATUSES

    if not settings.db_path.exists():
        return None, []
    engine = create_engine(f"sqlite:///{settings.db_path.as_uri()}?mode=ro&uri=true")
    blockers = []
    try:
        with Session(engine) as session:
            tables = (
                session.connection()
                .exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")
                .scalars()
                .all()
            )
            pending = (
                ManagedUpgradeRepository(session).status() if "managed_upgrades" in tables else None
            )
            tasks = session.execute(
                select(col(TrackerTask.project_id), col(TrackerTask.key)).where(
                    func.json_extract(TrackerTask.metadata_json, "$.delivery.status").in_(
                        ("queued", "running")
                    ),
                    col(TrackerTask.status).notin_(TERMINAL_TRACKER_STATUSES),
                )
            ).all()
            blockers = [f"project {project_id} task {key}" for project_id, key in tasks]
            for item in ProjectSetupRepository(session).managed_admissions():
                blockers.append(f"project {item.project_id} setup request {item.request_id}")
            AgentRequestRepository(session).require_native_quiescence()
            return pending, blockers
    except Exception as exc:
        context = "; owners: " + "; ".join(blockers) if blockers else ""
        if data := getattr(exc, "data", None):
            context += "; repair context: " + json.dumps(data, sort_keys=True)
        _fail(
            f"cannot prove source quiescence ({type(exc).__name__}: {exc}){context}. "
            "Inspect source owners; nothing was interrupted."
        )
    finally:
        engine.dispose()


def prepare_for_upgrade(settings: Settings, target_identity: str, *, only_if_changed=False):
    if not re.fullmatch(r"[^:]+:(?:build-info|python-source):sha256:[a-f0-9]{64}", target_identity):
        _fail("invalid staged target identity; use `stackos upgrade-identity` on the staged CLI.")
    reachable = _tcp_can_connect(settings.host, settings.port)
    # A fresh installation owns no source at a possibly occupied default port.
    if not settings.db_path.exists():
        if reachable:
            _fail(
                "the daemon port is occupied but the selected source database is missing. "
                "Use the source installation's matching host/port/data/state settings; "
                "source ownership is unproven and nothing may be interrupted."
            )
        return {"status": "quiescent-bootstrap", "fenced": False}
    status = _call(settings, "upgradeStatus", probe=True) if reachable else None
    if status is None:
        pending, blockers = _offline_state(settings)
        if pending and pending.get("fenced"):
            if pending["target_identity"] != target_identity or pending["status"] != "quiesced":
                _fail(
                    f"upgrade {pending['upgrade_id']} remains {pending['status']}; "
                    "inspect maintenance.upgradeStatus before retrying this target."
                )
            return pending
        if blockers:
            _fail(
                "source lacks the managed-upgrade handshake or is unavailable; admitted work: "
                + "; ".join(blockers)
                + ". Before controlled bootstrap, let these owners finish or use their "
                "explicitly authorized recovery. Keep the source service/app intact, then "
                "install while quiescent. Do not abort/reopen work to simulate continuation."
            )
        # Old sources cannot acquire the atomic fence. This enables the feature
        # only during an operator-coordinated quiescent bootstrap.
        return {"status": "quiescent-bootstrap", "fenced": False}
    if status.get("fenced"):
        if status["target_identity"] != target_identity or status["status"] != "quiesced":
            _fail(
                f"upgrade {status['upgrade_id']} remains {status['status']}; "
                "inspect maintenance.upgradeStatus before retrying this target."
            )
        return status
    source = status["runtime"]["running_identity"]
    if only_if_changed and source == target_identity:
        return None
    result = _call(
        settings,
        "prepareUpgrade",
        {
            "upgrade_id": f"upgrade-{uuid4().hex}",
            "source_identity": source,
            "target_identity": target_identity,
            "expected_revision": 0,
        },
    )["data"]
    if not result.get("fenced") or result.get("status") != "quiesced":
        _fail(
            "preparation did not confirm quiescence; inspect maintenance.upgradeStatus. "
            "Nothing may be replaced."
        )
    return result


def run_target_doctor(settings: Settings) -> str:
    command = [sys.executable, "-m", "stackos", "doctor", "--json"]
    env = {
        **os.environ,
        "STACKOS_HOST": settings.host,
        "STACKOS_PORT": str(settings.port),
        "STACKOS_DATA_DIR": str(settings.data_dir),
        "STACKOS_STATE_DIR": str(settings.state_dir),
    }
    failure = None
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as exc:
        result = None
        failure = type(exc).__name__
    receipt = settings.state_dir / f"managed-upgrade-doctor-{uuid4().hex}.json"
    with os.fdopen(os.open(receipt, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as stream:
        json.dump(
            {
                "command": command,
                "exit_code": result.returncode if result else None,
                "stdout": result.stdout if result else "",
                "stderr": result.stderr if result else "",
                "error_type": failure,
            },
            stream,
        )
    if result is None:
        _fail(
            f"target doctor did not complete ({failure}); continuation remains incomplete. "
            f"See {receipt}; repair the target before retrying the original upgrade."
        )
    try:
        doctor = json.loads(result.stdout)
    except ValueError:
        doctor = {}
    if (
        result.returncode != 0
        or not isinstance(doctor, dict)
        or doctor.get("ok") is not True
        or doctor.get("code") != 0
    ):
        _fail(
            f"target doctor failed (exit {result.returncode}); continuation remains incomplete. "
            f"See {receipt}; repair the target before retrying the original upgrade."
        )
    return str(receipt)


def finish_pending_upgrade(settings: Settings, upgrade_id: str | None = None):
    from stackos.operations.maintenance import runtime_identity

    status = _call(
        settings, "upgradeStatus", {"upgrade_id": upgrade_id} if upgrade_id is not None else None
    )
    if status["status"] == "idle" and upgrade_id is None:
        return None
    upgrade_id = status["upgrade_id"]
    # Consumption alone does not prove dispatch. Pending/old-generation work
    # needs the same original finish owner and current target postflight.
    if status["status"] == "continued" and not status.get("recovery_required"):
        return status
    retry = f"stackos finish-upgrade --upgrade-id {upgrade_id}"
    if (
        status["status"] not in {"quiesced", "partial", "continued"}
        or status["target_identity"] != runtime_identity()
    ):
        _fail(
            f"upgrade {upgrade_id} target/preparation differs; continuation remains incomplete. "
            f"Inspect maintenance.upgradeStatus for this upgrade; after repair use `{retry}`."
        )
    runtime = status["runtime"]
    if runtime["running_identity"] != status["target_identity"] or not _daemon_health_ok(
        settings.host, settings.port
    ):
        _fail(
            f"upgrade {upgrade_id} intended target is not healthy; "
            "continuation remains incomplete. "
            f"Repair/start the intended target, then use `{retry}`."
        )
    try:
        evidence = run_target_doctor(settings)
    except typer.Exit:
        typer.echo(f"Retry upgrade {upgrade_id} with `{retry}` after repair.", err=True)
        raise
    result = _call(
        settings,
        "finishUpgrade",
        {
            "upgrade_id": upgrade_id,
            "target_identity": status["target_identity"],
            "expected_revision": status["revision"],
            "daemon_generation": runtime["daemon_generation"],
            "doctor_exit_code": 0,
            "doctor_evidence_ref": evidence,
        },
    )["data"]
    if (
        result.get("status") != "continued"
        or result.get("fenced")
        or result.get("recovery_required")
    ):
        # Partial finish releases the global fence while preserving task holds.
        # Keep the actual owner envelope available even though this command fails.
        _echo_json(result)
        held = "; ".join(
            f"project {item['project_id']} task {item['task_key']} handoff {item['handoff_id']}"
            + (": " + ", ".join(item["issues"]) if item.get("issues") else "")
            for item in result.get("checkpoints", [])
            if item.get("status") not in {"consumed", "settled"}
            or item.get("issues")
            or item.get("continuation_state") in {"pending", "unconfirmed"}
        )
        _fail(
            f"upgrade {upgrade_id} continuation remains incomplete "
            f"(status {result.get('status')}); "
            f"held checkpoints: {held or 'inspect the retained upgrade receipt'}. "
            f"After repairing these original checkpoints, use `{retry}`."
        )
    return result


def archive_identity(archive: Path) -> str:
    with ZipFile(archive) as bundle:
        entries = [
            item
            for item in bundle.infolist()
            if item.filename.endswith(".app/Contents/Resources/stackos/build-info.json")
        ]
        if len(entries) != 1 or entries[0].file_size > 4096:
            _fail("downloaded update has no unique bounded packaged build identity.")
        raw = bundle.read(entries[0])
    info = json.loads(raw)
    if (
        not isinstance(info, dict)
        or not isinstance(info.get("buildId"), str)
        or not re.fullmatch(r"\d{8}T\d{6}Z", info["buildId"])
        or not isinstance(info.get("version"), str)
    ):
        _fail("downloaded update build identity is invalid.")
    return f"{info['version']}:build-info:sha256:{sha256(raw).hexdigest()}"


@app.command()
def upgrade_identity():
    """Read the staged CLI's target identity without changing installation state."""
    from stackos.operations.maintenance import runtime_identity

    _echo_json({"target_identity": runtime_identity()})


@app.command()
def prepare_upgrade(
    target_identity: Annotated[str | None, typer.Option("--target-identity")] = None,
    target_archive: Annotated[Path | None, typer.Option("--target-archive")] = None,
):
    """Prepare before replacing a staged runtime; never installs or resumes work."""
    if target_identity and target_archive:
        _fail("select only one staged --target-identity or --target-archive.")
    if target_archive:
        try:
            target_identity = archive_identity(target_archive)
        except (OSError, ValueError, KeyError, BadZipFile) as exc:
            _fail(f"cannot read downloaded target ({type(exc).__name__}); nothing was interrupted.")
    if target_identity is None:
        from stackos.operations.maintenance import runtime_identity

        target_identity = runtime_identity()
    _echo_json(prepare_for_upgrade(get_settings(), target_identity))


@app.command()
def finish_upgrade(
    upgrade_id: Annotated[
        str | None,
        typer.Option(
            "--upgrade-id", help="Select the original upgrade, including partial recovery."
        ),
    ] = None,
):
    """Finish a prepared upgrade, or acknowledge its retained completion."""
    _echo_json(finish_pending_upgrade(get_settings(), upgrade_id=upgrade_id))
