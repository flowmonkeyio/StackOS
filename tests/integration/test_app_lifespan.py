"""Lifespan integration tests.

Verifies that creating the app (which runs the lifespan startup hook via
`TestClient` __enter__) generates seed.bin and auth.token at the configured
state dir, both with mode 0600.
"""

from __future__ import annotations

import asyncio
import stat
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlmodel import Session

import stackos.server as server_module
from stackos import __milestone__, __version__
from stackos.config import Settings
from stackos.db.connection import make_engine
from stackos.db.migrate import upgrade_to_head
from stackos.db.model_core import ActionCall, Project
from stackos.db.model_enums import ActionCallStatus
from stackos.server import create_app


@pytest.mark.parametrize("clean", [True, False])
@pytest.mark.parametrize("admitted_dispatch", [False, True])
def test_codex_custody_fake_lifespan_records_before_disposal(monkeypatch, clean, admitted_dispatch):
    """All services/Session/settings are fakes: no app, socket, DB or auth files."""
    events = []
    log = Mock()
    engine = SimpleNamespace(dispose=lambda: events.append("dispose"))
    receipt = {"binding": {"action_call_id": 1}, "cleanup": {"status": "unknown"}}
    admitted_scope = object() if admitted_dispatch else None
    profile, transport = Mock(), Mock()

    class Selection:
        def __init__(self, *, engine: object, settings: object, services: dict, scope: object):
            assert scope is admitted_scope
            assert services["background_action_tasks"] is server_module.BACKGROUND_ACTION_TASKS
            assert "codex-worker" in services["action_connectors"].list_keys()

        def start(self):
            events.append("selection-start")

        async def stop(self):
            events.append("selection-stop")

    class Owner:
        def activate(self):
            events.append("owner-live")

        async def shutdown(self):
            events.append("owner-accounted")
            return {"clean": clean, "commands": [receipt]}

    class Session:
        def __init__(self, _engine):
            assert _engine is engine

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    class Repository:
        def __init__(self, _session):
            pass

        def reconcile_running_calls(self):
            return 0

        def reap_stale(self, **_kwargs):
            return 0

        def record_codex_custody(self, value):
            assert value is receipt
            events.append("original-audit")

    class Runtime:
        async def close_all(self):
            events.append("runtime-stop")

    class Dispatcher:
        def __init__(self, **_kwargs):
            pass

        async def start(self):
            events.append("dispatcher-start")

        async def stop(self):
            events.append("dispatcher-stop")

    class Scheduler:
        running = False

        def add_job(self, *_args, **_kwargs):
            pass

        def start(self):
            self.running = True

        def add_listener(self, callback, _event):
            self.callback = callback

        def shutdown(self, **_kwargs):
            self.running = False
            self.callback(None)

    async def restore(*_args):
        pass

    for name in (
        "configure_logging",
        "_ensure_seed",
        "configure_seed_path",
        "cleanup_old_backup",
        "upgrade_to_head",
    ):
        monkeypatch.setattr(server_module, name, lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server_module, "ensure_token", lambda *_args: "fake-not-a-token")
    monkeypatch.setattr(server_module, "derive_ui_token", lambda *_args: "fake-ui")
    monkeypatch.setattr(server_module, "make_engine", lambda *_args: engine)
    monkeypatch.setattr(server_module.SQLModel.metadata, "create_all", lambda *_args: None)
    monkeypatch.setattr(server_module, "Session", Session)
    monkeypatch.setattr(server_module, "ActionRepository", Repository)
    monkeypatch.setattr(server_module, "RunRepository", Repository)
    monkeypatch.setattr(server_module, "BACKGROUND_ACTION_TASKS", Owner())
    monkeypatch.setattr(server_module, "AgentDispatchLoop", Selection)
    monkeypatch.setattr(server_module, "build_telegram_runtime", lambda *_args: Runtime())
    monkeypatch.setattr(server_module, "restore_telegram_accounts", restore)
    monkeypatch.setattr(server_module, "DurableActionDispatcher", Dispatcher)
    monkeypatch.setattr(server_module, "build_scheduler", lambda *_args: Scheduler())
    monkeypatch.setattr(server_module, "get_logger", lambda *_args: log)
    app = SimpleNamespace(state=SimpleNamespace())
    settings = Mock()

    async def lifecycle():
        async with server_module._build_lifespan(
            settings,
            agent_dispatch_scope=admitted_scope,
            codex_profile=profile if admitted_dispatch else None,
            codex_transport=transport if admitted_dispatch else None,
        )(app):
            assert "codex-worker" in app.state.operation_services["action_connectors"].list_keys()
            assert (app.state.agent_dispatch is not None) is admitted_dispatch

    asyncio.run(lifecycle())
    if admitted_dispatch:
        assert events.index("owner-live") < events.index("selection-start")
        assert events.index("dispatcher-start") < events.index("selection-start")
        assert events.index("selection-stop") < events.index("owner-accounted")
    else:
        assert "selection-start" not in events and "selection-stop" not in events
    assert events.index("owner-accounted") < events.index("original-audit")
    if clean:
        assert events.index("original-audit") < events.index("dispose")
        assert any(call.args == ("daemon.shutdown.clean",) for call in log.info.call_args_list)
    else:
        assert "dispose" not in events and app.state.held_command_engine is engine
        assert not any(call.args == ("daemon.shutdown.clean",) for call in log.info.call_args_list)


def _mode(p) -> int:  # type: ignore[no-untyped-def]
    """Return the permission bits of a Path."""
    return stat.S_IMODE(p.stat().st_mode)


def test_clean_startup_creates_private_state_and_logs_release(
    settings: Settings,
    monkeypatch: MonkeyPatch,
) -> None:
    """One clean startup creates private state and reports the canonical release."""
    captured_log = Mock()
    monkeypatch.setattr(server_module, "get_logger", lambda _name: captured_log)
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:5180"):
        assert settings.data_dir.is_dir()
        assert settings.state_dir.is_dir()
        assert settings.seed_path.exists()
        assert settings.token_path.exists()
        assert _mode(settings.seed_path) == 0o600
        assert _mode(settings.token_path) == 0o600
        # Seed is exactly 32 bytes; token is urlsafe base64 of 32 bytes
        # (43 chars, no padding required by token_urlsafe).
        assert settings.seed_path.read_bytes().__len__() == 32
        token = settings.token_path.read_text(encoding="utf-8").strip()
        assert len(token) >= 32  # urlsafe-base64 inflation
        started_calls = [
            call for call in captured_log.info.call_args_list if call.args == ("daemon.started",)
        ]

    assert len(started_calls) == 1
    assert started_calls[0].kwargs["version"] == __version__
    assert started_calls[0].kwargs["milestone"] == __milestone__


def test_host_header_check_rejects_non_loopback(settings: Settings) -> None:
    """Non-loopback Host: header is rejected with 421 even on whitelisted paths."""
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:5180") as client:
        resp = client.get("/api/v1/health", headers={"host": "example.com"})
        assert resp.status_code == 421


def test_startup_reconciles_running_action_calls(settings: Settings) -> None:
    """A daemon restart makes orphaned background action outcomes explicit."""
    upgrade_to_head(settings)
    engine = make_engine(settings.db_path)
    with Session(engine) as session:
        project = Project(
            slug="startup-action-reconciliation",
            name="Startup action reconciliation",
            domain="example.test",
            locale="en-US",
        )
        session.add(project)
        session.flush()
        call = ActionCall(
            project_id=int(project.id),
            action_key="builtin.utils.ftp.upload",
            plugin_slug="builtin-utils-ftp",
            operation="file.upload",
            status=ActionCallStatus.RUNNING,
        )
        session.add(call)
        session.commit()
        call_id = int(call.id)
    engine.dispose()

    app = create_app(settings)
    with (
        TestClient(app, base_url="http://127.0.0.1:5180"),
        Session(app.state.engine) as session,
    ):
        reconciled = session.get(ActionCall, call_id)

    assert reconciled is not None
    assert reconciled.status == ActionCallStatus.FAILED
    assert reconciled.completed_at is not None
    assert reconciled.error == "daemon-restart-orphan"
    assert reconciled.response_json is not None
    assert reconciled.response_json["outcome_unknown"] is True
    assert reconciled.response_json["retry_safe"] is False


def test_restore_failure_still_closes_native_runtime_scheduler_and_engine(
    settings: Settings,
    monkeypatch: MonkeyPatch,
) -> None:
    class Runtime:
        closed = False

        async def close_all(self) -> None:
            self.closed = True

    runtime = Runtime()

    async def restore_failure(*_args, **_kwargs) -> None:
        raise RuntimeError("native restore failed")

    monkeypatch.setattr(server_module, "build_telegram_runtime", lambda *_args: runtime)
    monkeypatch.setattr(server_module, "restore_telegram_accounts", restore_failure)
    app = create_app(settings)

    with (
        pytest.raises(RuntimeError, match="native restore failed"),
        TestClient(app, base_url="http://127.0.0.1:5180"),
    ):
        pass

    assert runtime.closed is True
    assert app.state.scheduler_running is False
    assert app.state.scheduler.running is False


def test_native_close_failure_still_stops_scheduler_and_disposes_runtime_state(
    settings: Settings,
    monkeypatch: MonkeyPatch,
) -> None:
    class Runtime:
        closed = False

        async def close_all(self) -> None:
            self.closed = True
            raise RuntimeError("native close failed")

    runtime = Runtime()

    async def no_restore(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(server_module, "build_telegram_runtime", lambda *_args: runtime)
    monkeypatch.setattr(server_module, "restore_telegram_accounts", no_restore)
    app = create_app(settings)

    with (
        pytest.raises(RuntimeError, match="native close failed"),
        TestClient(app, base_url="http://127.0.0.1:5180"),
    ):
        pass

    assert runtime.closed is True
    assert app.state.scheduler_running is False
    assert app.state.scheduler.running is False


@pytest.mark.asyncio
async def test_startup_preserves_queued_delivery_until_telegram_restore_finishes(
    settings: Settings,
    monkeypatch: MonkeyPatch,
    tmp_path,
) -> None:
    from datetime import UTC, datetime

    from sqlmodel import select

    from stackos.db.models import Credential, DurableActionItem
    from stackos.repositories.projects import IntegrationCredentialRepository
    from tests.integration.test_repositories.test_durable_action_dispatcher import (
        _DispatchConnector,
        _dispatcher,
        _job,
        _seed_database,
    )

    # This regression covers an installed restart. Finish fresh schema setup
    # before create_app establishes the seed used by synthetic Account backing.
    upgrade_to_head(settings)
    app = create_app(settings)
    queue_engine, project_id, account_ref, call_id = _seed_database(tmp_path)
    now = datetime.now(UTC).replace(tzinfo=None)
    _job(queue_engine, project_id, account_ref, call_id, now)
    with Session(queue_engine) as session:
        credential = session.exec(
            select(Credential).where(Credential.credential_ref == account_ref)
        ).one()
        credential.status = "pending"
        session.add(credential)
        # The dispatcher's seeding helper deliberately uses placeholder bytes;
        # exercise real local decryption here instead of its usual auth mock.
        IntegrationCredentialRepository(session).set(
            credential_ref=account_ref,
            provider_key=credential.provider_key,
            integration_credential_id=credential.integration_credential_id,
            secret_payload=b"synthetic-test-key",
            commit=False,
        )
        session.commit()

    restore_started, restore_release = asyncio.Event(), asyncio.Event()
    startup_complete, stop = asyncio.Event(), asyncio.Event()
    connector = _DispatchConnector()
    dispatcher_started = False

    class Runtime:
        async def close_all(self):
            pass

    class Dispatcher:
        def __init__(self, **_kwargs):
            pass

        async def start(self):
            nonlocal dispatcher_started
            dispatcher_started = True
            await _dispatcher(queue_engine, connector).run_once(now=now)

        async def stop(self):
            pass

    async def restore(*_args):
        restore_started.set()
        await restore_release.wait()
        with Session(queue_engine) as session:
            credential = session.exec(
                select(Credential).where(Credential.credential_ref == account_ref)
            ).one()
            credential.status = "connected"
            session.add(credential)
            session.commit()

    monkeypatch.setattr(server_module, "build_telegram_runtime", lambda *_args: Runtime())
    monkeypatch.setattr(server_module, "restore_telegram_accounts", restore)
    monkeypatch.setattr(server_module, "DurableActionDispatcher", Dispatcher)

    async def run_lifespan():
        async with app.router.lifespan_context(app):
            startup_complete.set()
            await stop.wait()

    task = asyncio.create_task(run_lifespan())
    try:
        await asyncio.wait_for(restore_started.wait(), timeout=5)
        assert dispatcher_started is False
        assert startup_complete.is_set() is False
        with Session(queue_engine) as session:
            item = session.exec(select(DurableActionItem)).one()
            assert item.state == "pending"
            assert item.attempt_count == 0
        assert connector.requests == []
        restore_release.set()
        await asyncio.wait_for(startup_complete.wait(), timeout=5)
        assert dispatcher_started is True
        with Session(queue_engine) as session:
            item = session.exec(select(DurableActionItem)).one()
            assert item.state == "succeeded", item.error
            assert item.error is None
        assert len(connector.requests) == 1
    finally:
        restore_release.set()
        stop.set()
        await asyncio.wait_for(task, timeout=5)
        queue_engine.dispose()
