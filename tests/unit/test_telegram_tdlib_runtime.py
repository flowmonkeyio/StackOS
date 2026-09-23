from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from stackos.integrations.telegram_tdlib.runtime import (
    TDLIB_SOURCE_COMMIT,
    TelegramTdlibRuntimeError,
    tdlib_library_path,
    verify_tdlib_runtime,
)


def _runtime_layout(root: Path, *, library: bytes = b"tdjson fixture") -> Path:
    path = root / "lib" / "libtdjson.dylib"
    path.parent.mkdir(parents=True)
    path.write_bytes(library)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "runtime": "telegram-tdlib",
                "source": {
                    "repository": "https://github.com/tdlib/td.git",
                    "commit": TDLIB_SOURCE_COMMIT,
                    "archive_sha256": (
                        "5f5ebdb7f658b1a71ba6cb4f823017ad6649352427ce2a274cebc4b0dd2216e9"
                    ),
                },
                "tdlib_version": "1.8.67",
                "platform": {"os": "darwin", "arch": "arm64"},
                "build": {
                    "manifest_version": 1,
                    "dependency_policy": "system-only",
                    "source_archive_sha256": (
                        "5f5ebdb7f658b1a71ba6cb4f823017ad6649352427ce2a274cebc4b0dd2216e9"
                    ),
                },
                "library": {
                    "path": "lib/libtdjson.dylib",
                    "sha256": hashlib.sha256(library).hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_runtime_verification_uses_only_the_managed_root(tmp_path: Path) -> None:
    runtime_root = tmp_path / "telegram-tdlib-runtime"
    library = _runtime_layout(runtime_root)

    verified = verify_tdlib_runtime(runtime_root=runtime_root)

    assert verified.library_path == library
    assert tdlib_library_path(runtime_root=runtime_root) == library
    assert verified.source_commit == TDLIB_SOURCE_COMMIT


def test_runtime_verification_rejects_a_modified_library(tmp_path: Path) -> None:
    runtime_root = tmp_path / "telegram-tdlib-runtime"
    library = _runtime_layout(runtime_root)
    library.write_bytes(b"modified")

    with pytest.raises(TelegramTdlibRuntimeError, match="integrity"):
        verify_tdlib_runtime(runtime_root=runtime_root)


def test_runtime_verification_rejects_a_library_outside_its_manifest_root(tmp_path: Path) -> None:
    runtime_root = tmp_path / "telegram-tdlib-runtime"
    _runtime_layout(runtime_root)
    manifest_path = runtime_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["library"]["path"] = "../libtdjson.dylib"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(TelegramTdlibRuntimeError, match="managed runtime root"):
        verify_tdlib_runtime(runtime_root=runtime_root)


def test_runtime_verification_rejects_an_unpinned_source_archive(tmp_path: Path) -> None:
    runtime_root = tmp_path / "telegram-tdlib-runtime"
    _runtime_layout(runtime_root)
    manifest_path = runtime_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["source"]["archive_sha256"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(TelegramTdlibRuntimeError, match="source archive"):
        verify_tdlib_runtime(runtime_root=runtime_root)
