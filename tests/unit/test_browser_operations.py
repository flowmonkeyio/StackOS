from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError as InputError
from sqlmodel import Session, SQLModel, select

import stackos.operations.browser as browser_ops
from stackos.db.connection import make_memory_engine
from stackos.db.models import (
    Artifact,
    BrowserActionReceipt,
    BrowserProfile,
    BrowserSession,
    IdempotencyKey,
    Project,
)
from stackos.mcp.context import MCPContext
from stackos.mcp.server import ToolRegistry
from stackos.mcp.tools import register_all
from stackos.operations.dispatcher import OperationDispatcher
from stackos.operations.registry import build_operation_registry
from stackos.repositories.base import NotFoundError, ValidationError

BROWSER_OPERATIONS = {
    "browser.runtime.status",
    "browser.profile.create",
    "browser.profile.list",
    "browser.session.start",
    "browser.session.list",
    "browser.session.status",
    "browser.session.stop",
    "browser.cli.run",
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


def test_native_browser_replaces_all_command_wrappers() -> None:
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
    assert not tools.get("browser.cli.run").read_only


def test_native_relay_rejects_replay_and_preserves_unrestricted_argv() -> None:
    spec = build_operation_registry().get("browser.cli.run")
    args = {"project_id": 1, "session_ref": "selected", "argv": ["unknown", "a b", ""]}
    for field in ("idempotency_key", "expected_etag"):
        with pytest.raises(InputError):
            spec.input_model.model_validate({**args, field: "do-not-replay"})
    assert spec.input_model.model_validate(args).argv == args["argv"]
    assert spec.response_policy.allowed_modes == ("raw",)


def _native_fixture(monkeypatch, tmp_path, session_ref, *, stdout: bytes, stderr: bytes):
    from stackos.browser.runtime import BrowserRuntime, NativeCliContext, NativeSessionState

    executable = tmp_path / "upstream-cli"
    receipt = tmp_path / "invocations.jsonl"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import base64,json,os,sys\n"
        f"with open({str(receipt)!r}, 'a') as f:\n"
        " f.write(json.dumps({'argv':sys.argv[1:],"
        "'stdin':base64.b64encode(sys.stdin.buffer.read()).decode(),"
        "'cwd':os.getcwd(),'no_autostart':os.environ.get('BROWSE_NO_AUTOSTART')})+'\\n')\n"
        f"sys.stdout.buffer.write({stdout!r})\n"
        f"sys.stderr.buffer.write({stderr!r})\n"
        "sys.exit(17)\n"
    )
    executable.chmod(0o700)
    native = NativeCliContext(
        executable=str(executable), cwd=str(tmp_path), env={"BROWSE_NO_AUTOSTART": "1"}
    )
    state = NativeSessionState(
        session_ref=session_ref,
        profile_ref=session_ref.replace("browser-session", "browser-profile").rsplit(":", 1)[0],
        status="running",
        owned=True,
        healthy=True,
        pid=123,
        repair=None,
        native_cli=native,
    )
    runtime = BrowserRuntime()

    async def inspect_session(**kwargs):
        assert kwargs["session_ref"] == session_ref
        return state

    monkeypatch.setattr(runtime, "inspect_session", inspect_session)
    monkeypatch.setattr(browser_ops, "get_browser_runtime", lambda: runtime)
    return runtime, state, receipt


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stdout", "stderr", "encoding"),
    [
        ("こんにちは\r\n\x1b[31mraw\x00\n\n".encode(), b"cookie=synthetic-secret\r\n", "utf-8"),
        (b"\xff\x00\r\n", b"valid stderr\n", "base64"),
        (b"valid stdout\n", b"\xfe\x00", "base64"),
    ],
)
async def test_native_relay_preserves_argv_stdin_streams_and_nonzero_exit(
    browser_operation_context, monkeypatch, tmp_path, stdout, stderr, encoding
) -> None:
    project_id, session_ref, ctx, session = browser_operation_context
    _runtime, _state, receipt = _native_fixture(
        monkeypatch, tmp_path, session_ref, stdout=stdout, stderr=stderr
    )
    argv = ["unknown-command", "a b", "'quoted'", '"quoted"', "$(untouched)", ""]
    stdin = '[ ["js", "Unicode 日本語"] ]\n\x00\r\n'
    args = {"project_id": project_id, "session_ref": session_ref, "argv": argv, "stdin": stdin}
    dispatched = await OperationDispatcher(build_operation_registry()).dispatch(
        "browser.cli.run",
        args,
        session=session,
        surface="mcp",
        settings=ctx.extras["settings"],
    )
    data = dispatched.payload["data"]
    assert set(data) == {"stdout", "stderr", "exit_code", "encoding"}
    assert data["exit_code"] == 17
    assert data["encoding"] == encoding
    decode = (lambda value: value.encode("utf-8")) if encoding == "utf-8" else base64.b64decode
    assert decode(data["stdout"]) == stdout
    assert decode(data["stderr"]) == stderr
    calls = [json.loads(line) for line in receipt.read_text().splitlines()]
    assert calls == [
        {
            "argv": argv,
            "stdin": base64.b64encode(stdin.encode()).decode(),
            "cwd": str(tmp_path),
            "no_autostart": "1",
        }
    ]
    assert not session.exec(select(BrowserActionReceipt)).all()
    assert not session.exec(select(Artifact)).all()
    assert not session.exec(select(IdempotencyKey)).all()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["idempotency_key", "expected_etag", "response_mode"])
async def test_relay_invalid_transport_controls_never_invoke_native(
    browser_operation_context, monkeypatch, tmp_path, field
) -> None:
    project_id, session_ref, ctx, session = browser_operation_context
    _runtime, _state, receipt = _native_fixture(
        monkeypatch, tmp_path, session_ref, stdout=b"", stderr=b""
    )
    args = {
        "project_id": project_id,
        "session_ref": session_ref,
        "argv": ["status"],
        field: "compact" if field == "response_mode" else "replay",
    }
    with pytest.raises(ValidationError):
        await OperationDispatcher(build_operation_registry()).dispatch(
            "browser.cli.run", args, session=session, surface="mcp", settings=ctx.extras["settings"]
        )
    assert not receipt.exists()


@pytest.mark.asyncio
async def test_relay_rejects_foreign_session_before_native_execution(
    browser_operation_context, monkeypatch, tmp_path
) -> None:
    spec = build_operation_registry().get("browser.cli.run")
    _project_id, session_ref, ctx, session = browser_operation_context
    _runtime, _state, receipt = _native_fixture(
        monkeypatch, tmp_path, session_ref, stdout=b"", stderr=b""
    )
    other = Project(
        slug="other", name="Other", domain="other.example.test", locale="en-US", is_active=True
    )
    session.add(other)
    session.commit()
    with pytest.raises(NotFoundError):
        await spec.handler(
            spec.input_model(project_id=other.id, session_ref=session_ref, argv=["status"]),
            ctx,
            None,
        )
    assert not receipt.exists()


@pytest.mark.asyncio
async def test_discovery_reconciles_owned_state_and_limits_handoff(
    browser_operation_context, monkeypatch, tmp_path
) -> None:
    project_id, session_ref, ctx, _session = browser_operation_context
    _native_fixture(monkeypatch, tmp_path, session_ref, stdout=b"", stderr=b"")
    registry = build_operation_registry()
    listing = registry.get("browser.session.list")
    status = registry.get("browser.session.status")
    listed = await listing.handler(listing.input_model(project_id=project_id), ctx, None)
    assert [row.session_ref for row in listed.items] == [session_ref]
    assert listed.items[0].status == "running"
    assert listed.items[0].native_cli is None
    selected = await status.handler(
        status.input_model(project_id=project_id, session_ref=session_ref), ctx, None
    )
    assert selected.status == "running"
    assert selected.native_cli["executable"].endswith("upstream-cli")
