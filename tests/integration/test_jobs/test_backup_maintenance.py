"""Generic backup maintenance registration, persisted opt-in, and drain proof."""

from datetime import UTC, datetime, timedelta
from threading import Event, Thread

from fastapi.testclient import TestClient

from stackos import backups
from stackos.server import BACKUP_MAINTENANCE_JOB_ID, create_app


def test_restart_keeps_disabled_default_and_explicit_policy(settings, tmp_path):
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:5180"):
        job = app.state.scheduler.get_job(BACKUP_MAINTENANCE_JOB_ID)
        assert job is not None
        assert job.executor == "long"
        assert job.max_instances == 1 and job.coalesce
        assert len([item for item in app.state.scheduler.get_jobs() if item.id == job.id]) == 1
        assert job.func(**job.kwargs) == {"status": "skipped", "reason": "disabled"}
        assert backups.backup_status(settings)["last_success"] is None
        private, public = tmp_path / "keys/recovery.pem", tmp_path / "keys/public.pem"
        backups.generate_recovery_key(settings, private, public)
        backups.update_policy(
            settings, {"recipient_public_key": public.read_text(), "enabled": True}
        )
    restarted = create_app(settings)
    with TestClient(restarted, base_url="http://127.0.0.1:5180"):
        job = restarted.state.scheduler.get_job(BACKUP_MAINTENANCE_JOB_ID)
        assert backups.backup_status(settings)["enabled"] is True
        value = job.func(**job.kwargs)
        assert value["status"] == "success"
        previous = backups.backup_status(settings)["last_success"]
        backups.update_policy(settings, {"enabled": False})
        assert job.func(**job.kwargs) == {"status": "skipped", "reason": "disabled"}
        assert backups.backup_status(settings)["last_success"] == previous
    disabled = create_app(settings)
    with TestClient(disabled, base_url="http://127.0.0.1:5180"):
        assert backups.backup_status(settings)["enabled"] is False
        assert (
            disabled.state.scheduler.get_job(BACKUP_MAINTENANCE_JOB_ID).func(
                settings,
                now=datetime.now(UTC) + timedelta(days=3),
            )["status"]
            == "skipped"
        )


def test_lifespan_drains_active_long_backup_before_disposing_engine(settings, monkeypatch):
    import stackos.server as server

    started, release, finished = Event(), Event(), Event()
    shutdown_called, disposed = Event(), Event()
    disposal_states = []

    def held_backup(**_kwargs):
        started.set()
        assert release.wait(10), "test worker was not released"
        finished.set()

    monkeypatch.setattr(server, "run_due_backup", held_backup)
    app = create_app(settings)
    client = TestClient(app, base_url="http://127.0.0.1:5180")
    client.__enter__()
    scheduler = app.state.scheduler
    original_shutdown = scheduler.shutdown
    original_dispose = app.state.engine.dispose

    def shutdown(*args, **kwargs):
        shutdown_called.set()
        return original_shutdown(*args, **kwargs)

    def dispose(*args, **kwargs):
        disposal_states.append(finished.is_set())
        disposed.set()
        return original_dispose(*args, **kwargs)

    monkeypatch.setattr(scheduler, "shutdown", shutdown)
    monkeypatch.setattr(app.state.engine, "dispose", dispose)
    scheduler.modify_job(BACKUP_MAINTENANCE_JOB_ID, next_run_time=datetime.now(UTC))
    scheduler.wakeup()
    assert started.wait(5), "long executor did not start"
    failures = []

    def exit_client():
        try:
            client.__exit__(None, None, None)
        except BaseException as exc:
            failures.append(exc)

    thread = Thread(target=exit_client)
    thread.start()
    try:
        assert shutdown_called.wait(5)
        assert not disposed.wait(0.2), "engine disposed with a backup still running"
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert failures == []
    # Both SQLAlchemyJobStore.shutdown and the lifespan may dispose this engine.
    assert disposal_states and all(disposal_states)
    assert not app.state.scheduler_running
