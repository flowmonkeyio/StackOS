"""Session contention must be an immediate retry, not an async-server deadlock."""

from __future__ import annotations

import hashlib
import os
import secrets
import time

import pytest
from fastapi import HTTPException
from sqlalchemy import delete
from sqlmodel import Session, col
from starlette.requests import Request

from stackos.auth import ServerAuth, session_cookie_name
from stackos.config import get_settings
from stackos.db.connection import make_settings_engine
from stackos.db.migrate import upgrade_to_head
from stackos.db.model_auth import UserSession


def test_refresh_session_lock_returns_retryable_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    selected_file = os.environ.get("STACKOS_TEST_POSTGRES_ENV_FILE")
    if not selected_file:
        pytest.skip("requires explicit disposable STACKOS_TEST_POSTGRES_ENV_FILE")
    monkeypatch.setenv("STACKOS_ENV_FILE", selected_file)
    settings = get_settings()
    assert settings.process_role == "server"
    upgrade_to_head(settings)
    engine = make_settings_engine(settings)
    opaque = secrets.token_hex(32)
    digest = hashlib.sha256(opaque.encode()).hexdigest()
    row = UserSession(
        token_digest=digest,
        environment_id=settings.environment_id,
        server_origin=settings.server_url,
        user_id="00000000-0000-0000-0000-000000000001",
        csrf_digest="not-used-by-resolve",
        encrypted_payload=b"not-decrypted",
        nonce=b"not-decrypted",
        expires_at=int(time.time()) + 60,
    )
    request = Request(
        {
            "type": "http",
            "headers": [
                (b"cookie", f"{session_cookie_name(settings)}={opaque}".encode()),
            ],
        }
    )
    try:
        with Session(engine) as setup:
            setup.add(row)
            setup.commit()
        with Session(engine) as first, Session(engine) as second:
            ServerAuth(settings, first).resolve(request, lock=True)
            with pytest.raises(HTTPException) as conflict:
                ServerAuth(settings, second).resolve(request, lock=True)
            assert conflict.value.status_code == 409
            assert conflict.value.detail == {"code": "session_busy", "retryable": True}
            first.rollback()
            assert ServerAuth(settings, second).resolve(request, lock=True).token_digest == digest
    finally:
        with engine.begin() as connection:
            connection.execute(delete(UserSession).where(col(UserSession.token_digest) == digest))
        engine.dispose()
