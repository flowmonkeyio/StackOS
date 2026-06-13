"""Daemon-owned Camoufox browser runtime."""

from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from inspect import isawaitable
from pathlib import Path
from typing import Any

from stackos.browser.manifest import BrowserMethodSpec
from stackos.repositories.base import ValidationError

_SAFE_KEY_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
_PROTECTED_LAUNCH_OPTION_KEYS = frozenset(
    {
        "executable_path",
        "persistent_context",
        "user_data_dir",
    }
)


def safe_browser_key(value: str) -> str:
    """Normalize a profile/session key for refs and daemon-owned paths."""
    clean = _SAFE_KEY_RE.sub("-", value.strip()).strip(".-").lower()
    if not clean:
        raise ValidationError("browser key must contain at least one safe character")
    return clean[:160]


def browser_profile_dir(root: Path, *, project_id: int, profile_key: str) -> Path:
    """Return the daemon-private profile directory for a project profile."""
    return root / "browser-profiles" / f"project-{project_id}" / safe_browser_key(profile_key)


def sanitize_launch_options(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate agent launch options without exposing daemon-owned controls."""
    if raw is None:
        return None
    blocked = sorted(set(raw) & _PROTECTED_LAUNCH_OPTION_KEYS)
    if blocked:
        raise ValidationError(
            "browser launch options include daemon-owned controls",
            data={
                "blocked_keys": blocked,
                "repair": (
                    "Remove daemon-owned launch options. StackOS owns the "
                    "browser executable, persistent context mode, and profile directory."
                ),
            },
        )
    return dict(raw)


@dataclass
class RuntimeStatus:
    provider: str
    package_installed: bool
    package_version: str | None
    browser_downloaded: bool
    executable_path: str | None
    live_session_refs: list[str]
    repair: str | None = None

    def to_dict(
        self,
        *,
        project_id: int | None = None,
        reveal_executable_path: bool = False,
    ) -> dict[str, Any]:
        if project_id is None:
            live_session_refs: list[str] = []
        else:
            marker = f":project-{project_id}:"
            live_session_refs = sorted(ref for ref in self.live_session_refs if marker in ref)
        return {
            "provider": self.provider,
            "package_installed": self.package_installed,
            "package_version": self.package_version,
            "browser_downloaded": self.browser_downloaded,
            "browser_path_present": self.executable_path is not None,
            "executable_path": self.executable_path if reveal_executable_path else None,
            "live_session_refs": live_session_refs,
            "repair": self.repair,
        }


@dataclass
class BrowserCallResult:
    method: str
    status: str
    page_ref: str
    url: str | None = None
    title: str | None = None
    value: Any | None = None
    page_refs: list[str] | None = None
    screenshot_path: Path | None = None
    screenshot_mime_type: str | None = None

    def to_public_result(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "method": self.method,
            "status": self.status,
            "page_ref": self.page_ref,
        }
        if self.url is not None:
            out["url"] = self.url
        if self.title is not None:
            out["title"] = self.title
        if self.value is not None:
            out["value"] = self.value
        if self.page_refs is not None:
            out["page_refs"] = self.page_refs
        return out


@dataclass
class LiveBrowserSession:
    """In-memory Camoufox context for a persistent profile."""

    session_ref: str
    profile_ref: str
    profile_dir: Path
    manager: Any
    context: Any
    pages: dict[str, Any]
    active_page_ref: str
    handles: dict[str, Any] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def page(self) -> Any:
        return self.pages[self.active_page_ref]

    @property
    def page_ref(self) -> str:
        return self.active_page_ref

    @property
    def page_refs(self) -> list[str]:
        return sorted(self.pages)


class BrowserRuntime:
    """Process-local browser session manager.

    The database stores durable metadata and receipts. The actual browser
    handles are live Python objects and intentionally remain daemon-private.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, LiveBrowserSession] = {}

    def status(self) -> RuntimeStatus:
        installed = importlib.util.find_spec("camoufox") is not None
        version: str | None = None
        if installed:
            try:
                version = importlib.metadata.version("camoufox")
            except importlib.metadata.PackageNotFoundError:
                version = None
        executable_path: str | None = None
        repair: str | None = None
        if installed:
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "camoufox", "path"],
                    capture_output=True,
                    text=True,
                    timeout=4,
                    check=False,
                )
                candidate = result.stdout.strip()
                if result.returncode == 0 and candidate:
                    executable_path = candidate
                elif result.stderr.strip():
                    repair = result.stderr.strip()
            except Exception as exc:
                repair = f"{type(exc).__name__}: {exc}"
        if not installed:
            repair = "Install StackOS dependencies, then run `python3 -m camoufox fetch`."
        elif executable_path is None:
            repair = repair or "Run `python3 -m camoufox fetch` to download the browser."
        return RuntimeStatus(
            provider="camoufox",
            package_installed=installed,
            package_version=version,
            browser_downloaded=executable_path is not None,
            executable_path=executable_path,
            live_session_refs=sorted(self._sessions),
            repair=repair,
        )

    async def start_session(
        self,
        *,
        session_ref: str,
        profile_ref: str,
        profile_dir: Path,
        launch_options: dict[str, Any] | None,
        headless: bool,
    ) -> LiveBrowserSession:
        if session_ref in self._sessions:
            return self._sessions[session_ref]
        if importlib.util.find_spec("camoufox") is None:
            raise ValidationError(
                "Camoufox package is not installed",
                data={
                    "provider": "camoufox",
                    "repair": "Install StackOS dependencies, then run `python3 -m camoufox fetch`.",
                },
            )
        from camoufox.async_api import AsyncCamoufox

        profile_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(profile_dir, 0o700)
        options = sanitize_launch_options(launch_options) or {}
        options.setdefault("humanize", True)
        options["persistent_context"] = True
        options["user_data_dir"] = str(profile_dir)
        options["headless"] = headless
        manager = AsyncCamoufox(**options)
        context = await manager.__aenter__()
        pages = list(getattr(context, "pages", []) or [])
        page = pages[0] if pages else await context.new_page()
        page_ref = f"{session_ref}:page-1"
        live = LiveBrowserSession(
            session_ref=session_ref,
            profile_ref=profile_ref,
            profile_dir=profile_dir,
            manager=manager,
            context=context,
            pages={page_ref: page},
            active_page_ref=page_ref,
        )
        self._sync_pages(live)
        self._sessions[session_ref] = live
        return live

    async def stop_session(self, *, session_ref: str) -> bool:
        live = self._sessions.get(session_ref)
        if live is None:
            return False
        async with live.lock:
            await live.manager.__aexit__(None, None, None)
        self._sessions.pop(session_ref, None)
        return True

    def get_session(self, *, session_ref: str) -> LiveBrowserSession:
        live = self._sessions.get(session_ref)
        if live is None:
            raise ValidationError(
                "browser session is not live in this daemon process",
                data={
                    "session_ref": session_ref,
                    "repair": "Start the session again with browser.session.start.",
                },
            )
        return live

    async def page_call(
        self,
        *,
        session_ref: str,
        spec: BrowserMethodSpec | None,
        method: str | None = None,
        arguments: dict[str, Any],
        raw_args: list[Any] | None = None,
        raw_kwargs: dict[str, Any] | None = None,
        page_ref: str | None = None,
    ) -> BrowserCallResult:
        live = self.get_session(session_ref=session_ref)
        async with live.lock:
            page, selected_page_ref = self._page_for_ref(live, page_ref)
            method = method or (spec.method if spec is not None else None)
            if not method:
                raise ValidationError("browser page method is required")
            if method == "goto":
                response = await page.goto(
                    arguments["url"],
                    wait_until=arguments.get("wait_until"),
                    timeout=arguments.get("timeout_ms"),
                    referer=arguments.get("referer"),
                )
                status = getattr(response, "status", None)
                return BrowserCallResult(
                    method=method,
                    status="ok",
                    page_ref=selected_page_ref,
                    url=page.url,
                    page_refs=live.page_refs,
                    value={"response_status": status} if status is not None else None,
                )
            if method == "click":
                await page.click(
                    arguments["selector"],
                    button=arguments.get("button", "left"),
                    click_count=arguments.get("click_count", 1),
                    timeout=arguments.get("timeout_ms"),
                )
                return BrowserCallResult(
                    method=method,
                    status="ok",
                    page_ref=selected_page_ref,
                    url=page.url,
                    page_refs=live.page_refs,
                )
            if method == "fill":
                await page.fill(
                    arguments["selector"],
                    arguments["value"],
                    timeout=arguments.get("timeout_ms"),
                )
                return BrowserCallResult(
                    method=method,
                    status="ok",
                    page_ref=selected_page_ref,
                    url=page.url,
                    page_refs=live.page_refs,
                )
            if method == "type":
                await page.type(
                    arguments["selector"],
                    arguments["text"],
                    delay=arguments.get("delay_ms"),
                    timeout=arguments.get("timeout_ms"),
                )
                return BrowserCallResult(
                    method=method,
                    status="ok",
                    page_ref=selected_page_ref,
                    url=page.url,
                    page_refs=live.page_refs,
                )
            if method == "press":
                await page.press(
                    arguments["selector"],
                    arguments["key"],
                    timeout=arguments.get("timeout_ms"),
                )
                return BrowserCallResult(
                    method=method,
                    status="ok",
                    page_ref=selected_page_ref,
                    url=page.url,
                    page_refs=live.page_refs,
                )
            if method == "select_option":
                value = await page.select_option(
                    arguments["selector"],
                    arguments["values"],
                    timeout=arguments.get("timeout_ms"),
                )
                return BrowserCallResult(
                    method=method,
                    status="ok",
                    page_ref=selected_page_ref,
                    url=page.url,
                    page_refs=live.page_refs,
                    value=value,
                )
            if method == "wait_for_selector":
                await page.wait_for_selector(
                    arguments["selector"],
                    state=arguments.get("state"),
                    timeout=arguments.get("timeout_ms"),
                )
                return BrowserCallResult(
                    method=method,
                    status="ok",
                    page_ref=selected_page_ref,
                    url=page.url,
                    page_refs=live.page_refs,
                )
            if method == "wait_for_load_state":
                await page.wait_for_load_state(
                    state=arguments.get("state"),
                    timeout=arguments.get("timeout_ms"),
                )
                return BrowserCallResult(
                    method=method,
                    status="ok",
                    page_ref=selected_page_ref,
                    url=page.url,
                    page_refs=live.page_refs,
                )
            if method == "wait_for_timeout":
                await page.wait_for_timeout(arguments["timeout_ms"])
                return BrowserCallResult(
                    method=method,
                    status="ok",
                    page_ref=selected_page_ref,
                    url=page.url,
                    page_refs=live.page_refs,
                )
            if method == "title":
                return BrowserCallResult(
                    method=method,
                    status="ok",
                    page_ref=selected_page_ref,
                    url=page.url,
                    page_refs=live.page_refs,
                    title=await page.title(),
                )
            if method == "url":
                return BrowserCallResult(
                    method=method,
                    status="ok",
                    page_ref=selected_page_ref,
                    url=page.url,
                    page_refs=live.page_refs,
                    value=page.url,
                )
            if method == "text_content":
                value = await page.text_content(
                    arguments["selector"],
                    timeout=arguments.get("timeout_ms"),
                )
                return BrowserCallResult(
                    method=method,
                    status="ok",
                    page_ref=selected_page_ref,
                    url=page.url,
                    page_refs=live.page_refs,
                    value=value,
                )
            if method == "inner_text":
                value = await page.inner_text(
                    arguments["selector"],
                    timeout=arguments.get("timeout_ms"),
                )
                return BrowserCallResult(
                    method=method,
                    status="ok",
                    page_ref=selected_page_ref,
                    url=page.url,
                    page_refs=live.page_refs,
                    value=value,
                )
            if method == "locator_count":
                value = await page.locator(arguments["selector"]).count()
                return BrowserCallResult(
                    method=method,
                    status="ok",
                    page_ref=selected_page_ref,
                    url=page.url,
                    page_refs=live.page_refs,
                    value=value,
                )
            if method == "evaluate":
                if "arg" in arguments:
                    value = await page.evaluate(arguments["script"], arguments.get("arg"))
                else:
                    value = await page.evaluate(arguments["script"])
                return BrowserCallResult(
                    method=method,
                    status="ok",
                    page_ref=selected_page_ref,
                    url=page.url,
                    page_refs=live.page_refs,
                    value=value,
                )
            if method == "add_init_script":
                kwargs: dict[str, Any] = {}
                if arguments.get("script") is not None:
                    kwargs["script"] = arguments["script"]
                if arguments.get("path") is not None:
                    kwargs["path"] = arguments["path"]
                await page.add_init_script(**kwargs)
                return BrowserCallResult(
                    method=method,
                    status="ok",
                    page_ref=selected_page_ref,
                    url=page.url,
                    page_refs=live.page_refs,
                )
            if method.startswith("_"):
                raise ValidationError("private browser page methods are not callable")
            target = getattr(page, method, None)
            if not callable(target):
                raise ValidationError(
                    "browser page method is not callable",
                    data={"method": method},
                )
            args = list(raw_args or arguments.pop("args", []) or [])
            kwargs = dict(raw_kwargs or arguments.pop("kwargs", {}) or {})
            if arguments:
                kwargs.update(arguments)
            value = target(*args, **kwargs)
            if isawaitable(value):
                value = await value
            self._sync_pages(live)
            result_page_ref = self._page_ref_for(live, value) or selected_page_ref
            return BrowserCallResult(
                method=method,
                status="ok",
                page_ref=result_page_ref,
                url=page.url,
                page_refs=live.page_refs,
                value=_json_safe(value, live=live),
            )
        raise ValidationError("unsupported browser page method", data={"method": method})

    async def run_script(
        self,
        *,
        session_ref: str,
        script: str,
        arg: Any | None = None,
        page_ref: str | None = None,
    ) -> BrowserCallResult:
        arguments: dict[str, Any] = {"script": script}
        if arg is not None:
            arguments["arg"] = arg
        from stackos.browser.manifest import get_method_spec

        spec = get_method_spec("evaluate")
        if spec is None:
            raise ValidationError("browser evaluate method is not registered")
        return await self.page_call(
            session_ref=session_ref,
            spec=spec,
            arguments=arguments,
            page_ref=page_ref,
        )

    async def inject_script(
        self,
        *,
        session_ref: str,
        script: str,
        page_ref: str | None = None,
    ) -> BrowserCallResult:
        from stackos.browser.manifest import get_method_spec

        spec = get_method_spec("add_init_script")
        if spec is None:
            raise ValidationError("browser add_init_script method is not registered")
        return await self.page_call(
            session_ref=session_ref,
            spec=spec,
            arguments={"script": script},
            page_ref=page_ref,
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
        live = self.get_session(session_ref=session_ref)
        async with live.lock:
            if method.startswith("_"):
                raise ValidationError("private browser context methods are not callable")
            target = getattr(live.context, method, None)
            if not callable(target):
                raise ValidationError(
                    "browser context method is not callable",
                    data={"method": method},
                )
            args = list(raw_args or arguments.pop("args", []) or [])
            kwargs = dict(raw_kwargs or arguments.pop("kwargs", {}) or {})
            if arguments:
                kwargs.update(arguments)
            value = target(*args, **kwargs)
            if isawaitable(value):
                value = await value
            self._sync_pages(live)
            result_page_ref = self._page_ref_for(live, value) or live.page_ref
            if self._page_ref_for(live, value) is not None:
                live.active_page_ref = result_page_ref
            return BrowserCallResult(
                method=method,
                status="ok",
                page_ref=result_page_ref,
                url=live.page.url,
                page_refs=live.page_refs,
                value=_json_safe(value, live=live),
            )

    async def handle_call(
        self,
        *,
        session_ref: str,
        handle_ref: str,
        method: str,
        arguments: dict[str, Any],
        raw_args: list[Any] | None = None,
        raw_kwargs: dict[str, Any] | None = None,
    ) -> BrowserCallResult:
        live = self.get_session(session_ref=session_ref)
        async with live.lock:
            handle = live.handles.get(handle_ref)
            if handle is None:
                raise ValidationError(
                    "browser handle ref is not live in this session",
                    data={
                        "handle_ref": handle_ref,
                        "live_handle_refs": sorted(live.handles),
                        "repair": (
                            "Create a fresh handle with browser.page.call or browser.context.call."
                        ),
                    },
                )
            if method.startswith("_"):
                raise ValidationError("private browser handle methods are not callable")
            target = getattr(handle, method, None)
            args = list(raw_args or arguments.pop("args", []) or [])
            kwargs = dict(raw_kwargs or arguments.pop("kwargs", {}) or {})
            if arguments:
                kwargs.update(arguments)
            if callable(target):
                value = target(*args, **kwargs)
            elif target is not None and not args and not kwargs:
                value = target
            else:
                raise ValidationError(
                    "browser handle method is not callable",
                    data={"handle_ref": handle_ref, "method": method},
                )
            if isawaitable(value):
                value = await value
            self._sync_pages(live)
            result_page_ref = self._page_ref_for(live, value) or live.page_ref
            return BrowserCallResult(
                method=method,
                status="ok",
                page_ref=result_page_ref,
                url=live.page.url,
                page_refs=live.page_refs,
                value=_json_safe(value, live=live),
            )

    async def snapshot(
        self,
        *,
        session_ref: str,
        page_ref: str | None = None,
    ) -> BrowserCallResult:
        live = self.get_session(session_ref=session_ref)
        async with live.lock:
            page, selected_page_ref = self._page_for_ref(live, page_ref)
            title = await page.title()
            try:
                body_text = await page.locator("body").inner_text(timeout=1000)
            except Exception:
                body_text = ""
            return BrowserCallResult(
                method="snapshot",
                status="ok",
                page_ref=selected_page_ref,
                url=page.url,
                title=title,
                page_refs=live.page_refs,
                value={"body_text": body_text[:8000]},
            )

    async def screenshot(
        self,
        *,
        session_ref: str,
        path: Path,
        full_page: bool,
        page_ref: str | None = None,
    ) -> BrowserCallResult:
        live = self.get_session(session_ref=session_ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        async with live.lock:
            page, selected_page_ref = self._page_for_ref(live, page_ref)
            await page.screenshot(path=str(path), full_page=full_page)
            return BrowserCallResult(
                method="screenshot",
                status="ok",
                page_ref=selected_page_ref,
                url=page.url,
                title=await page.title(),
                page_refs=live.page_refs,
                screenshot_path=path,
                screenshot_mime_type="image/png",
            )

    def _sync_pages(self, live: LiveBrowserSession) -> None:
        context_pages = list(getattr(live.context, "pages", []) or [])
        known_ids = {id(page): ref for ref, page in live.pages.items()}
        for page in context_pages:
            if id(page) in known_ids:
                continue
            page_ref = f"{live.session_ref}:page-{len(live.pages) + 1}"
            live.pages[page_ref] = page
        if live.active_page_ref not in live.pages and live.pages:
            live.active_page_ref = sorted(live.pages)[0]

    def _page_ref_for(self, live: LiveBrowserSession, page: Any) -> str | None:
        for ref, candidate in live.pages.items():
            if candidate is page:
                return ref
        return None

    def _handle_ref_for(self, live: LiveBrowserSession, value: Any) -> str | None:
        for ref, candidate in live.handles.items():
            if candidate is value:
                return ref
        return None

    def _store_handle(self, live: LiveBrowserSession, value: Any) -> str:
        existing = self._handle_ref_for(live, value)
        if existing is not None:
            return existing
        handle_ref = f"{live.session_ref}:handle-{len(live.handles) + 1}"
        live.handles[handle_ref] = value
        return handle_ref

    def _page_for_ref(
        self,
        live: LiveBrowserSession,
        page_ref: str | None,
    ) -> tuple[Any, str]:
        self._sync_pages(live)
        selected_ref = page_ref or live.active_page_ref
        page = live.pages.get(selected_ref)
        if page is None:
            raise ValidationError(
                "browser page ref is not live in this session",
                data={
                    "page_ref": selected_ref,
                    "live_page_refs": live.page_refs,
                    "repair": (
                        "Call browser.context.call(method='pages') to refresh known page refs."
                    ),
                },
            )
        live.active_page_ref = selected_ref
        return page, selected_ref


_RUNTIME = BrowserRuntime()


def _json_safe(value: Any, *, live: LiveBrowserSession | None = None) -> Any:
    """Return a conservative JSON-compatible representation for raw calls."""
    if live is not None:
        page_ref = None
        for ref, page in live.pages.items():
            if page is value:
                page_ref = ref
                break
        if page_ref is not None:
            return {
                "page_ref": page_ref,
                "url": getattr(value, "url", None),
                "title": _safe_sync_attr(value, "title"),
            }
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list):
        return [_json_safe(item, live=live) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item, live=live) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item, live=live) for key, item in value.items()}
    for attr in ("url", "title", "status"):
        candidate = getattr(value, attr, None)
        if isinstance(candidate, str | int | float | bool):
            handle_ref = (
                get_browser_runtime()._store_handle(live, value) if live is not None else None
            )
            out: dict[str, Any] = {attr: candidate, "repr": repr(value)[:120]}
            if handle_ref is not None:
                out["handle_ref"] = handle_ref
                out["type"] = type(value).__name__
            return out
    if live is not None:
        handle_ref = get_browser_runtime()._store_handle(live, value)
        return {
            "handle_ref": handle_ref,
            "type": type(value).__name__,
            "repr": repr(value)[:120],
        }
    return repr(value)


def _safe_sync_attr(value: Any, name: str) -> Any | None:
    candidate = getattr(value, name, None)
    if callable(candidate) or isawaitable(candidate):
        return None
    if isinstance(candidate, str | int | float | bool):
        return candidate
    return None


def get_browser_runtime() -> BrowserRuntime:
    """Return the process-local browser runtime singleton."""
    return _RUNTIME


__all__ = [
    "BrowserCallResult",
    "BrowserRuntime",
    "RuntimeStatus",
    "browser_profile_dir",
    "get_browser_runtime",
    "safe_browser_key",
    "sanitize_launch_options",
]
