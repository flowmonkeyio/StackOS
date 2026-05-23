"""FastAPI routers — REST surface for the UI and external clients.

``register_routers`` mounts the StackOS REST surface onto a given FastAPI
app. Exception handlers + the request-id middleware are wired here too so
``server.create_app`` stays a single ``register_routers(app)`` call.
"""

from __future__ import annotations

from fastapi import FastAPI

from content_stack.api.actions import router as actions_router
from content_stack.api.auth import router as auth_router
from content_stack.api.auth_providers import router as auth_providers_router
from content_stack.api.context import router as context_router
from content_stack.api.errors import (
    RequestIdMiddleware,
    register_error_handlers,
)
from content_stack.api.health import router as health_router
from content_stack.api.meta import router as meta_router
from content_stack.api.operations import router as operations_router
from content_stack.api.plugins import router as plugins_router
from content_stack.api.projects import router as projects_router
from content_stack.api.resources import router as resources_router
from content_stack.api.run_plans import (
    project_router as run_plans_project_router,
)
from content_stack.api.run_plans import (
    run_plan_router as run_plans_run_plan_router,
)
from content_stack.api.runs import (
    project_router as runs_project_router,
)
from content_stack.api.runs import (
    run_router as runs_run_router,
)
from content_stack.api.workflow_templates import router as workflow_templates_router


def register_routers(app: FastAPI) -> None:
    """Mount every API router + register exception handlers + request-id middleware.

    Order matters only for OpenAPI grouping (FastAPI walks ``app.routes``
    in declaration order). We start with health (whitelisted from auth)
    and then domain routers.
    """
    register_error_handlers(app)
    app.add_middleware(RequestIdMiddleware)

    # Health (M0).
    app.include_router(health_router)
    # Auth bootstrap (whitelisted; UI fetches its bearer token at boot).
    app.include_router(auth_router)
    # StackOS auth provider boundary.
    app.include_router(auth_providers_router)
    # Meta (enums, observability).
    app.include_router(meta_router)
    # Protocol-neutral StackOS operation discovery and generic calls.
    app.include_router(operations_router)
    # StackOS plugin/catalog discovery.
    app.include_router(plugins_router)
    # StackOS action audit discovery.
    app.include_router(actions_router)
    # Generic StackOS resources/artifacts.
    app.include_router(resources_router)
    # Project memory/context primitives.
    app.include_router(context_router)
    # Reusable workflow templates.
    app.include_router(workflow_templates_router)
    # Project routers land before project-scoped run plans/runs.
    app.include_router(projects_router)
    app.include_router(run_plans_project_router)
    app.include_router(run_plans_run_plan_router)
    app.include_router(runs_project_router)
    app.include_router(runs_run_router)


__all__ = ["register_routers"]
