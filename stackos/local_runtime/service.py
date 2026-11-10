"""Local execution service bootstrap. Application state stays on the server.

Only connection configuration, native sessions, PID and logs belong here.
The native execution owner attaches the outbound invocation consumer separately.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException

from stackos.config import Settings, get_settings
from stackos.local_runtime.connection import LocalConnectionStore, check_connection


def connection_state(settings: Settings) -> str:
    store = LocalConnectionStore(settings.state_dir)
    binding = store.load()
    if binding is None:
        return "not_connected"
    if (binding.environment_id, binding.server_url) != (
        settings.environment_id,
        settings.server_url,
    ):
        raise ValueError("saved computer binding differs from the selected environment/server")
    store.require_folder(binding, Path(binding.root))
    with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
        check_connection(client, binding)
    return "authorized"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.process_role != "runtime":
        raise ValueError("local execution service requires an explicit runtime environment")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.ensure_dirs()
        connection_state(settings)  # Refuse stale/foreign configuration before listening.
        yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings

    @app.get("/api/v1/health")
    def health():
        try:
            state = connection_state(settings)
        except (ValueError, OSError, httpx.HTTPError):
            raise HTTPException(503, "Selected computer connection needs attention") from None
        return {
            "status": "ok",
            "process_role": "runtime",
            "environment_id": settings.environment_id,
            "server_url": settings.server_url,
            "connection_state": state,
        }

    return app
