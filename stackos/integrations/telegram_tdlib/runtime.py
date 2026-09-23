"""Verification for the StackOS-managed TDLib runtime.

The daemon deliberately loads only the library shipped in the StackOS payload
or the explicitly managed development runtime.  It never searches system,
Homebrew, or user-library paths for ``tdjson``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stackos.browser.runtime import packaged_stackos_root

TDLIB_RUNTIME_DIRNAME = "telegram-tdlib-runtime"
TDLIB_MANIFEST_FILENAME = "manifest.json"
TDLIB_LIBRARY_RELATIVE_PATH = Path("lib/libtdjson.dylib")
TDLIB_SOURCE_REPOSITORY = "https://github.com/tdlib/td.git"
TDLIB_SOURCE_COMMIT = "d1085f9cebc5a62379991ae1652673954f229c1f"
TDLIB_SOURCE_ARCHIVE_SHA256 = "5f5ebdb7f658b1a71ba6cb4f823017ad6649352427ce2a274cebc4b0dd2216e9"
TDLIB_BUILD_MANIFEST_VERSION = 1
TDLIB_VERSION = "1.8.67"
TDLIB_RUNTIME_REPAIR = (
    "Telegram delivery is unavailable because the managed TDLib runtime is missing or invalid. "
    "Repair the packaged StackOS app, or in a source checkout stage the pinned runtime "
    "into the configured StackOS data directory."
)


class TelegramTdlibRuntimeError(RuntimeError):
    """A managed TDLib runtime is missing, invalid, or incompatible."""


@dataclass(frozen=True)
class TelegramTdlibRuntime:
    """The verified local-library address and immutable build identity."""

    runtime_root: Path
    library_path: Path
    source_commit: str
    tdlib_version: str
    library_sha256: str


def _data_dir(data_dir: Path | None) -> Path:
    if data_dir is not None:
        return Path(data_dir)
    from stackos.config import get_settings

    return Path(get_settings().data_dir)


def tdlib_runtime_root(data_dir: Path | None = None) -> Path:
    """Return the single daemon-owned runtime root without installing anything."""
    packaged_root = packaged_stackos_root()
    if packaged_root is not None:
        return packaged_root / TDLIB_RUNTIME_DIRNAME
    return _data_dir(data_dir) / TDLIB_RUNTIME_DIRNAME


def tdlib_library_path(
    data_dir: Path | None = None, *, runtime_root: Path | None = None
) -> Path | None:
    """Return a verified library location, or ``None`` when the runtime is absent."""
    root = Path(runtime_root) if runtime_root is not None else tdlib_runtime_root(data_dir)
    try:
        return verify_tdlib_runtime(runtime_root=root).library_path
    except TelegramTdlibRuntimeError:
        return None


def _manifest_value(manifest: dict[str, Any], *keys: str) -> Any:
    value: Any = manifest
    for key in keys:
        if not isinstance(value, dict):
            raise TelegramTdlibRuntimeError("Telegram runtime manifest is malformed.")
        value = value.get(key)
    return value


def _require_string(manifest: dict[str, Any], *keys: str) -> str:
    value = _manifest_value(manifest, *keys)
    if not isinstance(value, str) or not value:
        raise TelegramTdlibRuntimeError("Telegram runtime manifest is malformed.")
    return value


def _require_integer(manifest: dict[str, Any], *keys: str) -> int:
    value = _manifest_value(manifest, *keys)
    if not isinstance(value, int) or isinstance(value, bool):
        raise TelegramTdlibRuntimeError("Telegram runtime manifest is malformed.")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_tdlib_runtime(*, runtime_root: Path) -> TelegramTdlibRuntime:
    """Validate a pinned, self-contained TDLib runtime before it can be loaded."""
    root = Path(runtime_root)
    manifest_path = root / TDLIB_MANIFEST_FILENAME
    if (
        not root.is_dir()
        or root.is_symlink()
        or not manifest_path.is_file()
        or manifest_path.is_symlink()
    ):
        raise TelegramTdlibRuntimeError(
            "Telegram runtime is not installed or its manifest is unreadable. "
            f"{TDLIB_RUNTIME_REPAIR}"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TelegramTdlibRuntimeError(
            "Telegram runtime is not installed or its manifest is unreadable. "
            f"{TDLIB_RUNTIME_REPAIR}"
        ) from exc
    if not isinstance(manifest, dict):
        raise TelegramTdlibRuntimeError("Telegram runtime manifest is malformed.")
    if manifest.get("runtime") != "telegram-tdlib":
        raise TelegramTdlibRuntimeError("Telegram runtime manifest has an unexpected runtime kind.")
    if _require_string(manifest, "source", "repository") != TDLIB_SOURCE_REPOSITORY:
        raise TelegramTdlibRuntimeError(
            "Telegram runtime manifest has an unexpected source repository."
        )
    source_commit = _require_string(manifest, "source", "commit")
    if source_commit != TDLIB_SOURCE_COMMIT:
        raise TelegramTdlibRuntimeError(
            "Telegram runtime manifest has an unexpected source commit."
        )
    try:
        source_archive_sha256 = _require_string(manifest, "source", "archive_sha256")
    except TelegramTdlibRuntimeError as exc:
        raise TelegramTdlibRuntimeError(
            "Telegram runtime manifest has an unexpected source archive digest."
        ) from exc
    if source_archive_sha256 != TDLIB_SOURCE_ARCHIVE_SHA256:
        raise TelegramTdlibRuntimeError(
            "Telegram runtime manifest has an unexpected source archive digest."
        )
    if _require_integer(manifest, "build", "manifest_version") != TDLIB_BUILD_MANIFEST_VERSION:
        raise TelegramTdlibRuntimeError(
            "Telegram runtime manifest has an unsupported build manifest version."
        )
    if _require_string(manifest, "build", "dependency_policy") != "system-only":
        raise TelegramTdlibRuntimeError(
            "Telegram runtime manifest has an unsupported native dependency policy."
        )
    try:
        build_source_archive_sha256 = _require_string(manifest, "build", "source_archive_sha256")
    except TelegramTdlibRuntimeError as exc:
        raise TelegramTdlibRuntimeError(
            "Telegram runtime manifest has an unexpected build source archive digest."
        ) from exc
    if build_source_archive_sha256 != TDLIB_SOURCE_ARCHIVE_SHA256:
        raise TelegramTdlibRuntimeError(
            "Telegram runtime manifest has an unexpected build source archive digest."
        )
    if _require_string(manifest, "tdlib_version") != TDLIB_VERSION:
        raise TelegramTdlibRuntimeError(
            "Telegram runtime manifest has an unexpected TDLib version."
        )
    if (
        _require_string(manifest, "platform", "os") != "darwin"
        or _require_string(manifest, "platform", "arch") != "arm64"
    ):
        raise TelegramTdlibRuntimeError("Telegram runtime is not built for supported macOS arm64.")
    relative_library = Path(_require_string(manifest, "library", "path"))
    if relative_library.is_absolute() or ".." in relative_library.parts:
        raise TelegramTdlibRuntimeError(
            "Telegram library must remain inside its managed runtime root."
        )
    if relative_library != TDLIB_LIBRARY_RELATIVE_PATH:
        raise TelegramTdlibRuntimeError(
            "Telegram runtime manifest has an unexpected library location."
        )
    library_path = root / relative_library
    if not library_path.is_file() or library_path.is_symlink():
        raise TelegramTdlibRuntimeError("Telegram runtime library is missing.")
    expected_digest = _require_string(manifest, "library", "sha256").lower()
    if len(expected_digest) != 64 or any(
        char not in "0123456789abcdef" for char in expected_digest
    ):
        raise TelegramTdlibRuntimeError(
            "Telegram runtime manifest has an invalid integrity digest."
        )
    try:
        observed_digest = _sha256(library_path)
    except OSError as exc:
        raise TelegramTdlibRuntimeError("Telegram runtime library could not be read.") from exc
    if observed_digest != expected_digest:
        raise TelegramTdlibRuntimeError("Telegram runtime library failed integrity verification.")
    return TelegramTdlibRuntime(
        runtime_root=root,
        library_path=library_path,
        source_commit=source_commit,
        tdlib_version=TDLIB_VERSION,
        library_sha256=observed_digest,
    )


__all__ = [
    "TDLIB_BUILD_MANIFEST_VERSION",
    "TDLIB_LIBRARY_RELATIVE_PATH",
    "TDLIB_RUNTIME_DIRNAME",
    "TDLIB_RUNTIME_REPAIR",
    "TDLIB_SOURCE_ARCHIVE_SHA256",
    "TDLIB_SOURCE_COMMIT",
    "TDLIB_SOURCE_REPOSITORY",
    "TDLIB_VERSION",
    "TelegramTdlibRuntime",
    "TelegramTdlibRuntimeError",
    "tdlib_library_path",
    "tdlib_runtime_root",
    "verify_tdlib_runtime",
]
