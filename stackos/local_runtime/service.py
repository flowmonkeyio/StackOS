"""Local execution service bootstrap. Application state stays on the server.

Only connection configuration, native sessions, PID and logs belong here.
One outbound consumer delegates finite invocations to the existing native host.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from threading import Event, Thread

import httpx
from fastapi import FastAPI, HTTPException

from stackos.config import Settings, get_settings
from stackos.local_runtime.connection import LocalConnectionStore, check_connection
from stackos.local_runtime.native import NativeAgentRunner
from stackos.local_runtime.worker import OutboundWorker


def consume_invocations(settings: Settings, stop: Event) -> None:
    """Poll only the selected server. Uncertainty stops this consumer for attention.

    This is transport, not a scheduler: attempts arrive only from the server's
    existing controller. Shutdown fences admission without guessing or killing a
    running native process. The thread owns its client until the last report.
    """
    store = LocalConnectionStore(settings.state_dir)
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
        runner = NativeAgentRunner(
            store,
            mcp_config_ref=settings.native_mcp_config_ref,
            launch_allowed=lambda: not stop.is_set(),
        )
        worker = OutboundWorker(client, store, runner)
        while not stop.is_set():
            try:
                binding = store.load()
                if binding is not None:
                    if (binding.environment_id, binding.server_url) != (
                        settings.environment_id,
                        settings.server_url,
                    ):
                        raise ValueError("computer binding changed; explicit attention required")
                    worker.exchange_once()
            except httpx.TransportError:
                # Retain this worker and its exact pending report across a
                # temporary network loss. A consumed launch is never reissued;
                # a lost launch response becomes unknown on the next poll.
                pass
            except (ValueError, OSError, httpx.HTTPError):
                # No automatic reissue, reconnect or process signal. The existing
                # attempt/transport retains the uncertain outcome for its owner.
                return
            if stop.wait(1):
                return


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
        stop = Event()
        consumer = Thread(
            target=consume_invocations,
            args=(settings, stop),
            name="stackos-local-invocations",
            daemon=True,
        )
        consumer.start()
        try:
            yield
        finally:
            stop.set()
            # Never block app shutdown on an agent or treat a timeout as a stop.
            consumer.join(timeout=0)

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
