"""One real disposable daemon, SDK transport and useful native report.

This is an explicit Darwin integration test, not part of portable component proof.
It never uses the bridge, shared state, providers or automatic action retries.
Additional scenarios may explicitly restart only this test's owned daemon.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlmodel import Session, create_engine, select

from stackos import controlled_process_clock as clock
from stackos import controlled_process_host as host
from stackos.actions import controlled_command as fixed
from stackos.config import Settings
from stackos.db.models import (
    ActionCall,
    ApprovalRequestStatus,
    Project,
    Run,
    TrackerTask,
    WorkItemOwnership,
)
from stackos.repositories.plugins import PluginRepository
from stackos.repositories.run_plans import RunPlanRepository
from stackos.repositories.runs import RunRepository
from stackos.repositories.tracker import TrackerRepository

REF = "engineering.controlled-proof.v1"
SCOPE = "e" * 64
pytestmark = pytest.mark.skipif(
    os.environ.get("STACKOS_RUN_LOADED_SUPERVISION_PROOF") != "1",
    reason="Explicit owned-daemon/native supervision proof requires opt-in",
)
# Original handles remain owned if an observation or cleanup operation fails.
_HELD_DAEMONS: set[subprocess.Popen] = set()


def _reap_owned_daemon(process):
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=30)
    except BaseException:
        # Cleanup failed. Keep the executing owner alive until this exact child
        # exits; do not send another signal or turn late cleanup into success.
        print("Owned daemon cleanup failed; holding its original handle until exit", flush=True)
        while process.returncode is None:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                continue
            except BaseException:
                time.sleep(0.1)
        _HELD_DAEMONS.remove(process)
        raise
    _HELD_DAEMONS.remove(process)


def _setup_case(settings, engine, *, publication=False):
    """Fixture-only canonical setup; actual execution uses the loaded SDK client."""
    with Session(engine) as session:
        PluginRepository(session).sync_builtin_plugins()
        project = Project(
            slug="loaded-proof",
            name="Synthetic loaded proof",
            domain="example.test",
            locale="en-US",
        )
        session.add(project)
        session.commit()
        # Know the exact target before freezing grants. No running grant is edited.
        TrackerRepository(session).create_task(
            project_id=project.id, key="loaded-report", title="Synthetic report"
        )
        task = session.exec(select(TrackerTask).where(TrackerTask.key == "loaded-report")).one()
        action_refs = [REF]
        approvals = [{"key": "command", "title": "Exact synthetic command"}]
        contracts = [{"key": "controlled", "action": REF, "approval_ref": "command"}]
        owner_grants = []
        if publication:
            from stackos.actions.controlled_publication import PUBLICATION_REF

            action_refs.append(PUBLICATION_REF)
            approvals.append({"key": "review", "title": "Exact synthetic publication review"})
            contracts.append(
                {"key": "publish", "action": PUBLICATION_REF, "approval_ref": "review"}
            )
            owner_grants.append(
                {"step_id": "report", "tool": "run.ownerStop", "targets": [f"task:{task.id}"]}
            )
        plans = RunPlanRepository(session)
        plan = plans.create(
            project_id=project.id,
            run_plan_json={
                "schema_version": "stackos.run-plan.v1",
                "key": "loaded.synthetic-report",
                "title": "Synthetic report",
                "approvals": approvals,
                "grants": {
                    "mcp_tool_grants": [
                        {"step_id": "report", "tool": "action.execute", "action_refs": action_refs},
                        *owner_grants,
                    ],
                    "action_contracts": contracts,
                },
                "steps": [{"id": "report", "title": "Report", "action_refs": action_refs}],
            },
        ).data
        started = plans.start(plan.id, project_id=project.id).data
        step = plans.get_step(plan.id, "report")
        task.source_json = {"run_plan_id": plan.id, "run_plan_step_id": step.id}
        task.metadata_json = {
            "ownership_continuity": {
                "scope_digest": SCOPE,
                "budget_ref": "synthetic:one-attempt",
                "stop_scope_ref": "synthetic:stop",
                "history_ref": "synthetic:history",
                "stopped": False,
                "denied": False,
                "attempts_used": 0,
                "failures_used": 0,
                # Report consumes one slot; publication still requires budget headroom.
                "attempt_limit": 2 if publication else 1,
                "failure_limit": 1,
            }
        }
        session.add(task)
        session.commit()
        owner = (
            RunRepository(session)
            .owner_acquire(
                project_id=project.id,
                canonical_item_id=f"task:{task.id}",
                owner_ref="synthetic:lead",
                scope_digest=SCOPE,
            )
            .ownership
        )
        payload = {
            "canonical_item_id": owner.canonical_item_id,
            "owner_ref": owner.owner_ref,
            "expected_epoch": owner.epoch,
            "expected_revision": owner.revision,
            "scope_digest": SCOPE,
            "intent_ref": "synthetic:loaded-report-1",
            "command_key": "synthetic-status-report-v1",
            "command_digest": "0" * 64,
        }
        request = fixed._ControlledCommandInput.model_validate(payload)
        directory = fixed._controlled_report_directory(
            settings, project_id=project.id, request=request
        )
        command = fixed._resolve_controlled_command(request.command_key, report_directory=directory)
        payload["command_digest"] = command.digest
        observed = clock._sample_clock()
        assert observed.status == "known" and observed.continuous_ns is not None
        deadline = observed.continuous_ns + 5_000_000_000
        native = {
            "schema_version": "stackos.controlled-host-approval.v1",
            "command_digest": command.digest,
            "runtime_digest": host._digest(host._native_manifest(command)),
            "boot_uuid": observed.boot_uuid,
            "deadline_continuous_ns": deadline,
            "term_grace_ns": 1_000_000_000,
            "cleanup_deadline_continuous_ns": deadline + 3_000_000_000,
            "max_output_bytes": 65536,
            "member_limit": 8,
            "threat_model": "cooperative-fixed-command",
        }
        decision = {
            "controlled_command": {
                "schema_version": "stackos.controlled-command-approval.v1",
                "action_ref": REF,
                "project_id": project.id,
                "controller_run_id": started.run_id,
                "run_plan_id": plan.id,
                "run_plan_step_id": step.id,
                "canonical_item_id": owner.canonical_item_id,
                "owner_ref": owner.owner_ref,
                "epoch": owner.epoch,
                "scope_digest": SCOPE,
                "intent_ref": request.intent_ref,
                "command_digest": command.digest,
                "command": command.descriptor(),
            },
            "controlled_host": native,
        }
        plans.update(
            run_plan_id=plan.id,
            approval_key="command",
            approval_status=ApprovalRequestStatus.APPROVED,
            decided_by="synthetic:operator",
            decision_json=decision,
        )
        raw = session.exec(select(WorkItemOwnership)).one()
        additions = {
            "deadline_ref": "native:" + host._digest(native),
            "effect_identity": "effect:synthetic:report-candidate",
        }
        task.metadata_json = {
            "ownership_continuity": task.metadata_json["ownership_continuity"] | additions
        }
        raw.continuity_json = raw.continuity_json | additions
        session.add_all([task, raw])
        session.commit()
        plans.claim_step(
            run_plan_id=plan.id,
            run_id=started.run_id,
            step_id="report",
            claimed_by="synthetic:lead",
        )
        return SimpleNamespace(
            project=project.id,
            item=owner.canonical_item_id,
            task=task.id,
            plan=plan.id,
            step=step.id,
            run=started.run_id,
            command=command,
            args={
                "project_id": project.id,
                "run_token": started.run_token,
                "action_ref": REF,
                "input_json": payload,
                "response_mode": "raw",
            },
        )


def _rows(engine):
    with Session(engine) as session:
        return {
            model.__name__: [
                row.model_dump(mode="json")
                for row in session.exec(select(model).order_by(model.id)).all()
            ]
            for model in (ActionCall, Run, WorkItemOwnership)
        }


def _without_capture(value):
    if isinstance(value, dict):
        return {k: _without_capture(v) for k, v in value.items() if k != "captured_at"}
    if isinstance(value, list):
        return [_without_capture(v) for v in value]
    return value


async def _exercise(settings, engine, base, env, work, audit, owner):
    token = settings.token_path.read_text().strip()
    wire_methods = []

    async def sent(request):
        if request.method == "POST":
            body = json.loads(request.content)
            wire_methods.append(body.get("method"))

    async with (
        httpx2.AsyncClient(
            headers={"Authorization": "Bearer " + token},
            transport=httpx2.AsyncHTTPTransport(retries=0),
            trust_env=False,
            follow_redirects=False,
            event_hooks={"request": [sent]},
        ) as client,
        streamable_http_client(base + "/mcp", http_client=client) as streams,
        ClientSession(*streams) as sdk,
    ):
        await sdk.initialize()

        async def call(name, args):
            result = await sdk.call_tool(name, arguments=args)
            assert not result.is_error, str(result)
            return json.loads(result.content[0].text)

        case = _setup_case(settings, engine)
        original = (
            case.command.source_root
            / "plugins/engineering/fixtures/controlled-status-input-v1.json"
        ).read_bytes()
        sentinel = work / "unrelated-sentinel"
        sentinel.write_bytes(b"unchanged sentinel\n")
        before = _rows(engine)
        result = await call("action.execute", case.args)
        output = result["data"]["output_json"]
        assert output["status"] == "succeeded" and output["work_accepted"] is False
        from tests.support.controlled_report import verify_report

        verify_report(
            case.command.report_directory,
            case.command.source_root
            / "plugins/engineering/fixtures/controlled-status-input-v1.json",
            original,
            sentinel,
        )
        after = _rows(engine)
        assert len(after["ActionCall"]) == len(before["ActionCall"]) + 1
        owner = after["WorkItemOwnership"][0]
        assert owner["continuity_json"]["attempts_used"] == 1
        assert owner["quiescence_revision"] == owner["revision"]
        assert "native_quiescence_certificate" in owner["continuity_json"]
        replay = await call("action.execute", case.args)
        assert replay["data"]["replayed"] is True
        assert replay["data"]["output_json"] == output
        assert _rows(engine) == after
        owner_args = {
            "project_id": case.project,
            "canonical_item_id": case.item,
            "response_mode": "raw",
        }
        mcp_status = await call("run.ownerGet", owner_args)
        with httpx.Client(
            headers={"Authorization": "Bearer " + token},
            transport=httpx.HTTPTransport(retries=0),
            trust_env=False,
            follow_redirects=False,
        ) as rest:
            response = rest.post(
                base + "/api/v1/operations/run.ownerGet/call",
                json={"arguments": owner_args},
            )
            response.raise_for_status()
            rest_status = response.json()
        request = work / "owner-read.json"
        request.write_text(json.dumps(owner_args))
        cli = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "stackos",
                "ops",
                "call",
                "run.ownerGet",
                "--input",
                str(request),
                "--response-mode",
                "raw",
            ],
            cwd=work,
            env=env,
            capture_output=True,
            check=True,
        )
        cli_status = json.loads(cli.stdout)
        assert _without_capture(mcp_status) == _without_capture(rest_status)
        assert _without_capture(mcp_status) == _without_capture(cli_status)
        assert _rows(engine) == after
        assert wire_methods == [
            "initialize",
            "notifications/initialized",
            "tools/call",
            "tools/list",
            "tools/call",
            "tools/call",
        ]
        audit.update(
            status="passed",
            action_call_id=result["data"]["action_call"]["id"],
            output=output,
            owner_status=mcp_status,
            actual_mcp_posts=wire_methods,
            report_sha256=hashlib.sha256(
                (case.command.report_directory / "report.json").read_bytes()
            ).hexdigest(),
            original_intent_replayed_without_second_action=True,
            same_revision_mcp_rest_cli=True,
        )


class _LoadedDaemonOwner:
    """One test's exact child handles, including explicit restart generations."""

    def __init__(self, settings, env, work, audit):
        self.settings, self.env, self.work, self.audit = settings, env, work, audit
        self.children = []
        self.streams = []
        self.process = None
        self.expected_loss_children = set()

    def start(self):
        assert self.process is None or self.process not in _HELD_DAEMONS
        number = len(self.children) + 1
        stdout = (self.work / f"daemon-{number}.stdout").open("xb")
        self.streams.append(stdout)
        stderr = (self.work / f"daemon-{number}.stderr").open("xb")
        self.streams.append(stderr)
        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-m",
                "stackos",
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.settings.port),
            ],
            cwd=self.work,
            env=self.env,
            stdout=stdout,
            stderr=stderr,
        )
        self.children.append(process)
        self.process = process
        _HELD_DAEMONS.add(process)
        self.audit["daemon_launches"] = len(self.children)
        startup_end = time.monotonic() + 30
        with httpx.Client(trust_env=False, transport=httpx.HTTPTransport(retries=0)) as client:
            while True:
                assert process.poll() is None, "Owned daemon exited during startup"
                try:
                    healthy = client.get(f"http://127.0.0.1:{self.settings.port}/api/v1/health")
                    if healthy.status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                assert time.monotonic() < startup_end, "Owned daemon did not become ready"
                time.sleep(0.05)

    def stop(self):
        assert self.process is not None
        if self.process in _HELD_DAEMONS:
            _reap_owned_daemon(self.process)

    def restart(self):
        """Explicit operator-selected test step; never called on request failure."""
        self.stop()
        self.start()

    def lose_owned_daemon(self):
        """One explicitly selected abrupt loss of the exact retained child."""
        process = self.process
        assert process is not None and process in _HELD_DAEMONS
        assert process.poll() is None
        process.kill()
        process.wait(timeout=15)
        assert process.returncode == -signal.SIGKILL
        self.expected_loss_children.add(process)
        self.audit["explicit_owned_loss_generations"] = [
            self.children.index(child) + 1 for child in self.expected_loss_children
        ]
        self.stop()

    def close(self):
        errors = []
        for process in reversed(self.children):
            try:
                if process in _HELD_DAEMONS:
                    _reap_owned_daemon(process)
            except BaseException as error:
                errors.append(error)
        for stream in self.streams:
            try:
                stream.close()
            except BaseException as error:
                errors.append(error)
        self.audit["owned_daemon_returncodes"] = [child.returncode for child in self.children]
        self.audit["owned_daemon_reaped"] = all(
            child not in _HELD_DAEMONS for child in self.children
        )
        if errors:
            raise errors[0]


def run_loaded_scenario(tmp_path, exercise):
    assert sys.platform == "darwin", "Explicit Darwin native proof only"
    source = Path(fixed.__file__).resolve().parents[2]
    work = tmp_path / "owned-loaded-proof"
    work.mkdir(mode=0o700)
    # One OS-selected loopback port; no scan or fallback to a shared daemon.
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    settings = Settings(
        _env_file=None,
        data_dir=work / "data",
        state_dir=work / "state",
        host="127.0.0.1",
        port=port,
        controlled_host_enabled=True,
    )
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(work / "home"),
        "TMPDIR": str(work / "tmp"),
        "PYTHONPATH": str(source),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PIP_NO_INDEX": "1",
        "UV_OFFLINE": "1",
        "STACKOS_DATA_DIR": str(settings.data_dir),
        "STACKOS_STATE_DIR": str(settings.state_dir),
        "STACKOS_HOST": "127.0.0.1",
        "STACKOS_PORT": str(port),
        "STACKOS_CONTROLLED_HOST_ENABLED": "true",
    }
    for name in ("home", "tmp"):
        (work / name).mkdir(mode=0o700)
    audit = {"status": "started", "daemon_launches": 0, "bridge_launches": 0}
    base = f"http://127.0.0.1:{port}"
    engine = None
    owner = _LoadedDaemonOwner(settings, env, work, audit)
    try:
        owner.start()
        engine = create_engine(f"sqlite:///{settings.db_path}")
        asyncio.run(exercise(settings, engine, base, env, work, audit, owner))
    finally:
        primary = sys.exception()
        cleanup_errors = []
        try:
            if engine is not None:
                engine.dispose()
        except BaseException as error:
            cleanup_errors.append(error)
        finally:
            try:
                owner.close()
            except BaseException as error:
                cleanup_errors.append(error)
            finally:
                audit["owned_daemon_returncode"] = (
                    owner.process.returncode if owner.process is not None else None
                )
                audit["cleanup_errors"] = [type(error).__name__ for error in cleanup_errors]
                try:
                    (work / "receipt.json").write_text(
                        json.dumps(audit, indent=2, sort_keys=True) + "\n"
                    )
                except BaseException as error:
                    cleanup_errors.append(error)
        if cleanup_errors:
            if primary is None:
                raise cleanup_errors[0]
            for cleanup_error in cleanup_errors:
                primary.add_note("Loaded cleanup also failed: " + type(cleanup_error).__name__)
    assert all(
        child.returncode == -signal.SIGKILL
        if child in owner.expected_loss_children
        else child.returncode in (0, -signal.SIGTERM)
        for child in owner.children
    )
    assert audit["owned_daemon_reaped"] is True and not audit["cleanup_errors"]


def test_loaded_sdk_report_and_original_readback(tmp_path):
    run_loaded_scenario(tmp_path, _exercise)
