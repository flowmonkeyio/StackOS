from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import Session, SQLModel, select

import stackos.operations.browser as browser_ops
from stackos.browser.manifest import browser_method_manifest, get_method_spec
from stackos.browser.runtime import (
    BrowserCallResult,
    BrowserRuntime,
    LiveBrowserSession,
    RuntimeStatus,
    get_browser_runtime,
    sanitize_launch_options,
)
from stackos.db.connection import make_memory_engine
from stackos.db.models import Artifact, BrowserActionReceipt, Project
from stackos.mcp.context import MCPContext
from stackos.mcp.server import ToolRegistry
from stackos.mcp.tools import register_all
from stackos.operations.browser import (
    BrowserPageCallInput,
    BrowserPageSnapshotInput,
    BrowserProfileCreateInput,
    BrowserScreenshotInput,
    BrowserScriptRunInput,
    BrowserSessionRefInput,
    BrowserSessionStartInput,
)
from stackos.operations.registry import build_operation_registry
from stackos.repositories.base import RepositoryError, ValidationError
from stackos.repositories.browser import BrowserRepository


class FakeBrowserRuntime:
    async def page_call(
        self,
        *,
        session_ref: str,
        spec: Any,
        method: str | None = None,
        arguments: dict[str, Any],
        raw_args: list[Any] | None = None,
        raw_kwargs: dict[str, Any] | None = None,
        page_ref: str | None = None,
    ) -> BrowserCallResult:
        _ = spec, raw_args, raw_kwargs
        call_method = method or "dynamic"
        if call_method == "explode":
            raise RuntimeError("boom secret-token")
        return BrowserCallResult(
            method=call_method,
            status="ok",
            page_ref=page_ref or f"{session_ref}:page-1",
            url="https://example.com/page?token=secret#frag",
            title="Visible title secret-token",
            value={"secret": "do-not-store", "count": 1, "arguments": arguments},
            page_refs=[f"{session_ref}:page-1", f"{session_ref}:page-2"],
        )

    async def context_call(
        self,
        *,
        session_ref: str,
        method: str,
        arguments: dict[str, Any],
        raw_args: list[Any] | None = None,
        raw_kwargs: dict[str, Any] | None = None,
    ) -> BrowserCallResult:
        _ = arguments, raw_args, raw_kwargs
        if method == "explode":
            raise RuntimeError("context secret-token")
        return BrowserCallResult(
            method=method,
            status="ok",
            page_ref=f"{session_ref}:page-1",
            url="https://example.com/context?auth=secret",
            value={"cookies": [{"name": "session", "value": "cookie-secret"}]},
            page_refs=[f"{session_ref}:page-1", f"{session_ref}:page-2"],
        )

    async def screenshot(
        self,
        *,
        session_ref: str,
        path: Path,
        full_page: bool,
        page_ref: str | None = None,
    ) -> BrowserCallResult:
        _ = full_page
        if "fail" in path.name:
            raise RuntimeError("screenshot /private/profile secret-token")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        return BrowserCallResult(
            method="screenshot",
            status="ok",
            page_ref=page_ref or f"{session_ref}:page-1",
            url="https://example.com/shot?token=secret#frag",
            title="Screenshot page secret-token",
            page_refs=[f"{session_ref}:page-1", f"{session_ref}:page-2"],
            screenshot_path=path,
            screenshot_mime_type="image/png",
        )

    async def snapshot(
        self,
        *,
        session_ref: str,
        page_ref: str | None = None,
    ) -> BrowserCallResult:
        if page_ref and page_ref.endswith(":explode"):
            raise RuntimeError("snapshot /private/profile secret-token")
        return BrowserCallResult(
            method="snapshot",
            status="ok",
            page_ref=page_ref or f"{session_ref}:page-1",
            url="https://example.com/snapshot?token=secret#frag",
            title="Snapshot title secret-token",
            value={"text": "raw snapshot secret"},
            page_refs=[f"{session_ref}:page-1", f"{session_ref}:page-2"],
        )

    async def start_session(
        self,
        *,
        session_ref: str,
        profile_ref: str,
        profile_dir: Path,
        launch_options: dict[str, Any] | None,
        headless: bool,
    ) -> Any:
        _ = profile_ref, profile_dir, headless
        if launch_options and launch_options.get("fail"):
            raise RuntimeError("start /private/profile secret-token")
        return SimpleNamespace(
            page_ref=f"{session_ref}:page-1",
            page_refs=[f"{session_ref}:page-1"],
            page=SimpleNamespace(url="about:blank"),
        )

    async def stop_session(self, *, session_ref: str) -> bool:
        return not session_ref.endswith(":missing-live")


class FakeDynamicPage:
    def __init__(self, url: str = "about:blank") -> None:
        self.url = url

    async def public_method(self, value: str, *, suffix: str) -> dict[str, str]:
        return {"value": value, "suffix": suffix}

    async def title(self) -> str:
        return "Fake Page"


class FakeDynamicContext:
    def __init__(self) -> None:
        self.pages = [FakeDynamicPage("about:one")]

    async def new_page(self) -> FakeDynamicPage:
        page = FakeDynamicPage("about:two")
        self.pages.append(page)
        return page


class FakeManager:
    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.fixture
def browser_operation_context(tmp_path: Path):
    engine = make_memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        project = Project(
            slug="browser-unit",
            name="Browser Unit",
            domain="example.com",
            locale="en-US",
            is_active=True,
        )
        session.add(project)
        session.commit()
        session.refresh(project)
        assert project.id is not None

        repo = BrowserRepository(session)
        profile_env = repo.create_profile(
            project_id=project.id,
            profile_key="default",
            name="Default",
            allowed_origins_json=None,
            launch_options_json=None,
            metadata_json=None,
        )
        profile = repo.get_profile(
            project_id=project.id,
            profile_ref=profile_env.data.profile_ref,
        )
        session_ref = repo.session_ref(
            project_id=project.id,
            profile_key="default",
            session_key="default",
        )
        repo.create_or_update_session(
            project_id=project.id,
            profile=profile,
            session_ref=session_ref,
            headless=True,
            page_refs=[f"{session_ref}:page-1"],
            current_url="https://example.com/start?token=old#frag",
            metadata_json=None,
        )
        ctx = MCPContext(
            session=session,
            request_id="browser-unit",
            run_id=321,
            project_id=project.id,
            extras={
                "settings": SimpleNamespace(
                    generated_assets_dir=tmp_path / "generated-assets",
                    data_dir=tmp_path / "data",
                )
            },
        )
        yield project.id, session_ref, ctx, session
    engine.dispose()


def _receipt_rows(session: Session) -> list[BrowserActionReceipt]:
    return list(session.exec(select(BrowserActionReceipt)).all())


def test_browser_manifest_is_full_control_not_restrictive() -> None:
    methods = {row["method"]: row for row in browser_method_manifest()}

    assert methods["evaluate"]["exposure"] == "supported"
    assert methods["add_init_script"]["exposure"] == "supported"
    assert "route" not in methods
    assert "context_cookies" not in methods
    assert "context_storage_state" not in methods
    assert all(row["exposure"] == "supported" for row in methods.values())
    assert get_method_spec("totally_dynamic_method") is None


def test_browser_operation_specs_are_raw_side_effects() -> None:
    registry = build_operation_registry()

    for name in (
        "browser.profile.create",
        "browser.session.start",
        "browser.session.stop",
        "browser.page.call",
        "browser.context.call",
        "browser.script.run",
        "browser.script.inject",
        "browser.page.snapshot",
        "browser.page.screenshot",
    ):
        described = registry.get(name).describe_out()
        assert described.mutating is True
        assert described.secret_policy == "raw-browser-output"
        assert described.response_policy.default_mode == "raw"
        assert described.response_policy.allowed_modes == ["raw"]


def test_browser_mcp_tools_use_operation_read_only_flags() -> None:
    registry = ToolRegistry()
    register_all(registry)

    assert registry.get("browser.runtime.status").read_only is True
    assert registry.get("browser.profile.list").read_only is True
    assert registry.get("browser.page.call").read_only is False
    assert registry.get("browser.context.call").read_only is False


def test_browser_runtime_status_is_repair_or_ready() -> None:
    status = get_browser_runtime().status()

    assert status.provider == "camoufox"
    assert isinstance(status.package_installed, bool)
    assert isinstance(status.browser_downloaded, bool)
    if not status.browser_downloaded:
        assert status.repair


def test_browser_runtime_status_redacts_paths_and_filters_sessions() -> None:
    status = RuntimeStatus(
        provider="camoufox",
        package_installed=True,
        package_version="0.4.11",
        browser_downloaded=True,
        executable_path="/private/stackos/camoufox",
        live_session_refs=[
            "browser-session:project-1:default:default",
            "browser-session:project-2:default:default",
        ],
    )

    public = status.to_dict(project_id=1)

    assert public["browser_path_present"] is True
    assert public["executable_path"] is None
    assert public["live_session_refs"] == ["browser-session:project-1:default:default"]
    assert status.to_dict()["live_session_refs"] == []


def test_sanitize_launch_options_rejects_daemon_owned_controls() -> None:
    with pytest.raises(ValidationError) as exc:
        sanitize_launch_options({"user_data_dir": "/tmp/profile", "humanize": True})

    assert exc.value.data["blocked_keys"] == ["user_data_dir"]
    assert sanitize_launch_options({"humanize": False}) == {"humanize": False}


def test_runtime_dynamic_page_and_context_methods_support_page_refs() -> None:
    runtime = BrowserRuntime()
    session_ref = "browser-session:project-1:default:dynamic"
    context = FakeDynamicContext()
    runtime._sessions[session_ref] = LiveBrowserSession(
        session_ref=session_ref,
        profile_ref="browser-profile:project-1:default",
        profile_dir=Path("/tmp/browser-profile"),
        manager=FakeManager(),
        context=context,
        pages={f"{session_ref}:page-1": context.pages[0]},
        active_page_ref=f"{session_ref}:page-1",
    )

    opened = asyncio.run(
        runtime.context_call(
            session_ref=session_ref,
            method="new_page",
            arguments={},
        )
    )

    assert opened.page_ref == f"{session_ref}:page-2"
    assert opened.value == {
        "page_ref": f"{session_ref}:page-2",
        "url": "about:two",
        "title": None,
    }
    assert opened.page_refs == [f"{session_ref}:page-1", f"{session_ref}:page-2"]

    called = asyncio.run(
        runtime.page_call(
            session_ref=session_ref,
            spec=None,
            method="public_method",
            arguments={},
            raw_args=["hello"],
            raw_kwargs={"suffix": "world"},
            page_ref=f"{session_ref}:page-2",
        )
    )

    assert called.page_ref == f"{session_ref}:page-2"
    assert called.value == {"value": "hello", "suffix": "world"}

    with pytest.raises(ValidationError):
        asyncio.run(
            runtime.page_call(
                session_ref=session_ref,
                spec=None,
                method="_private",
                arguments={},
            )
        )

    with pytest.raises(ValidationError):
        asyncio.run(
            runtime.page_call(
                session_ref=session_ref,
                spec=None,
                method="public_method",
                arguments={},
                page_ref=f"{session_ref}:missing",
            )
        )


def test_browser_profile_create_rejects_daemon_owned_launch_options(
    browser_operation_context: tuple[int, str, MCPContext, Session],
) -> None:
    project_id, _session_ref, ctx, _session = browser_operation_context

    with pytest.raises(ValidationError):
        asyncio.run(
            browser_ops._browser_profile_create(
                BrowserProfileCreateInput(
                    project_id=project_id,
                    profile_key="unsafe",
                    launch_options_json={"executable_path": "/tmp/browser"},
                ),
                ctx,
                _emit=None,
            )
        )


def test_browser_session_start_rejects_daemon_owned_launch_options(
    browser_operation_context: tuple[int, str, MCPContext, Session],
) -> None:
    project_id, _session_ref, ctx, _session = browser_operation_context

    with pytest.raises(ValidationError):
        asyncio.run(
            browser_ops._browser_session_start(
                BrowserSessionStartInput(
                    project_id=project_id,
                    profile_key="unsafe",
                    session_key="unsafe",
                    launch_options_json={"user_data_dir": "/tmp/browser-profile"},
                ),
                ctx,
                _emit=None,
            )
        )


def test_browser_session_start_uses_profile_launch_options(
    browser_operation_context: tuple[int, str, MCPContext, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, _session_ref, ctx, _session = browser_operation_context
    captured: dict[str, Any] = {}

    class CaptureStartRuntime(FakeBrowserRuntime):
        async def start_session(self, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return await super().start_session(**kwargs)

    profile = asyncio.run(
        browser_ops._browser_profile_create(
            BrowserProfileCreateInput(
                project_id=project_id,
                profile_key="profile-launch",
                launch_options_json={"locale": "en-US", "humanize": False},
            ),
            ctx,
            _emit=None,
        )
    )
    monkeypatch.setattr(browser_ops, "get_browser_runtime", lambda: CaptureStartRuntime())

    asyncio.run(
        browser_ops._browser_session_start(
            BrowserSessionStartInput(
                project_id=project_id,
                profile_ref=profile.data.profile_ref,
                session_key="profile-launch",
                launch_options_json={"humanize": True},
                headless=True,
            ),
            ctx,
            _emit=None,
        )
    )

    assert captured["launch_options"] == {"locale": "en-US", "humanize": True}


def test_browser_script_run_only_passes_arg_when_supplied(monkeypatch: Any) -> None:
    captured: list[dict[str, Any]] = []

    async def fake_page_call(inp: Any, ctx: Any, emit: Any) -> str:
        _ = ctx, emit
        captured.append(inp.arguments)
        return "ok"

    monkeypatch.setattr(browser_ops, "_browser_page_call", fake_page_call)

    asyncio.run(
        browser_ops._browser_script_run(
            BrowserScriptRunInput(
                project_id=1,
                session_ref="browser-session:project-1:default:default",
                script="() => document.title",
            ),
            ctx=None,
            _emit=None,
        )
    )
    asyncio.run(
        browser_ops._browser_script_run(
            BrowserScriptRunInput(
                project_id=1,
                session_ref="browser-session:project-1:default:default",
                script="arg => arg.ok",
                arg={"ok": True},
            ),
            ctx=None,
            _emit=None,
        )
    )

    assert captured == [
        {"script": "() => document.title"},
        {"script": "arg => arg.ok", "arg": {"ok": True}},
    ]


def test_page_call_returns_full_result_but_receipt_is_redacted(
    browser_operation_context: tuple[int, str, MCPContext, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, session_ref, ctx, session = browser_operation_context
    monkeypatch.setattr(browser_ops, "get_browser_runtime", lambda: FakeBrowserRuntime())

    out = asyncio.run(
        browser_ops._browser_page_call(
            BrowserPageCallInput(
                project_id=project_id,
                session_ref=session_ref,
                method="evaluate",
                arguments={
                    "script": "() => 'secret-output'",
                    "arg": {"secret": "do-not-store"},
                },
            ),
            ctx,
            _emit=None,
        )
    )

    assert out.data.result["value"]["secret"] == "do-not-store"
    assert out.data.receipt.target_url == "https://example.com/page?<redacted>#<redacted>"
    assert out.data.receipt.result_json == {
        "method": "evaluate",
        "status": "ok",
        "page_ref": f"{session_ref}:page-1",
        "url": "https://example.com/page?<redacted>#<redacted>",
        "title_summary": {
            "type": "str",
            "length": len("Visible title secret-token"),
            "sha256": hashlib.sha256(b"Visible title secret-token").hexdigest(),
        },
        "value_summary": {"type": "object", "key_count": 3},
        "page_refs": [f"{session_ref}:page-1", f"{session_ref}:page-2"],
    }
    assert out.data.receipt.input_summary_json == {
        "method": "evaluate",
        "script_length": len("() => 'secret-output'"),
        "script_sha256": hashlib.sha256(b"() => 'secret-output'").hexdigest(),
        "arg_summary": {"type": "object", "key_count": 1},
    }
    receipt_dump = str(out.data.receipt.model_dump())
    assert "secret-output" not in receipt_dump
    assert "do-not-store" not in receipt_dump
    assert "secret-token" not in receipt_dump
    assert len(_receipt_rows(session)) == 1


def test_page_snapshot_accepts_page_ref_and_redacts_receipt(
    browser_operation_context: tuple[int, str, MCPContext, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, session_ref, ctx, session = browser_operation_context
    repo = BrowserRepository(session)
    repo.update_session_url(
        project_id=project_id,
        session_ref=session_ref,
        current_url="https://example.com/start",
        page_refs=[f"{session_ref}:page-1", f"{session_ref}:page-2"],
    )
    monkeypatch.setattr(browser_ops, "get_browser_runtime", lambda: FakeBrowserRuntime())

    out = asyncio.run(
        browser_ops._browser_page_snapshot(
            BrowserPageSnapshotInput(
                project_id=project_id,
                session_ref=session_ref,
                page_ref=f"{session_ref}:page-2",
            ),
            ctx,
            _emit=None,
        )
    )

    assert out.data.result["page_ref"] == f"{session_ref}:page-2"
    assert out.data.result["value"] == {"text": "raw snapshot secret"}
    receipt = _receipt_rows(session)[0]
    assert receipt.page_ref == f"{session_ref}:page-2"
    assert receipt.input_summary_json == {
        "session_ref": session_ref,
        "page_ref": f"{session_ref}:page-2",
    }
    assert receipt.result_json["value_summary"] == {"type": "object", "key_count": 1}
    receipt_dump = str(receipt.model_dump())
    assert "raw snapshot secret" not in receipt_dump
    assert "secret-token" not in receipt_dump


def test_failed_page_snapshot_records_failed_receipt_without_raw_error(
    browser_operation_context: tuple[int, str, MCPContext, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, session_ref, ctx, session = browser_operation_context
    repo = BrowserRepository(session)
    repo.update_session_url(
        project_id=project_id,
        session_ref=session_ref,
        current_url="https://example.com/start?token=old#frag",
        page_refs=[f"{session_ref}:page-1", f"{session_ref}:explode"],
    )
    monkeypatch.setattr(browser_ops, "get_browser_runtime", lambda: FakeBrowserRuntime())

    with pytest.raises(RepositoryError) as exc:
        asyncio.run(
            browser_ops._browser_page_snapshot(
                BrowserPageSnapshotInput(
                    project_id=project_id,
                    session_ref=session_ref,
                    page_ref=f"{session_ref}:explode",
                ),
                ctx,
                _emit=None,
            )
        )

    assert exc.value.detail == "browser operation failed"
    assert "/private/profile" not in str(exc.value.to_dict())
    receipt = _receipt_rows(session)[0]
    assert receipt.operation == "browser.page.snapshot"
    assert receipt.status == "failed"
    assert receipt.page_ref == f"{session_ref}:explode"
    assert receipt.target_url == "https://example.com/start?<redacted>#<redacted>"
    assert receipt.input_summary_json == {
        "session_ref": session_ref,
        "page_ref": f"{session_ref}:explode",
    }
    receipt_dump = str(receipt.model_dump())
    assert "/private/profile" not in receipt_dump
    assert "secret-token" not in receipt_dump


def test_failed_page_call_records_failed_receipt_without_raw_error(
    browser_operation_context: tuple[int, str, MCPContext, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, session_ref, ctx, session = browser_operation_context
    monkeypatch.setattr(browser_ops, "get_browser_runtime", lambda: FakeBrowserRuntime())

    with pytest.raises(RepositoryError) as exc:
        asyncio.run(
            browser_ops._browser_page_call(
                BrowserPageCallInput(
                    project_id=project_id,
                    session_ref=session_ref,
                    method="explode",
                    arguments={
                        "url": "https://example.com/submit?token=input-secret",
                        "value": "secret-value",
                    },
                ),
                ctx,
                _emit=None,
            )
        )

    assert exc.value.detail == "browser operation failed"
    assert "secret-token" not in str(exc.value.to_dict())
    receipts = _receipt_rows(session)
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt.status == "failed"
    assert receipt.target_url == "https://example.com/start?<redacted>#<redacted>"
    assert receipt.input_summary_json == {
        "method": "explode",
        "url": "https://example.com/submit?<redacted>",
        "value_length": len("secret-value"),
        "value_sha256": hashlib.sha256(b"secret-value").hexdigest(),
    }
    assert receipt.result_json == {
        "method": "explode",
        "status": "failed",
        "error_type": "RuntimeError",
        "page_ref": f"{session_ref}:page-1",
        "url": "https://example.com/start?<redacted>#<redacted>",
    }
    assert receipt.error is not None
    assert "secret-token" not in receipt.error
    assert "secret-value" not in str(receipt.model_dump())


def test_failed_context_call_records_failed_receipt_without_raw_error(
    browser_operation_context: tuple[int, str, MCPContext, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, session_ref, ctx, session = browser_operation_context
    monkeypatch.setattr(browser_ops, "get_browser_runtime", lambda: FakeBrowserRuntime())

    with pytest.raises(RepositoryError) as exc:
        asyncio.run(
            browser_ops._browser_context_call(
                browser_ops.BrowserContextCallInput(
                    project_id=project_id,
                    session_ref=session_ref,
                    method="explode",
                    arguments={},
                ),
                ctx,
                _emit=None,
            )
        )

    assert "secret-token" not in str(exc.value.to_dict())
    receipt = _receipt_rows(session)[0]
    assert receipt.operation == "browser.context.call"
    assert receipt.status == "failed"
    assert receipt.result_json["error_type"] == "RuntimeError"
    assert receipt.error is not None
    assert "secret-token" not in receipt.error


def test_context_call_receipt_summarizes_result(
    browser_operation_context: tuple[int, str, MCPContext, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, session_ref, ctx, _session = browser_operation_context
    monkeypatch.setattr(browser_ops, "get_browser_runtime", lambda: FakeBrowserRuntime())

    out = asyncio.run(
        browser_ops._browser_context_call(
            browser_ops.BrowserContextCallInput(
                project_id=project_id,
                session_ref=session_ref,
                method="storage_state",
                arguments={},
            ),
            ctx,
            _emit=None,
        )
    )

    assert out.data.result["value"]["cookies"][0]["value"] == "cookie-secret"
    assert out.data.receipt.result_json == {
        "method": "storage_state",
        "status": "ok",
        "page_ref": f"{session_ref}:page-1",
        "url": "https://example.com/context?<redacted>",
        "value_summary": {"type": "object", "key_count": 1},
        "page_refs": [f"{session_ref}:page-1", f"{session_ref}:page-2"],
    }
    assert "cookie-secret" not in str(out.data.receipt.model_dump())


def test_failed_screenshot_records_failed_receipt_without_artifact(
    browser_operation_context: tuple[int, str, MCPContext, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, session_ref, ctx, session = browser_operation_context
    monkeypatch.setattr(browser_ops, "get_browser_runtime", lambda: FakeBrowserRuntime())

    with pytest.raises(RepositoryError) as exc:
        asyncio.run(
            browser_ops._browser_page_screenshot(
                BrowserScreenshotInput(
                    project_id=project_id,
                    session_ref=session_ref,
                    name="fail-shot",
                    full_page=True,
                ),
                ctx,
                _emit=None,
            )
        )

    assert "/private/profile" not in str(exc.value.to_dict())
    receipt = _receipt_rows(session)[0]
    assert receipt.operation == "browser.page.screenshot"
    assert receipt.status == "failed"
    assert receipt.artifact_id is None
    assert "secret-token" not in str(receipt.model_dump())
    assert list(session.exec(select(Artifact)).all()) == []


def test_screenshot_creates_artifact_and_receipt(
    browser_operation_context: tuple[int, str, MCPContext, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, session_ref, ctx, session = browser_operation_context
    monkeypatch.setattr(browser_ops, "get_browser_runtime", lambda: FakeBrowserRuntime())

    out = asyncio.run(
        browser_ops._browser_page_screenshot(
            BrowserScreenshotInput(
                project_id=project_id,
                session_ref=session_ref,
                name="manual-smoke",
                full_page=False,
            ),
            ctx,
            _emit=None,
        )
    )

    artifact = session.get(Artifact, out.data.artifact["id"])
    assert artifact is not None
    assert artifact.kind == "browser-screenshot"
    assert artifact.uri.startswith("/generated-assets/browser/project-")
    assert artifact.metadata_json["url"] == "https://example.com/shot?<redacted>#<redacted>"
    assert out.data.receipt.artifact_id == artifact.id
    assert out.data.receipt.target_url == "https://example.com/shot?<redacted>#<redacted>"
    assert out.data.receipt.result_json["artifact_id"] == artifact.id
    assert out.data.receipt.result_json["title_summary"] == {
        "type": "str",
        "length": len("Screenshot page secret-token"),
        "sha256": hashlib.sha256(b"Screenshot page secret-token").hexdigest(),
    }
    assert "secret-token" not in str(out.data.receipt.model_dump())
    path = Path(ctx.extras["settings"].generated_assets_dir) / artifact.uri.removeprefix(
        "/generated-assets/"
    )
    assert path.read_bytes().startswith(b"\x89PNG")


def test_failed_session_start_records_failed_receipt_without_raw_error(
    browser_operation_context: tuple[int, str, MCPContext, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, _session_ref, ctx, session = browser_operation_context
    monkeypatch.setattr(browser_ops, "get_browser_runtime", lambda: FakeBrowserRuntime())

    with pytest.raises(RepositoryError) as exc:
        asyncio.run(
            browser_ops._browser_session_start(
                BrowserSessionStartInput(
                    project_id=project_id,
                    profile_key="start-failure",
                    session_key="start-failure",
                    launch_options_json={"fail": True},
                ),
                ctx,
                _emit=None,
            )
        )

    assert "/private/profile" not in str(exc.value.to_dict())
    receipt = _receipt_rows(session)[0]
    assert receipt.operation == "browser.session.start"
    assert receipt.status == "failed"
    assert "secret-token" not in str(receipt.model_dump())


def test_failed_session_stop_records_failed_receipt(
    browser_operation_context: tuple[int, str, MCPContext, Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, session_ref, ctx, session = browser_operation_context

    class MissingLiveRuntime:
        async def stop_session(self, *, session_ref: str) -> bool:
            _ = session_ref
            return False

    monkeypatch.setattr(browser_ops, "get_browser_runtime", lambda: MissingLiveRuntime())

    with pytest.raises(RepositoryError):
        asyncio.run(
            browser_ops._browser_session_stop(
                BrowserSessionRefInput(project_id=project_id, session_ref=session_ref),
                ctx,
                _emit=None,
            )
        )

    receipt = _receipt_rows(session)[0]
    assert receipt.operation == "browser.session.stop"
    assert receipt.status == "failed"
    assert receipt.result_json["error_type"] == "ValidationError"
