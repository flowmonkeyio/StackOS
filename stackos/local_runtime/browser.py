"""Local adapter for existing browser operations; server retains metadata only."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

from stackos.browser.runtime import (
    browser_profile_dir,
    get_browser_runtime,
    gstack_state_file,
    safe_browser_key,
)
from stackos.cli.server_session import ServerSession
from stackos.config import Settings
from stackos.local_runtime.connection import LocalBinding, LocalConnectionStore
from stackos.operations.browser import operation_specs
from stackos.operations.responses import resolve_response_mode, shape_operation_response
from stackos.repositories.base import ValidationError
from stackos.repositories.browser import BrowserRepository, BrowserSessionOut

BROWSER_OPERATIONS = frozenset(spec.name for spec in operation_specs())


def browser_data_dir(settings: Settings, binding: LocalBinding) -> Path:
    # A different signed-in owner must never adopt this computer's saved cookies.
    owner = json.dumps([binding.server_url, binding.environment_id, binding.user_id])
    return settings.data_dir / "browser-owners" / hashlib.sha256(owner.encode()).hexdigest()


def session_args(settings: Settings, binding: LocalBinding, session_ref: str) -> dict[str, Any]:
    if BrowserRepository.project_id_from_session_ref(session_ref) != binding.project_id:
        raise ValueError("browser session belongs to another project")
    _, _, profile_key, session_key = session_ref.split(":")
    root = browser_data_dir(settings, binding)
    return {
        "session_ref": session_ref,
        "profile_ref": f"browser-profile:project-{binding.project_id}:{profile_key}",
        "profile_dir": browser_profile_dir(
            root, project_id=binding.project_id, profile_key=profile_key
        ),
        "state_file": gstack_state_file(
            root, project_id=binding.project_id, profile_key=profile_key, session_key=session_key
        ),
        # Runtime binaries stay in the selected computer's existing installation.
        "data_dir": settings.data_dir,
    }


def call_browser(
    settings: Settings,
    name: str,
    arguments: dict[str, Any],
    *,
    expected_binding: LocalBinding | None = None,
    folder: Path | None = None,
    surface: str = "cli",
) -> dict[str, Any]:
    if settings.process_role != "runtime" or name not in BROWSER_OPERATIONS:
        raise ValueError("browser execution requires the connected computer")
    spec = next(spec for spec in operation_specs() if spec.name == name)
    response_mode = resolve_response_mode(spec, arguments, surface=surface)
    inp = spec.input_model.model_validate(arguments)
    store = LocalConnectionStore(settings.state_dir)
    binding = store.load()
    if binding is None or (expected_binding is not None and binding != expected_binding):
        raise ValueError("selected computer binding unavailable")
    if (binding.server_url, binding.environment_id) != (
        settings.server_url,
        settings.environment_id,
    ):
        raise ValueError("selected computer belongs to another environment")
    if inp.project_id != binding.project_id:
        raise ValueError("browser operation requires the connected project")
    store.require_folder(binding, folder or Path.cwd())
    headers = ServerSession(settings).headers(
        require_connection=True,
        folder=folder or Path.cwd(),
        expected_binding=binding,
    )
    with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
        # Do not silently execute when the selected local service is closed or foreign.
        health = client.get(f"http://{settings.host}:{settings.port}/api/v1/health")
        health.raise_for_status()
        observed = health.json()
        if any(
            observed.get(k) != v
            for k, v in {
                "process_role": "runtime",
                "environment_id": binding.environment_id,
                "server_url": binding.server_url,
                "connection_state": "authorized",
                "connection_id": binding.connection_id,
                "project_id": binding.project_id,
            }.items()
        ):
            raise ValueError("selected computer runtime unavailable or foreign")
        # Existing server dispatcher enforces project ownership and run grants first.
        response = client.post(
            binding.server_url + f"/api/v1/operations/{name}/call",
            json={"arguments": {**arguments, "response_mode": "raw"}},
            headers={**headers, "X-StackOS-Client-Surface": "cli"},
        )
        if response.status_code != 200:
            raise ValidationError(
                "selected server refused the browser operation",
                data={"status_code": response.status_code, "repair": response.json()},
            )
        result = response.json()
    store.require_binding(binding)
    expected_ref = arguments.get("session_ref")
    if name == "browser.session.start":
        profile_ref = arguments.get("profile_ref")
        profile_key = (
            profile_ref.rsplit(":", 1)[-1]
            if profile_ref
            else safe_browser_key(arguments.get("profile_key", "default"))
        )
        expected_ref = (
            f"browser-session:project-{binding.project_id}:{profile_key}:"
            + safe_browser_key(arguments.get("session_key", "default"))
        )
    result = asyncio.run(_observe(settings, binding, name, result, expected_ref=expected_ref))
    store.require_binding(binding)
    return shape_operation_response(spec, result, response_mode=response_mode)


async def _observe(
    settings: Settings,
    binding: LocalBinding,
    name: str,
    result: dict[str, Any],
    *,
    expected_ref: str | None = None,
) -> dict[str, Any]:
    if name == "browser.runtime.status":
        runtime = get_browser_runtime()
        status = runtime.status(data_dir=settings.data_dir).to_dict(project_id=binding.project_id)
        states = await runtime.discover_sessions(
            project_id=binding.project_id,
            data_dir=settings.data_dir,
            state_root=browser_data_dir(settings, binding),
        )
        status["live_session_refs"] = [ref for ref, state in states.items() if state.owned]
        return status
    if name.startswith("browser.profile."):
        return result

    async def selected(raw: dict[str, Any], *, context: bool) -> dict[str, Any]:
        row = BrowserSessionOut.model_validate(raw)
        if expected_ref is not None and row.session_ref != expected_ref:
            raise ValueError("server returned another browser session")
        args = session_args(settings, binding, row.session_ref)
        if row.project_id != binding.project_id or row.profile_ref != args["profile_ref"]:
            raise ValueError("server returned foreign browser metadata")
        runtime = get_browser_runtime()
        method = {
            "browser.session.start": runtime.start_session,
            "browser.session.stop": runtime.stop_session,
        }.get(name, runtime.inspect_session)
        state = await method(**args)
        native = await runtime.session_context(**args, observed=state) if context else None
        return row.model_copy(
            update={
                "status": state.status,
                "healthy": state.healthy if state.owned else None,
                "repair": state.repair,
                "native_cli": native.to_dict() if native else None,
                "cli_argv": ["stackos.browser", "--session", row.session_ref],
            }
        ).model_dump(mode="json")

    if name == "browser.session.list":
        return {**result, "items": [await selected(row, context=False) for row in result["items"]]}
    if "data" in result:
        return {**result, "data": await selected(result["data"], context=name.endswith("start"))}
    return await selected(result, context=True)
