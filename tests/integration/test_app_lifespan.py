"""Lifespan integration tests.

Verifies that creating the app (which runs the lifespan startup hook via
`TestClient` __enter__) generates seed.bin and auth.token at the configured
state dir, both with mode 0600.
"""

from __future__ import annotations

import stat

from fastapi.testclient import TestClient
from sqlmodel import Session

from stackos.config import Settings
from stackos.db.connection import make_engine
from stackos.db.migrate import upgrade_to_head
from stackos.db.model_core import ActionCall, Project
from stackos.db.model_enums import ActionCallStatus
from stackos.server import create_app


def _mode(p) -> int:  # type: ignore[no-untyped-def]
    """Return the permission bits of a Path."""
    return stat.S_IMODE(p.stat().st_mode)


def test_seed_and_token_generated_with_mode_0600(settings: Settings) -> None:
    """First app build generates seed.bin and auth.token both at mode 0600."""
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:5180"):
        assert settings.seed_path.exists()
        assert settings.token_path.exists()
        assert _mode(settings.seed_path) == 0o600
        assert _mode(settings.token_path) == 0o600
        # Seed is exactly 32 bytes; token is urlsafe base64 of 32 bytes
        # (43 chars, no padding required by token_urlsafe).
        assert settings.seed_path.read_bytes().__len__() == 32
        token = settings.token_path.read_text(encoding="utf-8").strip()
        assert len(token) >= 32  # urlsafe-base64 inflation


def test_state_and_data_dirs_exist_after_startup(settings: Settings) -> None:
    """Lifespan creates both XDG dirs (mkdir -p) so first-run is hands-off."""
    app = create_app(settings)
    with TestClient(app, base_url="http://127.0.0.1:5180"):
        assert settings.data_dir.is_dir()
        assert settings.state_dir.is_dir()


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
