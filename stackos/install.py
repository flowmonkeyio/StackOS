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
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid
from collections.abc import Iterable
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Literal

from stackos.browser.runtime import (
    CHROMIUM_APP_NAME,
    CHROMIUM_EXECUTABLE_RELATIVE_PATH,
    CHROMIUM_LICENSE_FILENAME,
    PLAYWRIGHT_DRIVER_VERSION,
    PLAYWRIGHT_EXPECTED_BROWSER_VERSION,
    chromium_app_path,
    chromium_license_path,
    managed_chromium_app_path,
    packaged_stackos_root,
    playwright_driver_version,
)

InstallMode = Literal["clone", "pipx"]
"""How the daemon was installed: from a checked-out git repo or via pipx."""

MCP_SERVER_NAME = "stackos"
CHROMIUM_SNAPSHOT_REVISION = "1610473"
CHROMIUM_BROWSER_VERSION = "148.0.7778.0"
CHROMIUM_COMPATIBILITY_NOTE = (
    "Chromium snapshot 1610473 (148.0.7778.0) is the closest snapshot before the "
    "148.0.7778 branch point and was visibly verified with Playwright 1.60.0 "
    "(declared browserVersion 148.0.7778.96)."
)
CHROMIUM_SNAPSHOT_URL = (
    "https://commondatastorage.googleapis.com/chromium-browser-snapshots/"
    f"Mac_Arm/{CHROMIUM_SNAPSHOT_REVISION}/chrome-mac.zip"
)
CHROMIUM_SNAPSHOT_SHA256 = "3961cef2b608396de21aec027ffaadd7e9a65ff025391fba64ae0023ffefc80a"
CHROMIUM_ARCHIVE_APP_RELATIVE_PATH = Path("chrome-mac") / CHROMIUM_APP_NAME


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


def _browser_license_source() -> Path:
    return Path(__file__).resolve().parent / "browser" / CHROMIUM_LICENSE_FILENAME


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_chromium_bundle(app_path: Path, *, timeout_seconds: int) -> tuple[bool, str]:
    executable = app_path / CHROMIUM_EXECUTABLE_RELATIVE_PATH
    info_path = app_path / "Contents" / "Info.plist"
    if not executable.is_file() or not os.access(executable, os.X_OK) or not info_path.is_file():
        return False, "Chromium bundle layout is incomplete."
    try:
        info = plistlib.loads(info_path.read_bytes())
    except (OSError, ValueError):
        return False, "Chromium bundle metadata is invalid."
    if (
        info.get("CFBundleName") != "Chromium"
        or info.get("CFBundleShortVersionString") != CHROMIUM_BROWSER_VERSION
    ):
        return False, "Chromium bundle version does not match StackOS's pinned runtime."
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return False, "StackOS Chromium is available only for macOS arm64."
    try:
        arch = subprocess.run(
            ["lipo", "-archs", str(executable)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        if arch.returncode != 0 or "arm64" not in arch.stdout.split():
            return False, "Chromium bundle is not arm64."
        version = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except Exception:
        return False, "Chromium bundle launch verification failed."
    if version.returncode != 0 or CHROMIUM_BROWSER_VERSION not in version.stdout:
        return False, "Chromium bundle launch verification failed."
    return True, "Chromium bundle verified."


def _copy_chromium_license(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_browser_license_source(), destination)


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _install_chromium_runtime_atomically(
    source_app: Path,
    source_license: Path,
    target_app: Path,
    target_license: Path,
) -> None:
    """Replace the Chromium bundle and its notice as one recoverable transaction."""
    app_backup = target_app.with_name(f".{target_app.name}.previous-{uuid.uuid4().hex}")
    license_backup = target_license.with_name(f".{target_license.name}.previous-{uuid.uuid4().hex}")
    moved_app = False
    moved_license = False
    try:
        if target_app.exists():
            target_app.replace(app_backup)
            moved_app = True
        if target_license.exists():
            target_license.replace(license_backup)
            moved_license = True
        source_app.replace(target_app)
        source_license.replace(target_license)
    except Exception:
        _remove_path(target_app)
        _remove_path(target_license)
        if moved_app and app_backup.exists():
            app_backup.replace(target_app)
        if moved_license and license_backup.exists():
            license_backup.replace(target_license)
        raise
    finally:
        _remove_path(app_backup)
        _remove_path(license_backup)


def _download_chromium_archive(destination: Path, *, timeout_seconds: int) -> None:
    request = urllib.request.Request(
        CHROMIUM_SNAPSHOT_URL,
        headers={"User-Agent": "StackOS Chromium runtime installer"},
    )
    with (
        urllib.request.urlopen(request, timeout=timeout_seconds) as response,
        destination.open("wb") as out,
    ):
        shutil.copyfileobj(response, out)


def _extract_chromium_archive(
    archive: Path,
    destination: Path,
    *,
    timeout_seconds: int,
) -> None:
    """Extract Chromium with macOS tooling so the app bundle stays executable."""
    try:
        result = subprocess.run(
            ["/usr/bin/ditto", "-x", "-k", str(archive), str(destination)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Chromium archive extraction failed: {_safe_process_error(exc)}"
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(
            "Chromium archive extraction failed: "
            f"exit_code={result.returncode}; {_safe_process_output(detail)}"
        )


def verify_chromium_runtime(
    *,
    data_dir: Path | None = None,
    timeout_seconds: int = 10,
) -> tuple[bool, str]:
    """Verify StackOS's canonical Chromium bundle without downloading or changing it."""
    app_path = chromium_app_path(data_dir)
    if not app_path.exists():
        return False, "Chromium bundle is missing."
    valid, reason = _verify_chromium_bundle(app_path, timeout_seconds=timeout_seconds)
    if not valid:
        return False, reason
    if not chromium_license_path(data_dir).is_file():
        return False, "Chromium license notice is missing."
    return True, "Chromium runtime verified."


def ensure_chromium_runtime(
    *,
    data_dir: Path | None = None,
    runtime_root: Path | None = None,
    timeout_seconds: int = 180,
) -> tuple[bool, str]:
    """Ensure one pinned normal Chromium.app exists at StackOS's owned location.

    Playwright remains the driver. It never installs, selects, or falls back to
    a browser executable; this helper is the sole acquisition and repair owner.
    ``runtime_root`` is build-pipeline-only and writes a desktop payload, not a
    runtime-selected browser path.
    """
    if importlib.util.find_spec("playwright") is None:
        return (
            False,
            "Playwright package is not importable; install/sync Python dependencies first.",
        )
    if playwright_driver_version() != PLAYWRIGHT_DRIVER_VERSION:
        return (
            False,
            "Playwright driver is incompatible with StackOS Chromium; "
            f"expected version {PLAYWRIGHT_DRIVER_VERSION} "
            f"(declared browserVersion {PLAYWRIGHT_EXPECTED_BROWSER_VERSION}).",
        )

    if runtime_root is not None:
        app_path = Path(runtime_root) / CHROMIUM_APP_NAME
        license_path = Path(runtime_root) / CHROMIUM_LICENSE_FILENAME
    elif packaged_stackos_root() is not None:
        app_path = chromium_app_path(data_dir)
        license_path = chromium_license_path(data_dir)
        runtime_ok, _reason = verify_chromium_runtime(data_dir=data_dir)
        if runtime_ok:
            return True, "Bundled Chromium runtime present."
        return False, "Bundled Chromium runtime is missing; repair the StackOS app installation."
    else:
        if data_dir is None:
            from stackos.config import get_settings

            data_dir = Path(get_settings().data_dir)
        app_path = managed_chromium_app_path(Path(data_dir))
        license_path = app_path.parent / CHROMIUM_LICENSE_FILENAME

    if runtime_root is None:
        runtime_ok, _reason = verify_chromium_runtime(data_dir=data_dir)
        if runtime_ok:
            return True, "Managed Chromium runtime present."
    elif app_path.exists():
        valid, _reason = _verify_chromium_bundle(app_path, timeout_seconds=10)
        if valid and license_path.is_file():
            return True, "Bundled Chromium runtime present."

    app_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(
            prefix="stackos-chromium-", dir=app_path.parent
        ) as stage_name:
            stage = Path(stage_name)
            archive = stage / "chromium.zip"
            _download_chromium_archive(archive, timeout_seconds=timeout_seconds)
            if _sha256(archive) != CHROMIUM_SNAPSHOT_SHA256:
                return False, "Chromium archive checksum did not match StackOS's pinned runtime."
            extracted = stage / "extracted"
            _extract_chromium_archive(archive, extracted, timeout_seconds=timeout_seconds)
            candidate = extracted / CHROMIUM_ARCHIVE_APP_RELATIVE_PATH
            valid, reason = _verify_chromium_bundle(candidate, timeout_seconds=10)
            if not valid:
                return False, reason
            runtime_stage = stage / "runtime"
            runtime_stage.mkdir()
            candidate = candidate.replace(runtime_stage / CHROMIUM_APP_NAME)
            staged_license = runtime_stage / CHROMIUM_LICENSE_FILENAME
            _copy_chromium_license(staged_license)
            _install_chromium_runtime_atomically(
                candidate,
                staged_license,
                app_path,
                license_path,
            )
    except Exception as exc:
        return False, f"Chromium runtime install failed: {_safe_process_error(exc)}"
    return True, "Managed Chromium runtime installed."


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
    "ensure_chromium_runtime",
    "register_mcp_claude",
    "register_mcp_codex",
    "register_plugin_marketplace",
    "remove_mcp_hosts",
    "remove_plugins",
    "remove_skills",
    "repair_mcp_hosts",
    "verify_chromium_runtime",
]
