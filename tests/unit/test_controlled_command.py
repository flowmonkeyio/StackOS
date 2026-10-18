"""Fixed command/schema/registry boundary proof; no target is executed."""

import asyncio
import os
import socket
import subprocess
from copy import deepcopy

import pytest
from pydantic import ValidationError
from sqlmodel import Session, create_engine

from stackos.action_availability import build_action_availability
from stackos.actions import ActionConnectorRequest
from stackos.actions.connectors import ActionConnectorError
from stackos.actions.controlled_command import (
    ControlledCommandConnector,
    _ControlledCommandInput,
    _resolve_controlled_command,
)
from stackos.actions.manifest import ExecutableActionManifest
from stackos.operations.actions.controlled import _check_approval_binding
from stackos.repositories.base import ConflictError


@pytest.mark.parametrize(
    "case_id",
    [
        "missing-services",
        "default-disabled",
        "foreign-extras",
        "unsupported-abi",
        "enabled-canonical-settings",
        "legacy-unavailable-manifest",
    ],
)
def test_public_native_capability(case_id, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from stackos.actions import controlled_command as fixed
    from stackos.config import Settings
    from stackos.operations.actions.controlled import _native_settings

    settings = Settings(
        _env_file=None,
        state_dir=tmp_path / "state",
        data_dir=tmp_path / "data",
        controlled_host_enabled=case_id not in {"default-disabled", "missing-services"},
    )
    with Session(create_engine("sqlite://")) as session:
        if case_id != "missing-services":
            session.info["operation_services"] = {"settings": settings}
        ctx = SimpleNamespace(session=session, extras={"settings": settings})
        if case_id == "foreign-extras":
            ctx.extras["settings"] = settings.model_copy()
        if case_id in {"missing-services", "default-disabled", "foreign-extras"}:
            with pytest.raises(ConflictError, match="controlled_host_unavailable"):
                _native_settings(ctx)
        else:
            assert _native_settings(ctx) is settings
        if case_id == "unsupported-abi":
            monkeypatch.setattr(fixed.sys, "platform", "linux")
            assert not fixed._controlled_host_supported()
        if case_id == "legacy-unavailable-manifest":
            assert fixed._controlled_host_settings(session) is settings
            # The installed setting never erases an explicit unavailable declaration.
            manifest = ExecutableActionManifest(
                action_id=1,
                plugin_slug="engineering",
                action_key="controlled-proof.v1",
                action_ref="engineering.controlled-proof.v1",
                name="Controlled",
                operation="controlled-proof.v1",
                connector_key="controlled-command",
                execution_mode="foreground",
                risk_level="write",
                input_schema_json={},
                config_json={"controlled_host": "unavailable", "direct_run": "workflow-only"},
                requires_credential=False,
                allows_credential=False,
            )
            assert (
                build_action_availability(
                    session, manifest=manifest, connector_keys={"controlled-command"}
                ).executable
                is False
            )


@pytest.fixture(autouse=True)
def no_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("external effect forbidden")

    for name in ("Popen", "run"):
        monkeypatch.setattr(subprocess, name, forbidden)
    for name in ("fork", "posix_spawn", "posix_spawnp", "execv", "execve", "kill", "killpg"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("stackos.crypto.load_seed", forbidden)
    monkeypatch.setattr("stackos.crypto.seed.load_seed", forbidden)
    monkeypatch.setattr("stackos.crypto.seed.ensure_seed_file", forbidden)


def payload():
    return {
        "canonical_item_id": "ticket:1",
        "owner_ref": "fixture:owner",
        "expected_epoch": 1,
        "expected_revision": 1,
        "scope_digest": "a" * 64,
        "intent_ref": "fixture:intent",
        "command_key": "noop-v1",
        "command_digest": "b" * 64,
    }


def request(**changes):
    return ActionConnectorRequest(
        project_id=1,
        plugin_slug="engineering",
        action_key="controlled-proof.v1",
        action_ref="engineering.controlled-proof.v1",
        provider_key=None,
        operation="controlled-proof.v1",
        input_json=payload(),
        config_json={},
        **changes,
    )


def test_fixed_descriptor_has_no_caller_command_surface():
    command = _resolve_controlled_command()
    assert command.argv == (str(command.executable), "-I", "-c", "pass")
    assert command.executable.is_absolute() and command.cwd == command.source_root
    assert len(command.source_files) == 12 and len(command.digest) == 64
    assert {
        "stackos/config.py",
        "stackos/controlled_process_host.py",
        "plugins/engineering/plugin.yaml",
    } <= dict(command.source_files).keys()
    assert dict(command.environment) == {
        "LANG": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    assert set(_ControlledCommandInput.model_fields) == set(payload())
    assert ControlledCommandConnector().validate(request()) == []


@pytest.mark.parametrize("case", ["extra-argv", "bool-epoch", "null-intent", "wrong-command"])
def test_controlled_payload_rejects_noncanonical(case):
    value = payload()
    if case == "extra-argv":
        value["argv"] = ["other"]
    elif case == "bool-epoch":
        value["expected_epoch"] = True
    elif case == "null-intent":
        value["intent_ref"] = None
    else:
        value["command_key"] = "other"
    with pytest.raises(ValidationError):
        _ControlledCommandInput.model_validate(value)
    for bad in (True, 1.0, "1", 0, 9223372036854775808):
        with pytest.raises(ValidationError):
            _ControlledCommandInput.model_validate(payload() | {"expected_revision": bad})
    assert (
        _ControlledCommandInput.model_validate(
            payload() | {"expected_epoch": 9223372036854775807}
        ).expected_epoch
        == 9223372036854775807
    )


@pytest.mark.parametrize(
    "case", ["action", "command", "cwd", "source", "environment", "executable"]
)
def test_approval_command_binding_rejects_changed_field(case):
    command = _resolve_controlled_command()
    expected = {
        "schema_version": "stackos.controlled-command-approval.v1",
        "action_ref": "engineering.controlled-proof.v1",
        "project_id": 1,
        "controller_run_id": 1,
        "run_plan_id": 1,
        "run_plan_step_id": 1,
        "epoch": 1,
        "command": command.descriptor(),
        "command_digest": command.digest,
    }
    _check_approval_binding({"controlled_command": deepcopy(expected)}, expected, command)
    changed = deepcopy(expected)
    if case == "action":
        changed["action_ref"] = "other.action"
    elif case == "command":
        changed["command"]["argv"][-1] = "different"
    elif case == "cwd":
        changed["command"]["cwd"] += "/other"
    elif case == "source":
        changed["command"]["source_files"][next(iter(changed["command"]["source_files"]))] = (
            "0" * 64
        )
    elif case == "environment":
        changed["command"]["environment"]["PATH"] = "/other"
    else:
        changed["command"]["executable_sha256"] = "0" * 64
    with pytest.raises(ConflictError):
        _check_approval_binding({"controlled_command": changed}, expected, command)


@pytest.mark.parametrize("case", ["constructed-request", "forged-ids", "callback"])
def test_connector_never_executes_without_dispatch_authority(case):
    changes = {"action_call_id": 123, "attempt_ref": "fabricated"} if case == "forged-ids" else {}
    if case == "callback":
        changes["progress_callback"] = lambda _: None
    with pytest.raises(ActionConnectorError, match="controlled_host_unavailable"):
        asyncio.run(ControlledCommandConnector().execute(request(**changes)))


def test_catalog_reports_host_unavailable():
    manifest = ExecutableActionManifest(
        action_id=1,
        action_key="controlled-proof.v1",
        action_ref="engineering.controlled-proof.v1",
        plugin_slug="engineering",
        name="Proof",
        risk_level="write",
        connector_key="controlled-command",
        operation="controlled-proof.v1",
        execution_mode="foreground",
    )
    engine = create_engine("sqlite://")
    try:
        with Session(engine) as session:
            available = build_action_availability(
                session, manifest=manifest, connector_keys={"controlled-command"}
            )
            assert available.status == "not_executable" and not available.executable
            assert "controlled_host_unavailable" in available.reasons
            disabled = build_action_availability(
                session,
                manifest=manifest,
                connector_keys={"controlled-command"},
                plugin_disabled=True,
            )
            assert disabled.status == "plugin_disabled"
    finally:
        engine.dispose()
