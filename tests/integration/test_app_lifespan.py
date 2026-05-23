"""Lifespan integration tests.

Verifies that creating the app (which runs the lifespan startup hook via
`TestClient` __enter__) generates seed.bin and auth.token at the configured
state dir, both with mode 0600.
"""

from __future__ import annotations

import stat

from fastapi.testclient import TestClient

from stackos.config import Settings
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
