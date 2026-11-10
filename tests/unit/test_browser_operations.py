from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from pydantic import SecretStr
from sqlmodel import Session, SQLModel

import stackos.operations.browser as browser_ops
from stackos.browser.runtime import BrowserRuntime, NativeCliContext, NativeSessionState
from stackos.db.connection import make_memory_engine
from stackos.db.models import BrowserProfile, BrowserSession, Project
from stackos.local_runtime import browser as local_browser
from stackos.local_runtime.connection import LocalBinding, LocalConnectionStore
from stackos.mcp.context import MCPContext
from stackos.mcp.server import ToolRegistry
from stackos.mcp.tools import register_all
from stackos.operations.registry import build_operation_registry
from stackos.repositories.base import ValidationError
from stackos.repositories.browser import BrowserRepository

BROWSER_OPERATIONS = {
    "browser.runtime.status",
    "browser.profile.create",
    "browser.profile.list",
    "browser.session.start",
    "browser.session.list",
    "browser.session.status",
    "browser.session.stop",
}


@pytest.mark.asyncio
async def test_online_server_never_inspects_or_launches_a_browser(
    browser_operation_context, monkeypatch
):
    project_id, session_ref, ctx, _ = browser_operation_context
    ctx.extras["settings"].process_role = "server"

    def forbidden():
        pytest.fail("server attempted native browser access")

    monkeypatch.setattr(browser_ops, "get_browser_runtime", forbidden)
    registry = build_operation_registry()
    for name, args in (
        ("browser.runtime.status", {}),
        ("browser.session.list", {}),
        ("browser.session.status", {"session_ref": session_ref}),
        ("browser.session.stop", {"session_ref": session_ref}),
        ("browser.session.start", {"profile_key": "default"}),
    ):
        spec = registry.get(name)
        result = await spec.handler(spec.input_model(project_id=project_id, **args), ctx, None)
        if name == "browser.runtime.status":
            assert not result.package_installed
        else:
            result = getattr(result, "data", result)
            rows = result.items if hasattr(result, "items") else [result]
            assert all(row.native_cli is None and row.status == "unavailable" for row in rows)


@pytest.fixture
def browser_operation_context(tmp_path: Path):
    engine = make_memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        project = Project(
            slug="browser-unit",
            name="Browser Unit",
            domain="browser-unit.example.test",
            locale="en-US",
            is_active=True,
        )
        session.add(project)
        session.commit()
        session.refresh(project)
        assert project.id is not None
        profile = BrowserProfile(
            project_id=project.id,
            profile_key="default",
            name="Default",
            profile_ref=f"browser-profile:project-{project.id}:default",
            metadata_json={"purpose": "synthetic proof"},
        )
        session.add(profile)
        session.commit()
        session.refresh(profile)
        assert profile.id is not None
        session_ref = f"browser-session:project-{project.id}:default:default"
        session.add(
            BrowserSession(
                project_id=project.id,
                profile_id=profile.id,
                session_ref=session_ref,
                status="running",
            )
        )
        session.commit()
        ctx = MCPContext(
            session=session,
            request_id="browser-unit",
            project_id=project.id,
            extras={"settings": SimpleNamespace(data_dir=tmp_path / "data")},
        )
        yield project.id, session_ref, ctx, session
    engine.dispose()


def test_browser_surface_contains_only_lifecycle_operations() -> None:
    registry = build_operation_registry()
    assert {
        spec.name for spec in registry.all() if spec.name.startswith("browser.")
    } == BROWSER_OPERATIONS
    for surface in ("mcp", "rest", "cli"):
        assert {
            spec.name for spec in registry.by_surface(surface) if spec.name.startswith("browser.")
        } == BROWSER_OPERATIONS
    tools = ToolRegistry()
    register_all(tools)
    assert tools.get("browser.session.list").read_only
    with pytest.raises(KeyError):
        tools.get("browser.cli.run")


def test_session_ref_parser_is_canonical_and_does_not_normalize() -> None:
    ref = "browser-session:project-42:default:main"

    assert BrowserRepository.project_id_from_session_ref(ref) == 42
    for invalid in (
        "browser-session:project-0:default:main",
        "browser-session:project-42:Default:main",
        "browser-session:project-42:default:main:extra",
        "browser-session:project-42:default:main/escape",
    ):
        with pytest.raises(ValidationError):
            BrowserRepository.project_id_from_session_ref(invalid)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "owned", "healthy"),
    [
        ("running", True, True),
        ("failed", True, False),
        ("stopped", False, False),
        ("stale", False, False),
    ],
)
async def test_selected_status_keeps_observation_and_returns_generic_handoff(
    browser_operation_context,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    owned: bool,
    healthy: bool,
) -> None:
    project_id, session_ref, ctx, _session = browser_operation_context
    native = NativeCliContext(
        executable="/runtime/gstack/browse",
        cwd="/owned/cwd",
        env={"BROWSE_STATE_FILE": "/owned/.gstack/browse.json"},
    )
    state = NativeSessionState(
        session_ref=session_ref,
        profile_ref=f"browser-profile:project-{project_id}:default",
        status=status,  # type: ignore[arg-type]
        owned=owned,
        healthy=healthy,
        pid=123 if owned else None,
        repair="busy" if status == "failed" else None,
    )
    runtime = BrowserRuntime()
    context_calls: list[dict[str, object]] = []

    async def inspect_session(**kwargs: object) -> NativeSessionState:
        assert kwargs["session_ref"] == session_ref
        return state

    async def session_context(**kwargs: object) -> NativeCliContext:
        context_calls.append(kwargs)
        assert kwargs["observed"] == state
        return native

    monkeypatch.setattr(runtime, "inspect_session", inspect_session)
    monkeypatch.setattr(runtime, "session_context", session_context, raising=False)
    monkeypatch.setattr(browser_ops, "get_browser_runtime", lambda: runtime)
    registry = build_operation_registry()
    listing = registry.get("browser.session.list")
    selected = registry.get("browser.session.status")

    listed = await listing.handler(listing.input_model(project_id=project_id), ctx, None)
    result = await selected.handler(
        selected.input_model(project_id=project_id, session_ref=session_ref), ctx, None
    )

    assert listed.items[0].native_cli is None
    assert listed.items[0].cli_argv == ["stackos.browser", "--session", session_ref]
    assert result.status == status
    assert result.healthy is (healthy if owned else None)
    assert result.native_cli == native.to_dict()
    assert result.cli_argv == ["stackos.browser", "--session", session_ref]
    assert len(context_calls) == 1


@pytest.fixture
def connected_browser(tmp_path, monkeypatch, browser_operation_context):
    project_id, session_ref, _ctx, session = browser_operation_context
    folder = tmp_path / "project"
    folder.mkdir()
    info = folder.stat()
    settings = SimpleNamespace(
        process_role="runtime",
        data_dir=tmp_path / "native",
        state_dir=tmp_path / "state",
        host="127.0.0.1",
        port=20461,
        server_url="http://127.0.0.1:20460",
        environment_id="synthetic-browser",
    )
    binding = LocalBinding(
        server_url=settings.server_url,
        environment_id=settings.environment_id,
        user_id="owner",
        project_id=project_id,
        connection_id="computer",
        credential=SecretStr("synthetic"),
        root=str(folder.resolve()),
        root_device=info.st_dev,
        root_inode=info.st_ino,
    )
    store = LocalConnectionStore(settings.state_dir)
    with store.reserve() as stream:
        payload = binding.model_dump(mode="json")
        payload["credential"] = "synthetic"
        json.dump(payload, stream)
    repo = BrowserRepository(session)
    row = repo.session_out(*repo.get_session(project_id=project_id, session_ref=session_ref))
    state = {
        "response": row.model_dump(mode="json"),
        "calls": [],
        "health": "authorized",
    }

    def respond(request):
        state["calls"].append(request)
        if request.url.port == settings.port:
            return httpx.Response(
                200,
                json={
                    "process_role": "runtime",
                    "environment_id": settings.environment_id,
                    "server_url": settings.server_url,
                    "connection_state": state["health"],
                    "connection_id": state.get("connection_id", binding.connection_id),
                    "project_id": binding.project_id,
                },
            )
        assert request.headers["Cookie"] == "opaque-owner-session"
        return httpx.Response(200, json=state["response"])

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(**kw, transport=httpx.MockTransport(respond))
    )
    monkeypatch.setattr(local_browser.ServerSession, "__init__", lambda self, settings: None)
    headers = Mock(return_value={"Cookie": "opaque-owner-session"})
    monkeypatch.setattr(local_browser.ServerSession, "headers", headers)
    monkeypatch.chdir(folder)
    return settings, binding, session_ref, state, headers


def test_connected_status_uses_local_context_and_rechecks_binding(connected_browser, monkeypatch):
    settings, binding, ref, state, headers = connected_browser
    runtime = Mock()
    observed = NativeSessionState(
        session_ref=ref,
        profile_ref=state["response"]["profile_ref"],
        status="running",
        owned=True,
        healthy=True,
        pid=123,
    )
    args = local_browser.session_args(settings, binding, ref)
    native = NativeCliContext(
        executable="/local/browse",
        cwd="/local/work",
        env={
            "BROWSE_STATE_FILE": str(args["state_file"]),
            "CHROMIUM_PROFILE": str(args["profile_dir"]),
        },
    )

    async def inspect(**kwargs):
        assert kwargs == args
        return observed

    async def context(**kwargs):
        assert kwargs == {**args, "observed": observed}
        return native

    runtime.inspect_session = inspect
    runtime.session_context = context
    monkeypatch.setattr(local_browser, "get_browser_runtime", lambda: runtime)
    state["response"]["native_cli"] = {"executable": "/foreign/server/browse"}
    result = local_browser.call_browser(
        settings,
        "browser.session.status",
        {
            "project_id": binding.project_id,
            "session_ref": ref,
            "response_mode": "raw",
        },
    )
    assert result["native_cli"] == native.to_dict()
    assert result["status"] == "running"
    assert headers.call_args.kwargs["expected_binding"] == binding
    assert not (settings.data_dir / "stackos.db").exists()
    assert local_browser.browser_data_dir(settings, binding) != local_browser.browser_data_dir(
        settings, binding.model_copy(update={"user_id": "foreign"})
    )


@pytest.mark.parametrize(
    "failure", ["project", "session", "profile", "runtime", "connection", "folder", "server"]
)
def test_connected_browser_refuses_wrong_context_before_native(
    connected_browser, monkeypatch, failure
):
    settings, binding, ref, state, _ = connected_browser
    native = Mock(side_effect=AssertionError("must not touch a local browser"))
    monkeypatch.setattr(local_browser, "get_browser_runtime", native)
    args = {"project_id": binding.project_id, "session_ref": ref}
    if failure == "project":
        args["project_id"] += 1
    elif failure == "session":
        state["response"]["session_ref"] = ref.rsplit(":", 1)[0] + ":foreign"
    elif failure == "profile":
        state["response"]["profile_ref"] += "foreign"
    elif failure == "runtime":
        state["health"] = "not_connected"
    elif failure == "connection":
        state["connection_id"] = "another-computer"
    elif failure == "folder":
        LocalConnectionStore(settings.state_dir).path.unlink()
    else:
        settings.process_role = "server"
    with pytest.raises(ValueError):
        local_browser.call_browser(settings, "browser.session.status", args)
    native.assert_not_called()
