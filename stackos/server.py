"""FastAPI app factory.

Wires middleware in the order: Host header check (outermost so it runs
even on auth-whitelisted non-ingress paths) → CORS (same-origin) →
bearer-token auth.
Lifespan ensures dirs/seed/token before the first request lands.
"""

from __future__ import annotations

import os
import secrets
import stat
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, SQLModel
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from stackos import __version__
from stackos.actions import ActionRepository
from stackos.api import register_routers
from stackos.auth import BearerTokenMiddleware, derive_ui_token, ensure_token
from stackos.config import Settings, get_settings
from stackos.crypto import cleanup_old_backup
from stackos.crypto.aes_gcm import configure_seed_path
from stackos.db.connection import make_engine
from stackos.db.migrate import upgrade_to_head
from stackos.jobs.runs_reaper import (
    DEFAULT_STALE_AFTER_SECONDS,
    reap_orphaned_runs,
)
from stackos.jobs.runs_reaper import (
    make_session_factory as reaper_session_factory,
)
from stackos.jobs.scheduler import (
    REAPER_MISFIRE_GRACE_SECONDS,
    RUNS_REAPER_JOB_ID,
    build_scheduler,
)
from stackos.logging import configure_logging, get_logger
from stackos.mcp import register_mcp
from stackos.repositories.runs import RunRepository

_SEED_BYTES = 32
_REQUIRED_MODE = 0o600

# Hosts the daemon will respond to. The port-suffix variants are accepted
# because some clients send `Host: 127.0.0.1:5180`. We do *not* hard-code
# the port here so non-default ports still work; the suffix is stripped
# before comparison.
_ALLOWED_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})
_PUBLIC_INGRESS_PREFIXES: tuple[str, ...] = (
    "/api/v1/ingress/telegram",
    "/api/v1/ingress/slack",
)
_SLOW_REQUEST_MS = 100


class RequestTimingMiddleware(BaseHTTPMiddleware):
    """Expose local request timing and record actionable slow requests.

    Query strings and request bodies are deliberately excluded: timing logs
    must remain useful without becoming another place that can retain user or
    provider data.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            get_logger("stackos.http").exception(
                "http.request.failed",
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
            )
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        timing = f"stackos-http;dur={duration_ms:.2f}"
        existing_timing = response.headers.get("server-timing")
        response.headers["server-timing"] = (
            f"{existing_timing}, {timing}" if existing_timing else timing
        )
        response.headers["x-stackos-request-duration-ms"] = f"{duration_ms:.2f}"

        if duration_ms >= _SLOW_REQUEST_MS:
            content_length = response.headers.get("content-length")
            get_logger("stackos.http").warning(
                "http.request.slow",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
                response_bytes=(
                    int(content_length) if content_length and content_length.isdigit() else None
                ),
            )
        return response


class HostHeaderMiddleware(BaseHTTPMiddleware):
    """Reject requests whose `Host:` header is not loopback with 421.

    Defence-in-depth against DNS rebinding and stray cross-origin probes
    (curl from another machine, browser plugins, etc.). The CLI already
    refuses non-loopback `--host`, so this is the runtime backstop.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Strip the optional `:port` suffix and compare to the allow-list."""
        host_header = request.headers.get("host", "")
        # Strip port from forms like "127.0.0.1:5180" or "[::1]:5180".
        if host_header.startswith("["):
            # IPv6 literal: "[::1]:5180" -> "[::1]"
            host_only, _, _ = host_header.partition("]")
            host_only = host_only + "]"
        else:
            host_only = host_header.split(":", 1)[0] if host_header else ""

        if host_only not in _ALLOWED_HOSTS and not _is_public_ingress_path(request.url.path):
            return JSONResponse(
                {"detail": f"Host header {host_header!r} is not loopback"},
                status_code=421,
            )
        return await call_next(request)


def _is_public_ingress_path(path: str) -> bool:
    """Allow tunnel/deployed hosts only on provider-verified webhook paths."""
    return any(
        path == prefix or path.startswith(prefix + "/") for prefix in _PUBLIC_INGRESS_PREFIXES
    )


def _ensure_seed(seed_path: Path) -> None:
    """Generate `seed.bin` if absent; refuse to start if mode is wrong.

    The seed only matters once integrations land (M5), but we generate it at
    M0 so install ordering doesn't matter and so doctor can verify mode 0600
    on every fresh install.
    """
    if seed_path.exists():
        mode = stat.S_IMODE(seed_path.stat().st_mode)
        if mode != _REQUIRED_MODE:
            raise RuntimeError(
                f"seed file at {seed_path} has mode {oct(mode)}; expected {oct(_REQUIRED_MODE)}"
            )
        return
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(seed_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _REQUIRED_MODE)
    try:
        os.write(fd, secrets.token_bytes(_SEED_BYTES))
    finally:
        os.close(fd)
    os.chmod(seed_path, _REQUIRED_MODE)


def _build_lifespan(
    settings: Settings,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Build a FastAPI lifespan for the given settings (closure binds settings)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Startup: ensure dirs, seed, token, engine. Shutdown: clean log."""
        settings.ensure_dirs()
        configure_logging(log_path=settings.log_path, level=settings.log_level)
        log = get_logger("stackos.server")

        _ensure_seed(settings.seed_path)
        # M4: register the seed path with the crypto layer so encrypt/decrypt
        # can resolve it without each call passing it explicitly. We also
        # delete the rotation backup left over from the previous boot
        # (PLAN.md L1142 — bak kept for one boot only).
        configure_seed_path(settings.seed_path)
        cleanup_old_backup(settings.seed_path)
        token = ensure_token(settings.token_path)

        # Bring the SQLite schema to the tracked Alembic head on every daemon
        # boot. The follow-up ``create_all`` remains a no-op safety net for
        # tests and partially-initialised dev databases.
        upgrade_to_head(settings)
        engine = make_engine(settings.db_path)

        SQLModel.metadata.create_all(engine)
        app.state.settings = settings
        app.state.token = token
        app.state.ui_token = derive_ui_token(token)
        app.state.engine = engine
        app.state.started_at = time.monotonic()

        with Session(engine) as session:
            reconciled_actions = ActionRepository(session).reconcile_running_calls()
            if reconciled_actions:
                log.info(
                    "daemon.action_recovery_sweep.reconciled",
                    count=reconciled_actions,
                )

        # Build the APScheduler instance + run the crash-recovery
        # sweep BEFORE registering recurring jobs (per audit BLOCKER-13).
        # The sweep's effects are idempotent (rows that aren't stale are
        # skipped) so re-running on every boot is fine.
        scheduler = build_scheduler(settings, engine)
        app.state.scheduler = scheduler

        # Crash-recovery sweep: any ``status='running' AND
        # heartbeat_at < now - 5 min`` row gets ``aborted`` with
        # ``error='daemon-restart-orphan'``. Per PLAN.md L1366-L1391
        # we don't auto-resume — we surface the orphan in the UI's
        # RunsView with a "Resumable" badge.
        with Session(engine) as session:  # type: ignore[name-defined]
            reaped = RunRepository(session).reap_stale(
                stale_after_seconds=DEFAULT_STALE_AFTER_SECONDS
            )
            if reaped:
                log.info("daemon.recovery_sweep.reaped", count=reaped)

        # Register recurring background jobs. The reaper uses the ``memory``
        # jobstore because its body closes over the daemon-local engine +
        # session factory and is not picklable.
        scheduler.add_job(
            reap_orphaned_runs,
            kwargs={
                "session_factory": reaper_session_factory(engine),
                "stale_after_seconds": DEFAULT_STALE_AFTER_SECONDS,
            },
            trigger=IntervalTrigger(minutes=5),
            id=RUNS_REAPER_JOB_ID,
            name="runs reaper (orphan sweep)",
            replace_existing=True,
            jobstore="memory",
            misfire_grace_time=REAPER_MISFIRE_GRACE_SECONDS,
        )

        scheduler.start()
        app.state.scheduler_running = True

        log.info(
            "daemon.started",
            host=settings.host,
            port=settings.port,
            version=__version__,
            milestone="M8",
            data_dir=str(settings.data_dir),
            state_dir=str(settings.state_dir),
        )
        try:
            yield
        finally:
            log.info("daemon.shutdown.clean")
            # Drain the scheduler before disposing the engine so any
            # in-flight short job finishes its DB writes cleanly.
            import contextlib as _ctx_lib

            with _ctx_lib.suppress(Exception):  # pragma: no cover — defensive
                scheduler.shutdown(wait=True)
            app.state.scheduler_running = False
            engine.dispose()

    return lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a FastAPI app with middleware, lifespan, and routes wired.

    `settings` may be supplied for tests that want isolated paths; in
    production, callers pass `None` and we resolve the global Settings.
    """
    settings = settings or get_settings()
    # Pre-flight ensure so the token exists before middleware reads it.
    settings.ensure_dirs()
    _ensure_seed(settings.seed_path)
    # Wire the crypto layer up before any request hits an integration repo.
    configure_seed_path(settings.seed_path)
    cleanup_old_backup(settings.seed_path)
    token = ensure_token(settings.token_path)
    ui_token = derive_ui_token(token)

    app = FastAPI(
        title="StackOS",
        version=__version__,
        summary="Local StackOS runtime for agents and humans.",
        description=(
            "A local Python daemon (FastAPI + SQLite/WAL + MCP Streamable HTTP) "
            "plus Vue UI for projects, plugins, workflow templates, run plans, "
            "resources, actions, and auditable runs."
        ),
        lifespan=_build_lifespan(settings),
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.ui_token = ui_token

    # Middleware order — Starlette runs the *last-added* first on the
    # request path, so we add inside-out: auth, then CORS, then host check.
    app.add_middleware(BearerTokenMiddleware, token=token, ui_token=ui_token)
    app.add_middleware(
        CORSMiddleware,
        # Same-origin only: no cross-origin browser fetches.
        allow_origins=[f"http://{settings.host}:{settings.port}"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["authorization", "content-type", "if-match", "x-request-id"],
    )
    app.add_middleware(HostHeaderMiddleware)
    app.add_middleware(RequestTimingMiddleware)

    register_routers(app)

    # Mount the MCP Streamable HTTP transport at /mcp. Bearer-token
    # middleware already covers ``/mcp/*`` via ``PROTECTED_PREFIXES``,
    # so the MCP layer never re-checks auth — every request landing on
    # a tool handler has already cleared the BearerTokenMiddleware.
    register_mcp(app)

    app.mount(
        "/generated-assets",
        StaticFiles(directory=settings.generated_assets_dir),
        name="generated-assets",
    )
    _mount_ui(app, settings)

    return app


def _mount_ui(app: FastAPI, settings: Settings) -> None:
    """Mount static UI bundle at `/`; serve a placeholder if not yet built.

    SPA-aware: for routes that aren't static files (e.g. ``/projects/12/resources``)
    we fall back to ``index.html`` so the browser-side router can resolve
    them. The fallback only fires for requests that don't match any API
    or static-file path, which avoids leaking the SPA in place of a real
    404 from the API.
    """
    _ = settings
    ui_dist = Path(__file__).parent / "ui_dist"
    index = ui_dist / "index.html"

    # Always register the placeholder/static branch *after* API routes are
    # included so router precedence is deterministic.
    if index.is_file():
        app.mount("/assets", StaticFiles(directory=ui_dist / "assets"), name="ui-assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def _ui_spa(full_path: str) -> Response:
            """Return the static asset if present, else fall back to index.html.

            Anything starting with ``api/`` or ``mcp/`` should never reach
            this handler (those routers come before the catch-all in
            ``register_routers`` ordering), but we guard with a 404 just
            in case so we don't paper over a missing API endpoint with
            the SPA shell.
            """
            if full_path.startswith(("api/", "mcp/", "generated-assets/")):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            target = ui_dist / full_path
            try:
                target_resolved = target.resolve()
                ui_dist_resolved = ui_dist.resolve()
            except OSError:
                target_resolved = target
                ui_dist_resolved = ui_dist
            if full_path and target.is_file() and ui_dist_resolved in target_resolved.parents:
                return FileResponse(target_resolved)
            return FileResponse(
                index,
                media_type="text/html",
                headers={"cache-control": "no-cache"},
            )

        _ = _ui_spa
        return

    @app.get("/", include_in_schema=False)
    async def _ui_placeholder() -> HTMLResponse:
        """Placeholder shown until `make build-ui` populates ui_dist/."""
        body = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>StackOS</title></head><body>"
            "<h1>StackOS daemon is running.</h1>"
            "<p>UI not built yet — run <code>make build-ui</code>.</p>"
            f"<p>API docs: <a href='/api/docs'>/api/docs</a></p>"
            f"<p>Version: {__version__}</p>"
            "</body></html>"
        )
        return HTMLResponse(body, status_code=200)

    # Touch the variable so linters know it's intentionally registered as a route.
    _ = _ui_placeholder
