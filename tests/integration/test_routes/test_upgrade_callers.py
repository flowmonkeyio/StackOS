"""Lifecycle adapters consume real maintenance envelopes, never assert postflight."""

import json
import socket
import subprocess
from zipfile import ZipFile

import pytest
import typer
from sqlmodel import Session, select
from typer.testing import CliRunner

from stackos.cli import app
from stackos.db.models import TrackerTask
from tests.integration.test_repositories.test_task_delivery import (
    git,
    native_source_fixtures,  # noqa: F401
    prepared_task,
    submit,
)


def maintenance_request(api, method, path, *, body=None, **kwargs):
    response = api.request(method, path, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_lifecycle_caller_prepares_and_finishes_real_owner(api, settings, monkeypatch):
    from stackos.cli import upgrade_commands as caller

    def request(method, path, *, body=None, **kwargs):
        response = api.request(method, path, json=body)
        assert response.status_code == 200, response.text
        return response.json()

    monkeypatch.setattr(caller, "_api_request", request)
    monkeypatch.setattr(caller, "_tcp_can_connect", lambda *args, **kwargs: True)
    status = request("POST", "/api/v1/operations/maintenance.upgradeStatus/call", body={})
    target = status["runtime"]["running_identity"]
    prepared = caller.prepare_for_upgrade(settings, target)
    assert prepared["fenced"] and prepared["status"] == "quiesced"
    assert prepared["target_identity"] == target
    assert caller.prepare_for_upgrade(settings, target) == prepared
    # A restart is required even for a same-build repair.
    api.app.state.scheduler.task_delivery_admission_id = "target-caller-generation"
    monkeypatch.setattr(caller, "_daemon_health_ok", lambda *args, **kwargs: True)
    monkeypatch.setattr("stackos.operations.maintenance.runtime_identity", lambda: target)
    observed = []
    monkeypatch.setattr(
        caller,
        "run_target_doctor",
        lambda settings: observed.append(settings) or "fixture:doctor:exit-0",
    )
    result = caller.finish_pending_upgrade(settings)
    assert result["status"] == "continued" and not result["fenced"]
    assert observed == [settings]
    assert caller.finish_pending_upgrade(settings) is None


def test_identity_cli_reports_staged_target():
    import sys
    from pathlib import Path

    result = CliRunner().invoke(app, ["upgrade-identity"])
    assert result.exit_code == 0, result.output
    assert ":python-source:sha256:" in json.loads(result.stdout)["target_identity"]
    fresh = subprocess.run(
        [sys.executable, "-m", "stackos", "upgrade-identity"],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
    )
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
    assert json.loads(fresh.stdout) == json.loads(result.stdout)


def test_missing_source_database_never_authorizes_occupied_port(settings, monkeypatch, capsys):
    from stackos.cli import upgrade_commands as caller

    monkeypatch.setattr(caller, "_tcp_can_connect", lambda *a: True)
    monkeypatch.setattr(caller, "_api_request", lambda *a, **k: pytest.fail("unowned daemon"))
    with pytest.raises(typer.Exit):
        caller.prepare_for_upgrade(settings, "target:python-source:sha256:" + "a" * 64)
    assert "source ownership is unproven" in capsys.readouterr().err


def test_real_occupied_port_with_missing_source_refuses_without_mutation(settings, monkeypatch):
    from stackos.cli import upgrade_commands as caller

    # Unlike unit lifecycle fixtures, this exercises the real reachability and
    # preparation boundary against an exact owned listener, never default 5180.
    with socket.socket() as source:
        source.bind(("127.0.0.1", 0))
        source.listen()
        selected = settings.model_copy(update={"port": source.getsockname()[1]})
        monkeypatch.setattr(caller, "get_settings", lambda: selected)
        monkeypatch.setattr(caller, "_api_request", lambda *a, **k: pytest.fail("foreign auth/API"))
        result = CliRunner().invoke(app, ["prepare-upgrade"])
        assert result.exit_code == 1
        assert "source ownership is unproven" in result.stderr
        assert not selected.data_dir.exists() and not selected.state_dir.exists()
        assert source.fileno() != -1


def test_postflight_failure_preserves_fence_then_retry(api, settings, monkeypatch):
    from stackos.cli import upgrade_commands as caller

    monkeypatch.setattr(caller, "_api_request", lambda *a, **k: maintenance_request(api, *a, **k))
    monkeypatch.setattr(caller, "_tcp_can_connect", lambda *a: True)
    target = maintenance_request(
        api, "POST", "/api/v1/operations/maintenance.upgradeStatus/call", body={}
    )["runtime"]["running_identity"]
    before = caller.prepare_for_upgrade(settings, target)
    monkeypatch.setattr("stackos.operations.maintenance.runtime_identity", lambda: target)
    monkeypatch.setattr(caller, "_daemon_health_ok", lambda *a: False)
    monkeypatch.setattr(caller, "run_target_doctor", lambda *a: pytest.fail("unhealthy target"))
    with pytest.raises(typer.Exit):
        caller.finish_pending_upgrade(settings)
    assert caller.prepare_for_upgrade(settings, target) == before
    monkeypatch.setattr(caller, "_daemon_health_ok", lambda *a: True)

    def bad_doctor(*args):
        raise typer.Exit(9)

    monkeypatch.setattr(caller, "run_target_doctor", bad_doctor)
    with pytest.raises(typer.Exit):
        caller.finish_pending_upgrade(settings)
    assert caller.prepare_for_upgrade(settings, target) == before
    with pytest.raises(typer.Exit):
        caller.prepare_for_upgrade(settings, "other:python-source:sha256:" + "a" * 64)
    monkeypatch.setattr(caller, "run_target_doctor", lambda *a: "fixture:doctor:exit-0")
    assert caller.finish_pending_upgrade(settings)["status"] == "continued"


@pytest.mark.parametrize("reachable", [False, True])
def test_unsupported_source_refuses_admitted_work_then_bootstraps_quiescent(
    api, settings, project_id, monkeypatch, capsys, reachable
):
    from stackos.cli import upgrade_commands as caller
    from stackos.repositories.tracker import TrackerRepository

    with Session(api.app.state.engine) as session:
        repo = TrackerRepository(session)
        task = repo.create_task(
            project_id=project_id, key="legacy-active", title="Legacy work"
        ).data.task
        row = session.get(TrackerTask, task.id)
        row.metadata_json = {"delivery": {"status": "queued", "handoff_id": "retained"}}
        session.add(row)
        session.commit()
    # Exercise the pre-handshake fallback with the actual persisted scalar owners.
    monkeypatch.setattr(caller, "_tcp_can_connect", lambda *a: reachable)
    monkeypatch.setattr(caller, "_api_request", lambda *a, **k: None)
    identity = "target:python-source:sha256:" + "a" * 64
    with pytest.raises(typer.Exit):
        caller.prepare_for_upgrade(settings, identity)
    refusal = capsys.readouterr().err
    assert f"project {project_id} task legacy-active" in refusal
    assert "controlled bootstrap" in refusal and "Do not abort/reopen" in refusal
    with Session(api.app.state.engine) as session:
        row = session.get(TrackerTask, task.id)
        assert row.metadata_json["delivery"]["handoff_id"] == "retained"
        row.metadata_json = {"delivery": {"status": "stopped"}}
        session.add(row)
        session.commit()
    assert caller.prepare_for_upgrade(settings, identity)["status"] == "quiescent-bootstrap"


def test_downloaded_archive_identity_is_exact_and_bounded(tmp_path):
    from hashlib import sha256

    from stackos.cli.upgrade_commands import archive_identity

    archive = tmp_path / "target.zip"
    raw = b'{"version":"2.1.45","buildId":"20261116T170000Z"}\n'
    member = "StackOS.app/Contents/Resources/stackos/build-info.json"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr(member, raw)
    assert archive_identity(archive) == f"2.1.45:build-info:sha256:{sha256(raw).hexdigest()}"
    with ZipFile(archive, "a") as bundle:
        bundle.writestr("other/" + member, raw)
    with pytest.raises(typer.Exit):
        archive_identity(archive)


def test_install_and_restart_use_maintenance_before_side_effects(tmp_path, monkeypatch):
    import stackos.cli.daemon_commands as daemon
    import stackos.cli.daemon_processes as processes
    import stackos.cli.local_commands as local
    from stackos.cli import upgrade_commands as caller
    from stackos.config import Settings

    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state", port=29684)
    monkeypatch.setattr(daemon, "get_settings", lambda: settings)
    monkeypatch.setattr(daemon, "_launchd_plist_for_requested_context", lambda *a, **k: None)
    monkeypatch.setattr(processes, "_discover_daemon_processes", lambda *a: ([43210], []))
    events = []

    def prepare(context, target, **kwargs):
        assert context.port == 29685 and context.data_dir == settings.data_dir
        events.append("prepare")
        return {"fenced": True, "status": "quiesced", "upgrade_id": "fixture"}

    def terminate(*a, **k):
        events.append("terminate")
        return True, "stopped"

    def spawn(*a, **k):
        events.append("target-ready")
        return True, "ready"

    monkeypatch.setattr(caller, "prepare_for_upgrade", prepare)
    monkeypatch.setattr(processes, "_terminate_daemon_processes", terminate)
    monkeypatch.setattr(processes, "_spawn_detached_daemon", spawn)

    def finish(context, *, upgrade_id):
        assert context.port == 29685 and upgrade_id == "fixture"
        events.append("finish")

    monkeypatch.setattr(caller, "finish_pending_upgrade", finish)
    result = CliRunner().invoke(app, ["restart", "--port", "29685"])
    assert result.exit_code == 0, result.output
    assert events == ["prepare", "terminate", "target-ready", "finish"]

    def refused(*args, **kwargs):
        events.append("refused")
        raise typer.Exit(1)

    events.clear()
    monkeypatch.setattr(caller, "prepare_for_upgrade", refused)
    assert CliRunner().invoke(app, ["restart"]).exit_code == 1
    assert events == ["refused"]
    events.clear()
    monkeypatch.setattr(local, "get_settings", lambda: settings)
    monkeypatch.setattr(
        "stackos.db.migrate.upgrade_to_head", lambda *a: pytest.fail("migration before preparation")
    )
    assert CliRunner().invoke(app, ["install", "--skip-doctor"]).exit_code == 1
    assert events == ["refused"]
    events.clear()
    monkeypatch.setattr(local, "_doctor_home", lambda: tmp_path / "home")
    assert (
        CliRunner()
        .invoke(app, ["install", "--mcp-only", "--launchd", "--force", "--skip-doctor"])
        .exit_code
        == 1
    )
    assert events == ["refused"]


@pytest.mark.parametrize(
    "exit_code,envelope", [(9, {}), (0, {"ok": False, "code": 9}), (0, {"ok": True, "code": 0})]
)
def test_target_doctor_retains_actual_command_exit_and_selected_context(
    settings, monkeypatch, exit_code, envelope
):
    from stackos.cli import upgrade_commands as caller

    settings.ensure_dirs()
    seen = []

    def run(command, **kwargs):
        seen.append((command, kwargs))
        return subprocess.CompletedProcess(command, exit_code, json.dumps(envelope), "")

    monkeypatch.setattr(caller.subprocess, "run", run)
    if exit_code or envelope["ok"] is False:
        with pytest.raises(typer.Exit):
            caller.run_target_doctor(settings)
    else:
        caller.run_target_doctor(settings)
    receipts = list(settings.state_dir.glob("managed-upgrade-doctor-*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    assert receipt["command"][-2:] == ["doctor", "--json"]
    assert receipt["exit_code"] == exit_code
    assert receipts[0].stat().st_mode & 0o777 == 0o600
    assert seen[0][1]["env"]["STACKOS_DATA_DIR"] == str(settings.data_dir)
    assert seen[0][1]["env"]["STACKOS_STATE_DIR"] == str(settings.state_dir)


def test_clone_install_prepares_before_dependency_sync(tmp_path):
    from pathlib import Path

    makefile = Path(__file__).resolve().parents[3] / "Makefile"
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text('#!/bin/sh\necho "$*" > prepare.log\nexit 1\n')
    python.chmod(0o755)
    uv = tmp_path / "uv"
    uv.write_text("#!/bin/sh\ntouch unsafe-sync\nexit 1\n")
    uv.chmod(0o755)
    result = subprocess.run(
        ["make", "-f", str(makefile), "install", f"UV={uv}"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "prepare-upgrade" in (tmp_path / "prepare.log").read_text()
    assert not (tmp_path / "unsafe-sync").exists()


def test_launchd_install_preserves_other_context(tmp_path, monkeypatch):
    from stackos.cli import launchd
    from stackos.config import Settings

    settings = Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state", port=29684)
    home = tmp_path / "home"
    plist = launchd._launchd_plist_path(home)
    plist.parent.mkdir(parents=True)
    original = launchd._launchd_plist_content(
        settings, home=home, host=settings.host, port=settings.port, log_level="INFO"
    )
    plist.write_bytes(original)
    # A different port must not authorize replacement of the same launchd job.
    other = settings.model_copy(update={"port": 29685})
    monkeypatch.setattr(launchd.shutil, "which", lambda *a: "/fixture/launchctl")
    monkeypatch.setattr(launchd, "_launchd_bootout", lambda *a: pytest.fail("foreign bootout"))
    ok, error = launchd._install_launchd_autostart(
        other, home=home, force=True, host=other.host, port=other.port, log_level="INFO"
    )
    assert not ok and "different data/state/host/port context" in error
    assert plist.read_bytes() == original
    assert not plist.with_suffix(".plist.bak").exists()


@pytest.fixture(params=["finish-upgrade", "restart"])
def partial_continuation(api, settings, project_id, tmp_path, monkeypatch, request):
    """Real queue/REST checkpoints: one safe task and one held by source drift."""
    from stackos.cli import daemon_commands as daemon
    from stackos.cli import daemon_processes as processes
    from stackos.cli import upgrade_commands as caller
    from stackos.repositories.tracker import TrackerRepository

    command = request.param
    scheduler = api.app.state.scheduler
    scheduler.pause()
    roots = [tmp_path / name for name in ("held-source", "safe-source")]
    for root in roots:
        root.mkdir()
        git(root, "init")
        git(root, "config", "user.name", "Continuation fixture")
        git(root, "config", "user.email", "fixture@example.test")
        git(root, "commit", "--allow-empty", "-m", "baseline")
    with Session(api.app.state.engine) as session:
        prepared_task(session, project_id)
        repo = TrackerRepository(session)
        repo.create_task(project_id=project_id, key="safe", title="Safe continuation")
        repo.create_ticket(project_id=project_id, task_key="safe", key="safe-first", title="Safe")
        submit(session, project_id, roots[0], scheduler)
        submit(session, project_id, roots[1], scheduler, task_key="safe")

    calls = []

    def operation_request(method, path, *, body=None, **kwargs):
        calls.append({"path": path, "body": body})
        return maintenance_request(api, method, path, body=body)

    monkeypatch.setattr(caller, "_api_request", operation_request)
    monkeypatch.setattr(caller, "get_settings", lambda: settings)
    monkeypatch.setattr(caller, "_tcp_can_connect", lambda *a: True)
    monkeypatch.setattr(caller, "_daemon_health_ok", lambda *a: True)
    monkeypatch.setattr(caller, "run_target_doctor", lambda *a: "fixture:doctor:exit-0")
    target = operation_request(
        "POST", "/api/v1/operations/maintenance.upgradeStatus/call", body={}
    )["runtime"]["running_identity"]
    prepared = caller.prepare_for_upgrade(settings, target)
    scheduler.task_delivery_admission_id = "repair-target-generation"
    drift = roots[0] / "foreign.txt"
    drift.write_text("unaccepted source drift")
    if command == "restart":
        monkeypatch.setattr(daemon, "get_settings", lambda: settings)
        monkeypatch.setattr(daemon, "_launchd_plist_for_requested_context", lambda *a, **k: None)
        monkeypatch.setattr(processes, "_discover_daemon_processes", lambda *a: ([], []))
        monkeypatch.setattr(processes, "_tcp_can_connect", lambda *a, **k: False)
        monkeypatch.setattr(
            processes, "_spawn_detached_daemon", lambda *a, **k: (True, "target ready")
        )
    first = CliRunner().invoke(app, [command])
    partial = json.loads(first.stdout[first.stdout.index("{") :])
    assert partial["upgrade_id"] == prepared["upgrade_id"]
    assert partial["status"] == "partial" and partial["fenced"] is False
    assert {item["task_key"]: item["status"] for item in partial["checkpoints"]} == {
        "deliver": "held",
        "safe": "consumed",
    }
    return first, partial, drift, calls, operation_request


def test_partial_finish_fails_with_original_upgrade_and_checkpoint_refs(partial_continuation):
    first, partial, _, _, request = partial_continuation
    assert first.exit_code == 1, first.output
    assert partial["upgrade_id"] in first.stderr
    held = next(item for item in partial["checkpoints"] if item["status"] == "held")
    assert held["task_key"] in first.stderr and held["handoff_id"] in first.stderr
    assert f"finish-upgrade --upgrade-id {partial['upgrade_id']}" in first.stderr
    # The global fence is gone; this must not be mistaken for the held upgrade.
    assert (
        request("POST", "/api/v1/operations/maintenance.upgradeStatus/call", body={})["status"]
        == "idle"
    )


def test_partial_finish_explicit_retry_preserves_safe_and_unrelated_work(
    partial_continuation, api, project_id
):
    _, partial, drift, calls, _request = partial_continuation

    def metadata():
        with Session(api.app.state.engine) as session:
            return {
                row.key: row.metadata_json
                for row in session.exec(
                    select(TrackerTask).where(TrackerTask.project_id == project_id)
                ).all()
            }

    before = metadata()
    drift.unlink()
    calls.clear()
    retry = CliRunner().invoke(app, ["finish-upgrade", "--upgrade-id", partial["upgrade_id"]])
    assert retry.exit_code == 0, retry.output
    continued = json.loads(retry.stdout)
    assert continued["status"] == "continued" and continued["upgrade_id"] == partial["upgrade_id"]
    assert continued["revision"] == partial["revision"] + 1
    assert calls[0]["body"]["arguments"] == {"upgrade_id": partial["upgrade_id"]}
    assert not any("prepareUpgrade" in call["path"] for call in calls)
    finish = next(call["body"]["arguments"] for call in calls if "finishUpgrade" in call["path"])
    assert finish["upgrade_id"] == partial["upgrade_id"]
    assert finish["expected_revision"] == partial["revision"]
    after = metadata()
    assert after["safe"] == before["safe"]
    assert after["unrelated"] == before["unrelated"]
    assert after["deliver"]["delivery"]["upgrade_checkpoint"]["status"] == "consumed"
    calls.clear()
    repeated = CliRunner().invoke(app, ["finish-upgrade", "--upgrade-id", partial["upgrade_id"]])
    assert repeated.exit_code == 0, repeated.output
    assert json.loads(repeated.stdout) == continued
    assert metadata() == after
    assert not any("finishUpgrade" in call["path"] for call in calls)
