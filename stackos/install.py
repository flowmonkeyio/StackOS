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
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import uuid
import zipfile
from collections.abc import Iterable
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Literal

from stackos.browser.runtime import (
    GSTACK_RUNTIME_DIRNAME,
    gstack_runtime_root,
    packaged_stackos_root,
)

InstallMode = Literal["clone", "pipx"]
"""How the daemon was installed: from a checked-out git repo or via pipx."""

MCP_SERVER_NAME = "stackos"
GSTACK_VERSION = "1.84.1.0"
GSTACK_REVISION = "71f6048e8ada25180e61438abc1d98cb151fe9a7"
GSTACK_SOURCE_ARCHIVE_URL = f"https://github.com/garrytan/gstack/archive/{GSTACK_REVISION}.tar.gz"
GSTACK_SOURCE_ARCHIVE_SHA256 = "4740bfb9efb35fb407679496d890210bfc7c193bc8331f70cbf7d64bbf75cfb8"
BUN_VERSION = "1.3.8"
BUN_ARCHIVE_URL = (
    f"https://github.com/oven-sh/bun/releases/download/bun-v{BUN_VERSION}/bun-darwin-aarch64.zip"
)
BUN_ARCHIVE_SHA256 = "672a0a9a7b744d085a1d2219ca907e3e26f5579fca9e783a9510a4f98a36212f"
GSTACK_PLAYWRIGHT_VERSION = "1.62.1"
GSTACK_RUNTIME_MANIFEST_SCHEMA = 1
_UPSTREAM_CHROMIUM_EXECUTABLE = (
    Path("chrome-mac-arm64")
    / "Google Chrome for Testing.app"
    / "Contents"
    / "MacOS"
    / "Google Chrome for Testing"
)


class _GstackRuntimeInstallError(RuntimeError):
    """A safe, stage-specific runtime acquisition failure for CLI users."""

    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(reason)
        self.stage = stage
        self.reason = reason


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _download_runtime_archive(
    url: str,
    destination: Path,
    *,
    stage: str,
    timeout_seconds: int,
) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "StackOS gstack runtime installer"},
    )
    try:
        with (
            urllib.request.urlopen(request, timeout=timeout_seconds) as response,
            destination.open("wb") as out,
        ):
            shutil.copyfileobj(response, out)
    except Exception as exc:
        raise _GstackRuntimeInstallError(
            stage,
            "download failed; check network access and run `stackos install` again.",
        ) from exc


def _safe_archive_destination(destination: Path, member_name: str) -> Path:
    if not member_name or Path(member_name).is_absolute():
        raise ValueError("unsafe archive member")
    candidate = destination / member_name
    try:
        candidate.resolve().relative_to(destination.resolve())
    except ValueError as exc:
        raise ValueError("unsafe archive member") from exc
    return candidate


def _safe_archive_symlink_destination(destination: Path, member_name: str, link_name: str) -> Path:
    if not link_name or Path(link_name).is_absolute():
        raise ValueError("unsafe archive member")
    member_path = _safe_archive_destination(destination, member_name)
    link_target = member_path.parent / link_name
    try:
        link_target.resolve().relative_to(destination.resolve())
    except ValueError as exc:
        raise ValueError("unsafe archive member") from exc
    return link_target


def _extract_gstack_source_archive(archive: Path, destination: Path) -> None:
    """Extract source while retaining only symlinks whose archive target is internal."""
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive, "r:gz") as source:
            members = source.getmembers()
            root = destination.resolve()
            archive_paths: set[str] = set()
            for member in members:
                target = _safe_archive_destination(destination, member.name)
                archive_paths.add(target.resolve().relative_to(root).as_posix())
                if not (member.isdir() or member.isreg() or member.issym()):
                    raise ValueError("unsafe archive member")
            for member in members:
                if not member.issym():
                    continue
                link_target = _safe_archive_symlink_destination(
                    destination,
                    member.name,
                    member.linkname,
                )
                if link_target.resolve().relative_to(root).as_posix() not in archive_paths:
                    raise ValueError("unsafe archive member")
            for member in members:
                if not member.isdir():
                    continue
                target = _safe_archive_destination(destination, member.name)
                target.mkdir(parents=True, exist_ok=True)
            for member in members:
                if not member.isreg():
                    continue
                target = _safe_archive_destination(destination, member.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                source_file = source.extractfile(member)
                if source_file is None:
                    raise _GstackRuntimeInstallError(
                        "gstack source extraction",
                        "archive could not be read; run `stackos install` again.",
                    )
                with source_file, target.open("wb") as output:
                    shutil.copyfileobj(source_file, output)
                os.chmod(target, member.mode & 0o777)
            for member in members:
                if not member.issym():
                    continue
                target = _safe_archive_destination(destination, member.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(member.linkname)
    except _GstackRuntimeInstallError:
        raise
    except ValueError as exc:
        raise _GstackRuntimeInstallError(
            "gstack source extraction",
            "archive contains an unsafe member; refresh the pinned runtime and retry.",
        ) from exc
    except (OSError, tarfile.TarError) as exc:
        raise _GstackRuntimeInstallError(
            "gstack source extraction",
            "archive could not be extracted; run `stackos install` again.",
        ) from exc


def _extract_bun_archive(archive: Path, destination: Path) -> Path:
    """Extract the official Bun zip and return its executable, rejecting unsafe entries."""
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as source:
            members = source.infolist()
            for member in members:
                _safe_archive_destination(destination, member.filename)
                mode = member.external_attr >> 16
                if mode and (mode & 0o170000) == 0o120000:
                    raise ValueError("unsafe archive member")
            for member in members:
                target = _safe_archive_destination(destination, member.filename)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(member) as source_file, target.open("wb") as output:
                    shutil.copyfileobj(source_file, output)
                mode = member.external_attr >> 16
                if mode:
                    os.chmod(target, mode & 0o777)
    except ValueError as exc:
        raise _GstackRuntimeInstallError(
            "Bun archive extraction",
            "archive contains an unsafe member; refresh the pinned runtime and retry.",
        ) from exc
    except (OSError, zipfile.BadZipFile) as exc:
        raise _GstackRuntimeInstallError(
            "Bun archive extraction",
            "archive could not be extracted; run `stackos install` again.",
        ) from exc
    bun = destination / "bun-darwin-aarch64" / "bun"
    if not bun.is_file():
        raise _GstackRuntimeInstallError(
            "Bun archive extraction",
            "archive layout is incomplete; refresh the pinned runtime and retry.",
        )
    return bun


def _gstack_source_root(extracted: Path) -> Path:
    roots = [child for child in extracted.iterdir() if child.is_dir()]
    if len(roots) != 1 or not (roots[0] / "package.json").is_file():
        raise _GstackRuntimeInstallError(
            "gstack source extraction",
            "archive layout is incomplete; refresh the pinned runtime and retry.",
        )
    return roots[0]


def _remove_playwright_build_links(browsers: Path) -> None:
    """Drop Playwright's build-directory registration, which cannot survive relocation."""
    links = browsers / ".links"
    try:
        if links.is_symlink() or links.is_file():
            links.unlink()
        elif links.is_dir():
            shutil.rmtree(links)
    except OSError as exc:
        raise _GstackRuntimeInstallError(
            "gstack browser asset finalization",
            "could not remove build-only browser registration metadata; retry `stackos install`.",
        ) from exc


def _run_gstack_build(
    args: list[str],
    *,
    stage: str,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
) -> None:
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except Exception as exc:
        raise _GstackRuntimeInstallError(
            stage,
            "command could not start; run `stackos install` again.",
        ) from exc
    if result.returncode != 0:
        detail = result.stderr or result.stdout
        raise _GstackRuntimeInstallError(
            stage,
            f"command exited {result.returncode}; {_safe_process_output(detail)}",
        )


def _runtime_manifest() -> dict[str, object]:
    return {
        "schema_version": GSTACK_RUNTIME_MANIFEST_SCHEMA,
        "provider": "gstack",
        "gstack_revision": GSTACK_REVISION,
        "gstack_version": GSTACK_VERSION,
        "gstack_source_archive_sha256": GSTACK_SOURCE_ARCHIVE_SHA256,
        "bun_version": BUN_VERSION,
        "bun_archive_sha256": BUN_ARCHIVE_SHA256,
        "playwright_version": GSTACK_PLAYWRIGHT_VERSION,
    }


def _upstream_chromium_executable(root: Path) -> tuple[Path | None, str | None]:
    """Locate Chromium using gstack's installed Playwright metadata, not a StackOS pin."""
    metadata_path = root / "gstack" / "node_modules" / "playwright-core" / "browsers.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "gstack upstream browser metadata is missing or invalid."
    descriptors = metadata.get("browsers") if isinstance(metadata, dict) else None
    if not isinstance(descriptors, list):
        return None, "gstack upstream browser metadata is missing or invalid."
    chromium = next(
        (
            descriptor
            for descriptor in descriptors
            if isinstance(descriptor, dict)
            and descriptor.get("name") == "chromium"
            and descriptor.get("installByDefault") is True
        ),
        None,
    )
    revision = chromium.get("revision") if isinstance(chromium, dict) else None
    if not isinstance(revision, str) or not revision.isdecimal():
        return None, "gstack upstream Chromium metadata is missing or invalid."
    browser_dir = root / "browsers" / f"chromium-{revision}"
    executable = browser_dir / _UPSTREAM_CHROMIUM_EXECUTABLE
    if not (browser_dir / "INSTALLATION_COMPLETE").is_file() or not executable.is_file():
        return None, "gstack upstream Chromium executable is missing."
    if not os.access(executable, os.X_OK):
        return None, "gstack upstream Chromium executable is not executable."
    return executable, None


def _verify_gstack_runtime_root(root: Path) -> tuple[bool, str]:
    if not root.is_dir():
        return False, "gstack runtime is missing."
    manifest_path = root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "gstack runtime manifest is missing or invalid."
    if manifest != _runtime_manifest():
        return False, "gstack runtime manifest does not match StackOS's pinned runtime."
    bun = root / "bin" / "bun"
    if not bun.is_file() or not os.access(bun, os.X_OK):
        return False, "gstack Bun runtime is missing."
    cli = root / "gstack" / "browse" / "dist" / "browse"
    if not cli.is_file() or not os.access(cli, os.X_OK):
        return False, "gstack native CLI is missing."
    if not (root / "gstack" / "browse" / "src" / "server.ts").is_file():
        return False, "gstack native server is missing."
    if not (root / "gstack" / "LICENSE").is_file() or not (root / "gstack" / "NOTICE.md").is_file():
        return False, "gstack license notices are missing."
    browsers = root / "browsers"
    if not browsers.is_dir():
        return False, "gstack browser assets are missing."
    _chromium, chromium_reason = _upstream_chromium_executable(root)
    if chromium_reason is not None:
        return False, chromium_reason
    return True, "gstack runtime verified."


def verify_gstack_runtime(
    *,
    data_dir: Path | None = None,
    runtime_root: Path | None = None,
) -> tuple[bool, str]:
    """Verify the immutable gstack distribution without downloading or changing it."""
    root = (
        Path(runtime_root) / GSTACK_RUNTIME_DIRNAME
        if runtime_root is not None
        else gstack_runtime_root(data_dir)
    )
    return _verify_gstack_runtime_root(root)


def _build_gstack_runtime(candidate: Path, *, timeout_seconds: int) -> None:
    """Build the fixed upstream distribution in a staging directory only."""
    source_archive = candidate.parent / "gstack.tar.gz"
    bun_archive = candidate.parent / "bun.zip"
    _download_runtime_archive(
        GSTACK_SOURCE_ARCHIVE_URL,
        source_archive,
        stage="gstack source download",
        timeout_seconds=timeout_seconds,
    )
    if _sha256(source_archive) != GSTACK_SOURCE_ARCHIVE_SHA256:
        raise _GstackRuntimeInstallError(
            "gstack source verification",
            "checksum did not match the pinned runtime; retry `stackos install`.",
        )
    _download_runtime_archive(
        BUN_ARCHIVE_URL,
        bun_archive,
        stage="Bun download",
        timeout_seconds=timeout_seconds,
    )
    if _sha256(bun_archive) != BUN_ARCHIVE_SHA256:
        raise _GstackRuntimeInstallError(
            "Bun verification",
            "checksum did not match the pinned runtime; retry `stackos install`.",
        )

    source_unpack = candidate.parent / "gstack-source"
    bun_unpack = candidate.parent / "bun-source"
    _extract_gstack_source_archive(source_archive, source_unpack)
    source_root = _gstack_source_root(source_unpack)
    try:
        candidate.mkdir(parents=True, exist_ok=False)
        shutil.copytree(source_root, candidate / "gstack", symlinks=True)
    except OSError as exc:
        raise _GstackRuntimeInstallError(
            "gstack runtime staging",
            "could not stage the verified source; retry `stackos install`.",
        ) from exc
    bundled_bun = _extract_bun_archive(bun_archive, bun_unpack)
    bun = candidate / "bin" / "bun"
    bun.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bundled_bun, bun)
    bun.chmod(0o755)
    browsers = candidate / "browsers"
    browsers.mkdir()
    env = os.environ.copy()
    env["PATH"] = f"{bun.parent}{os.pathsep}{env.get('PATH', '')}"
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    gstack_dir = candidate / "gstack"
    _run_gstack_build(
        [str(bun), "install", "--frozen-lockfile"],
        stage="gstack dependency installation",
        cwd=gstack_dir,
        env=env,
        timeout_seconds=timeout_seconds,
    )
    _run_gstack_build(
        [str(bun), "build", "--compile", "browse/src/cli.ts", "--outfile", "browse/dist/browse"],
        stage="gstack CLI compilation",
        cwd=gstack_dir,
        env=env,
        timeout_seconds=timeout_seconds,
    )
    _run_gstack_build(
        [str(bun), "run", "vendor:xterm"],
        stage="gstack browser asset preparation",
        cwd=gstack_dir,
        env=env,
        timeout_seconds=timeout_seconds,
    )
    _run_gstack_build(
        ["bash", "scripts/write-version-files.sh", "browse/dist/.version"],
        stage="gstack version metadata preparation",
        cwd=gstack_dir,
        env=env,
        timeout_seconds=timeout_seconds,
    )
    _run_gstack_build(
        [str(bun), "node_modules/playwright/cli.js", "install", "--no-shell", "chromium"],
        stage="gstack browser asset installation",
        cwd=gstack_dir,
        env=env,
        timeout_seconds=timeout_seconds,
    )
    _remove_playwright_build_links(browsers)
    (candidate / "manifest.json").write_text(
        json.dumps(_runtime_manifest(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _install_gstack_runtime_atomically(candidate: Path, target: Path) -> None:
    """Replace only the runtime directory, preserving session profiles beside it."""
    backup = target.with_name(f".{target.name}.previous-{uuid.uuid4().hex}")
    moved_target = False
    try:
        if target.exists():
            target.replace(backup)
            moved_target = True
        candidate.replace(target)
    except Exception:
        _remove_path(target)
        if moved_target and backup.exists():
            backup.replace(target)
        raise
    finally:
        _remove_path(backup)


def ensure_gstack_runtime(
    *,
    data_dir: Path | None = None,
    runtime_root: Path | None = None,
    timeout_seconds: int = 900,
) -> tuple[bool, str]:
    """Install or repair the pinned, self-contained gstack browser distribution."""
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return False, "gstack runtime is available only for macOS arm64."
    if runtime_root is not None:
        target = Path(runtime_root) / GSTACK_RUNTIME_DIRNAME
        runtime_ok, _reason = verify_gstack_runtime(runtime_root=Path(runtime_root))
        if runtime_ok:
            return True, "Bundled gstack runtime present."
        result_message = "Bundled gstack runtime installed."
    elif packaged_stackos_root() is not None:
        runtime_ok, _reason = verify_gstack_runtime(data_dir=data_dir)
        if runtime_ok:
            return True, "Bundled gstack runtime present."
        return False, "Bundled gstack runtime is missing; repair the StackOS app installation."
    else:
        target = gstack_runtime_root(data_dir)
        runtime_ok, _reason = verify_gstack_runtime(data_dir=data_dir)
        if runtime_ok:
            return True, "Managed gstack runtime present."
        result_message = "Managed gstack runtime installed."

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="stackos-gstack-", dir=target.parent) as stage_name:
            candidate = Path(stage_name) / GSTACK_RUNTIME_DIRNAME
            _build_gstack_runtime(candidate, timeout_seconds=timeout_seconds)
            candidate_ok, candidate_reason = _verify_gstack_runtime_root(candidate)
            if not candidate_ok:
                return False, candidate_reason
            _install_gstack_runtime_atomically(candidate, target)
    except _GstackRuntimeInstallError as exc:
        return False, f"gstack runtime install failed during {exc.stage}: {exc.reason}"
    except Exception:
        return (
            False,
            "gstack runtime install failed while staging the verified runtime; "
            "run `stackos install` again.",
        )
    return True, result_message


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
    return copy_skills_to(target)


def copy_skills_to(target: Path) -> tuple[Path, int]:
    """Mirror the canonical StackOS skill set into one exact skills directory."""

    source = _resolve_source("skills")
    if isinstance(source, Path):
        _mirror_path(source, target, exclude_dirs=())
    else:
        _mirror_traversable(source, target, exclude_dirs=())
    count = sum(1 for _ in target.rglob("SKILL.md"))
    return target, count


def copy_stackos_skill_to(skills_root: Path) -> Path:
    """Mirror only the canonical StackOS skill without touching sibling skills."""

    source_root = _resolve_source("skills")
    target = skills_root / "stackos"
    if isinstance(source_root, Path):
        _mirror_path(source_root, target, exclude_dirs=())
    else:
        _mirror_traversable(source_root, target, exclude_dirs=())
    return target


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

    from stackos.host_mcp.bridge import resolve_bridge_command

    command = resolve_bridge_command(runtime="codex")
    return {
        "mcpServers": {
            MCP_SERVER_NAME: {
                "command": command[0],
                "args": command[1:],
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
    operation_ok = not any(
        result.status in {"token_missing", "register_failed", "remove_failed"}
        or result.connection_state == "repair_needed"
        for result in aggregate.results
    )
    return operation_ok, aggregate.summary_lines()


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
    "copy_skills_to",
    "copy_stackos_skill_to",
    "detect_mode",
    "ensure_gstack_runtime",
    "register_mcp_claude",
    "register_mcp_codex",
    "register_plugin_marketplace",
    "remove_mcp_hosts",
    "remove_plugins",
    "remove_skills",
    "repair_mcp_hosts",
    "verify_gstack_runtime",
]
