from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel

import stackos.operations.browser as browser_ops
from stackos.browser.runtime import BrowserRuntime, NativeCliContext, NativeSessionState
from stackos.db.connection import make_memory_engine
from stackos.db.models import BrowserProfile, BrowserSession, Project
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
