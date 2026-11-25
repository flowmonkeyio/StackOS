"""Local execution service bootstrap. Application state stays on the server.

Only connection configuration, native sessions, PID and logs belong here.
One outbound consumer delegates finite invocations to the existing native host.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from secrets import compare_digest
from threading import Event, Thread

import anyio
import httpx
from fastapi import FastAPI, HTTPException, Request
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from stackos.auth import ensure_token
from stackos.config import Settings, get_settings
from stackos.local_runtime.connection import LocalBinding, LocalConnectionStore, check_connection
from stackos.local_runtime.native import NativeAgentRunner
from stackos.local_runtime.worker import OutboundWorker
from stackos.operations.desktop import AVAILABILITY_PATH


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


def connection_state(settings: Settings, *, binding: LocalBinding | None = None) -> str:
    store = LocalConnectionStore(settings.state_dir)
    binding = binding or store.load()
    if binding is None:
        return "not_connected"
    if (binding.environment_id, binding.server_url) != (
        settings.environment_id,
        settings.server_url,
    ):
        raise ValueError("saved computer binding differs from the selected environment/server")
    store.require_binding(binding)
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
        app.state.token = ensure_token(settings.token_path)
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

    @app.post(AVAILABILITY_PATH)
    async def native_availability(request: Request):
        # The local disk token is never browser login. Only Electron main can
        # hold this transport; the project credential stays on this computer.
        if not compare_digest(
            request.headers.get("Authorization", ""), f"Bearer {app.state.token}"
        ):
            raise HTTPException(403, "Native desktop authentication required")
        store = LocalConnectionStore(settings.state_dir)
        try:
            binding = store.load()
            if binding is None:
                raise ValueError("computer not connected")
            if (binding.environment_id, binding.server_url) != (
                settings.environment_id,
                settings.server_url,
            ):
                raise ValueError("computer binding differs from selected environment")
            store.require_binding(binding)
        except (ValueError, OSError):
            raise HTTPException(503, "Selected computer connection needs attention") from None

        client = httpx.AsyncClient(
            timeout=httpx.Timeout(5, read=None), follow_redirects=False, trust_env=False
        )
        upstream = None
        try:
            async with asyncio.timeout(5):
                upstream = await client.send(
                    client.build_request(
                        "POST",
                        binding.server_url + AVAILABILITY_PATH,
                        headers={
                            "Authorization": f"Bearer {binding.credential.get_secret_value()}",
                            "Origin": binding.server_url,
                        },
                    ),
                    stream=True,
                )
            upstream.raise_for_status()
        except (httpx.HTTPError, TimeoutError):
            with anyio.CancelScope(shield=True):
                if upstream is not None:
                    await upstream.aclose()
                await client.aclose()
            raise HTTPException(503, "Selected computer availability is unavailable") from None
        except BaseException:
            with anyio.CancelScope(shield=True):
                await client.aclose()
            raise

        response = upstream

        class AvailabilityStream(StreamingResponse):
            async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
                try:
                    await super().__call__(scope, receive, send)
                finally:
                    # Also retire upstream if the downstream dies while sending
                    # headers, before the body iterator has ever been entered.
                    with anyio.CancelScope(shield=True):
                        await response.aclose()
                        await client.aclose()

        async def events():
            async for chunk in response.aiter_bytes():
                yield chunk

        return AvailabilityStream(
            events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
        )

    @app.get("/api/v1/health")
    def health():
        try:
            binding = LocalConnectionStore(settings.state_dir).load()
            state = connection_state(settings, binding=binding)
        except (ValueError, OSError, httpx.HTTPError):
            raise HTTPException(503, "Selected computer connection needs attention") from None
        return {
            "status": "ok",
            "process_role": "runtime",
            "environment_id": settings.environment_id,
            "server_url": settings.server_url,
            "connection_state": state,
            **(
                {"connection_id": binding.connection_id, "project_id": binding.project_id}
                if binding is not None
                else {}
            ),
        }

    return app
