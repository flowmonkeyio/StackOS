"""Install pipeline shared by clone-mode (bash scripts) and pipx-mode (`stackos install`).

Clone installs reach into ``${REPO_ROOT}/plugins`` and the canonical
``${REPO_ROOT}/plugins/stackos/skills/stackos`` skill asset directory. Pipx
installs cannot use repo-relative paths, so those assets are bundled at
``stackos/_assets/skills`` and ``stackos/_assets/plugins`` and resolved
through ``importlib.resources``.

The two install paths copy from different *sources* but write to the same
*targets* and share the same idempotency contract: re-running yields the same
end state.

Public surface:

- :func:`detect_mode` — returns ``"clone"`` if the package import points at
  a checked-out repo with a ``plugins/`` sibling, else ``"pipx"``.
- :func:`copy_skills` / :func:`copy_plugins` mirror assets into
  ``~/.codex/...`` or ``~/.claude/...`` with mtime-aware copy and
  ``--delete``-style cleanup of stale files.
- :func:`register_mcp_codex` / :func:`register_mcp_claude` — local agent
  MCP registration helpers. Claude Code registration is owned by
  ``stackos.claude_mcp`` so install, doctor, uninstall, and shell wrappers use
  one contract.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Literal

from stackos.browser.runtime import playwright_chromium_executable_path

InstallMode = Literal["clone", "pipx"]
"""How the daemon was installed: from a checked-out git repo or via pipx."""

MCP_SERVER_NAME = "stackos"


# ---------------------------------------------------------------------------
# Mode detection + asset resolution
# ---------------------------------------------------------------------------


def _package_root() -> Path:
    """Return the on-disk path to the imported `stackos` package."""
    import stackos

    pkg_path = Path(stackos.__file__).resolve().parent
    return pkg_path


def _repo_root_if_clone() -> Path | None:
    """Return the repo root iff the package import points at a clone.

    Heuristic: the parent of the package directory contains the StackOS plugin
    manifest and a ``pyproject.toml`` whose ``name`` is ``stackos``. We do
    NOT rely on the presence of
    ``.git`` because users may install via ``uv pip install -e`` from a
    tarball checkout without ``.git``.
    """
    parent = _package_root().parent
    pyproj = parent / "pyproject.toml"
    plugins = parent / "plugins"
    plugin_manifest = plugins / "stackos" / ".codex-plugin" / "plugin.json"
    if not (pyproj.exists() and plugin_manifest.is_file()):
        return None
    try:
        text = pyproj.read_text(encoding="utf-8")
    except OSError:
        return None
    if 'name = "stackos"' not in text:
        return None
    return parent


def detect_mode() -> InstallMode:
    """Return the install mode based on the on-disk layout."""
    return "clone" if _repo_root_if_clone() is not None else "pipx"


def ensure_playwright_browser(*, timeout_seconds: int = 180) -> tuple[bool, str]:
    """Ensure the packaged Playwright Chromium browser binary is installed.

    The Python dependency is declared in ``pyproject.toml``. This helper owns the
    second Playwright setup step (`python -m playwright install chromium`) so
    clone-mode and package-mode installs converge on the same ready-to-run
    browser state.
    """
    if importlib.util.find_spec("playwright") is None:
        return (
            False,
            "Playwright package is not importable; install/sync Python dependencies first.",
        )
    if playwright_chromium_executable_path(timeout_seconds=10):
        return True, "Playwright Chromium browser present."
    try:
        install = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except Exception as exc:
        return False, f"Playwright Chromium install failed: {_safe_process_error(exc)}"
    if install.returncode != 0:
        detail = (install.stderr or install.stdout).strip()
        return False, (
            f"Playwright Chromium install failed: exit_code={install.returncode}; "
            f"{_safe_process_output(detail)}"
        )
    return True, "Playwright Chromium browser installed."


def _safe_process_error(exc: Exception) -> str:
    message = str(exc)
    return (
        f"error_type={type(exc).__name__}; "
        f"message_sha256={hashlib.sha256(message.encode('utf-8')).hexdigest()}; "
        f"message_length={len(message)}"
    )


def _safe_process_output(output: str) -> str:
    if not output:
        return "output=empty"
    return (
        f"output_sha256={hashlib.sha256(output.encode('utf-8')).hexdigest()}; "
        f"output_length={len(output)}"
    )


def _bundled_assets_root() -> Traversable:
    """Return a `Traversable` rooted at the wheel-bundled `_assets/` tree.

    Raises ``FileNotFoundError`` when the assets are not present, which
    happens during clone-mode development before the first wheel build.
    """
    root = resources.files("stackos").joinpath("_assets")
    if not root.is_dir():
        raise FileNotFoundError(
            "stackos/_assets/ not found in the installed package. "
            "In clone-mode, run `make build-ui` then re-run."
        )
    return root


def _resolve_source(kind: Literal["skills", "plugins"]) -> Path | Traversable:
    """Return the source root for ``kind`` based on detected mode.

    Returns a :class:`Path` in clone mode (so callers can use ``rsync`` /
    ``shutil.copytree`` directly) or a :class:`Traversable` in pipx mode
    (so callers walk the bundled wheel resources).
    """
    repo = _repo_root_if_clone()
    if repo is not None:
        if kind == "skills":
            plugin_skill = repo / "plugins" / "stackos" / "skills" / "stackos"
            if plugin_skill.is_dir():
                return plugin_skill
        return repo / kind
    source = _bundled_assets_root().joinpath(kind)
    if kind == "skills":
        source = source.joinpath("stackos")
    return source


# ---------------------------------------------------------------------------
# Copy primitives
# ---------------------------------------------------------------------------


def _iter_traversable(
    root: Traversable, exclude_dirs: Iterable[str]
) -> Iterable[tuple[str, Traversable]]:
    """Yield ``(rel_posix_path, traversable)`` for every file under ``root``.

    Directories whose name appears in ``exclude_dirs`` are skipped wholesale.
    """
    excluded = frozenset(exclude_dirs)

    def walk(node: Traversable, rel: str) -> Iterable[tuple[str, Traversable]]:
        for child in node.iterdir():
            name = child.name
            if name in {".DS_Store", "__pycache__"}:
                continue
            child_rel = f"{rel}/{name}" if rel else name
            if child.is_dir():
                if name in excluded:
                    continue
                yield from walk(child, child_rel)
            else:
                yield child_rel, child

    yield from walk(root, "")


def _mirror_traversable(
    source: Traversable,
    dest: Path,
    exclude_dirs: Iterable[str],
) -> None:
    """Copy every file under ``source`` (``Traversable``) into ``dest``.

    Pre-existing files NOT present in ``source`` are removed so the result
    matches ``rsync --delete``.
    """
    dest.mkdir(parents=True, exist_ok=True)

    seen: set[Path] = set()
    for rel, node in _iter_traversable(source, exclude_dirs):
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        # `Traversable.read_bytes()` covers both filesystem and
        # zipfile-backed resources.
        target.write_bytes(node.read_bytes())
        seen.add(target.resolve())

    # Sweep stale files / dirs.
    for existing in list(dest.rglob("*")):
        if existing.is_file() and existing.resolve() not in seen:
            existing.unlink()
    # Prune empty dirs left after file sweep — `rmdir` raises if a dir
    # is non-empty, which we accept silently.
    for d in sorted(
        (p for p in dest.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        with contextlib.suppress(OSError):
            d.rmdir()


def _mirror_path(source: Path, dest: Path, exclude_dirs: Iterable[str]) -> None:
    """Copy a filesystem ``source`` tree into ``dest`` with ``--delete`` semantics."""
    dest.mkdir(parents=True, exist_ok=True)
    excluded = frozenset(exclude_dirs)
    seen: set[Path] = set()
    for src in source.rglob("*"):
        if src.is_dir():
            if src.name in excluded:
                # Skip the entire subtree.
                continue
            continue
        # Skip files inside an excluded directory.
        rel = src.relative_to(source)
        if any(part in excluded for part in rel.parts):
            continue
        if src.name in {".DS_Store"} or src.name.endswith(".pyc"):
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        seen.add(target.resolve())

    for existing in list(dest.rglob("*")):
        if existing.is_file() and existing.resolve() not in seen:
            existing.unlink()
    for d in sorted(
        (p for p in dest.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        with contextlib.suppress(OSError):
            d.rmdir()


# ---------------------------------------------------------------------------
# Public copy helpers
# ---------------------------------------------------------------------------


def _runtime_target(home: Path, runtime: Literal["codex", "claude"], kind: str) -> Path:
    """Return the runtime-specific install target under ``home``."""
    return home / f".{runtime}" / kind / "stackos"


def copy_skills(
    runtime: Literal["codex", "claude"],
    home: Path | None = None,
) -> tuple[Path, int]:
    """Mirror skills into the runtime-specific path.

    Returns ``(target_dir, skill_count)`` so callers can echo the same
    summary as the bash scripts.
    """
    home_dir = home if home is not None else Path.home()
    target = _runtime_target(home_dir, runtime, "skills")
    source = _resolve_source("skills")
    if isinstance(source, Path):
        _mirror_path(source, target, exclude_dirs=())
    else:
        _mirror_traversable(source, target, exclude_dirs=())
    count = sum(1 for _ in target.rglob("SKILL.md"))
    return target, count


def remove_skills(
    runtime: Literal["codex", "claude"],
    home: Path | None = None,
) -> Path:
    """Remove the StackOS skill mirror for one runtime."""
    home_dir = home if home is not None else Path.home()
    target = _runtime_target(home_dir, runtime, "skills")
    shutil.rmtree(target, ignore_errors=True)
    return target


def _plugin_mcp_payload() -> dict[str, object]:
    """Return a plugin-local MCP config that does not depend on shell PATH."""
    return {
        "mcpServers": {
            MCP_SERVER_NAME: {
                "command": sys.executable,
                "args": ["-m", "stackos", "mcp-bridge"],
            }
        }
    }


def _write_plugin_mcp_config(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".mcp.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_plugin_mcp_payload(), f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, target)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _refresh_existing_plugin_cache(home_dir: Path, source_plugin: Path) -> None:
    """Refresh Codex's installed plugin cache when it already exists.

    Codex loads enabled plugins from ``~/.codex/plugins/cache``. Updating the
    marketplace source is enough for a future reinstall, but refreshing our own
    existing cache copy keeps clone-mode installs usable immediately after
    ``stackos install``. The cache is a runtime copy of the plugin, so mirror
    the full plugin tree instead of only refreshing generated MCP config.
    """
    cache_root = home_dir / ".codex" / "plugins" / "cache" / "local-stackos" / "stackos"
    if not cache_root.is_dir():
        return
    for version_dir in cache_root.iterdir():
        if (version_dir / ".codex-plugin" / "plugin.json").is_file():
            _mirror_path(source_plugin, version_dir, exclude_dirs=())
            _write_plugin_mcp_config(version_dir / ".mcp.json")


def copy_plugins(home: Path | None = None) -> tuple[Path, int]:
    """Mirror and hydrate plugin packages into the home-local plugin directory."""
    home_dir = home if home is not None else Path.home()
    target = home_dir / ".codex" / "plugins" / "stackos"
    source_root = _resolve_source("plugins")
    if isinstance(source_root, Path):
        _mirror_path(source_root / "stackos", target, exclude_dirs=())
    else:
        _mirror_traversable(source_root.joinpath("stackos"), target, exclude_dirs=())

    _write_plugin_mcp_config(target / ".mcp.json")
    _refresh_existing_plugin_cache(home_dir, target)

    count = sum(1 for p in target.rglob("plugin.json") if p.parent.name == ".codex-plugin")
    return target, count


def remove_plugins(home: Path | None = None) -> tuple[Path, Path]:
    """Remove StackOS plugin source and Codex's cached installed copy."""
    home_dir = home if home is not None else Path.home()
    target = home_dir / ".codex" / "plugins" / "stackos"
    cache_root = home_dir / ".codex" / "plugins" / "cache" / "local-stackos" / "stackos"
    shutil.rmtree(target, ignore_errors=True)
    shutil.rmtree(cache_root, ignore_errors=True)
    return target, cache_root


def register_plugin_marketplace(
    *,
    home: Path | None = None,
    remove: bool = False,
) -> str:
    """Upsert the home-local plugin marketplace entry for StackOS."""
    home_dir = home if home is not None else Path.home()
    target = home_dir / ".agents" / "plugins" / "marketplace.json"
    target.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, object] = {
        "name": "local-stackos",
        "interface": {"displayName": "Local StackOS Plugins"},
        "plugins": [],
    }
    if target.exists():
        text = target.read_text(encoding="utf-8").strip()
        if text:
            loaded = json.loads(text)
            if not isinstance(loaded, dict):
                raise ValueError(f"existing {target} is not a JSON object")
            existing = loaded

    plugins = existing.setdefault("plugins", [])
    if not isinstance(plugins, list):
        raise ValueError(f"`plugins` in {target} must be a list")

    plugins[:] = [p for p in plugins if not (isinstance(p, dict) and p.get("name") == "stackos")]
    if remove:
        msg = f"Unregistered plugin 'stackos' from {target}"
    else:
        plugins.append(
            {
                "name": "stackos",
                "source": {"source": "local", "path": "./.codex/plugins/stackos"},
                "policy": {
                    "installation": "INSTALLED_BY_DEFAULT",
                    "authentication": "ON_USE",
                },
                "category": "Productivity",
            }
        )
        msg = f"Registered plugin 'stackos' in {target}"

    fd, tmp = tempfile.mkstemp(prefix=".marketplace.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, target)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return msg


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


def _read_token(home: Path) -> str:
    """Read the auth token from the canonical state path under ``home``."""
    token_path = home / ".local" / "state" / "stackos" / "auth.token"
    if not token_path.is_file():
        raise FileNotFoundError(
            f"auth token missing at {token_path} — run `stackos init` or `make install` first."
        )
    return token_path.read_text(encoding="utf-8").strip()


def register_mcp_codex(
    *,
    home: Path | None = None,
    port: int = 5180,
    remove: bool = False,
    force: bool = False,
) -> str:
    """Register (or remove) the StackOS MCP server in Codex."""
    from stackos.host_mcp import register_host, remove_host

    home_dir = home if home is not None else Path.home()
    result = (
        remove_host("codex", home=home_dir)
        if remove
        else register_host("codex", home=home_dir, force=force)
    )
    return _host_mcp_message(result)


def _codex_mcp_line_is_bridge(line: str) -> bool:
    """Return true when a Codex MCP list row is the local stdio bridge."""
    from stackos.host_mcp.bridge import (
        command_line_mentions,
        output_row_matches_server,
        resolve_bridge_command,
    )

    normalized = line.strip()
    if not output_row_matches_server(normalized, MCP_SERVER_NAME):
        return False
    lowered = normalized.lower()
    forbidden = (
        "/mcp",
        "--url",
        "--bearer-token-env-var",
        "authorization",
        "bearer",
    )
    if any(token in lowered for token in forbidden):
        return False
    return command_line_mentions(resolve_bridge_command(runtime="codex"), normalized)


def register_mcp_claude(
    *,
    home: Path | None = None,
    port: int = 5180,
    target: Path | None = None,
    remove: bool = False,
) -> str:
    """Register or remove StackOS through Claude Code's MCP CLI."""
    from stackos.host_mcp import register_host, remove_host

    home_dir = home if home is not None else Path.home()
    if target is not None:
        # Retained for call-signature compatibility only. Claude Code no longer
        # uses this legacy target as the product registration source of truth.
        del target
    result = (
        remove_host("claude-code", home=home_dir)
        if remove
        else register_host("claude-code", home=home_dir)
    )
    return _host_mcp_message(result)


def repair_mcp_hosts(*, home: Path | None = None) -> tuple[bool, list[str]]:
    """Repair every known host MCP registration and return status lines."""
    from stackos.host_mcp import repair_all

    aggregate = repair_all(home=home if home is not None else Path.home())
    return aggregate.ok, aggregate.summary_lines()


def remove_mcp_hosts(*, home: Path | None = None) -> tuple[bool, list[str]]:
    """Remove StackOS MCP entries from every known host registration surface."""
    from stackos.host_mcp import remove_all

    aggregate = remove_all(home=home if home is not None else Path.home())
    return aggregate.ok, aggregate.summary_lines()


def _host_mcp_message(result: object) -> str:
    message = getattr(result, "message", "")
    repair = getattr(result, "repair", None)
    ok = getattr(result, "ok", True)
    return f"{message} {repair or ''}".strip() if not ok else str(message)


__all__ = [
    "InstallMode",
    "copy_plugins",
    "copy_skills",
    "detect_mode",
    "register_mcp_claude",
    "register_mcp_codex",
    "register_plugin_marketplace",
    "remove_mcp_hosts",
    "remove_plugins",
    "remove_skills",
    "repair_mcp_hosts",
]
