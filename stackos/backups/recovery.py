"""Authenticated recovery to an inert, fresh directory; never live installation."""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from . import limits
from .envelope import decrypt_payload
from .filesystem import (
    CHUNK,
    canonical,
    fsync_directory,
    private_directory,
    read_regular,
)
from .models import BackupError

MANIFEST_SCHEMA = "stackos.local-backup.v2"
CORE_NAMES = {
    "data/stackos.db",
    "state/seed.bin",
    "state/auth.token",
    "state/seed.bin.bak",
    "state/backup-policy.reference.json",
}
REQUIRED_NAMES = {
    "data/stackos.db",
    "state/seed.bin",
    "state/auth.token",
    "state/backup-policy.reference.json",
}
TARGET_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _allowed_name(name: str, *, directory: bool) -> bool:
    stripped = name[:-1] if directory else name
    path = PurePosixPath(stripped)
    if not stripped or "\\" in stripped or "\x00" in stripped or path.is_absolute():
        return False
    if any(part in {"", ".", ".."} for part in stripped.split("/")) or str(path) != stripped:
        return False
    parts = path.parts
    if directory:
        return stripped in {"data", "state", "external"} or (
            len(parts) >= 2 and parts[0] == "external" and TARGET_ID.fullmatch(parts[1]) is not None
        )
    return (
        stripped == "manifest.json"
        or stripped in CORE_NAMES
        or (
            len(parts) >= 3 and parts[0] == "external" and TARGET_ID.fullmatch(parts[1]) is not None
        )
    )


def _inspect(archive: zipfile.ZipFile) -> tuple[dict[str, Any], list[zipfile.ZipInfo]]:
    infos = archive.infolist()
    if len(infos) > limits.MAX_MEMBERS:
        raise BackupError("unsafe_archive", "The recovery archive has too many members.")
    names: set[str] = set()
    total = 0
    for info in infos:
        normalized = info.filename.rstrip("/")
        if normalized in names or not _allowed_name(info.filename, directory=info.is_dir()):
            raise BackupError(
                "unsafe_archive", "The recovery archive contains an unsafe or duplicate path."
            )
        names.add(normalized)
        mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if kind not in {0, stat.S_IFDIR if info.is_dir() else stat.S_IFREG} or info.flag_bits & 1:
            raise BackupError(
                "unsafe_archive", "The recovery archive contains an unsupported file type."
            )
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise BackupError(
                "unsafe_archive", "The recovery archive uses unsupported compression."
            )
        if info.is_dir() and info.file_size:
            raise BackupError(
                "unsafe_archive", "Archive directories must not contain payload data."
            )
        total += info.file_size
        if total > limits.MAX_EXPANDED_BYTES or info.file_size > limits.MAX_EXPANDED_BYTES:
            raise BackupError(
                "unsafe_archive", "The recovery archive exceeds supported size limits."
            )
        if (
            info.file_size > 1024 * 1024
            and info.file_size > max(info.compress_size, 1) * limits.MAX_COMPRESSION_RATIO
        ):
            raise BackupError(
                "unsafe_archive", "The recovery archive has an unsafe compression ratio."
            )
    if (
        "manifest.json" not in names
        or archive.getinfo("manifest.json").file_size > limits.MAX_MANIFEST_BYTES
    ):
        raise BackupError("invalid_manifest", "The recovery archive has no valid bounded manifest.")
    try:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest["schema"] != MANIFEST_SCHEMA or not isinstance(manifest["included"], list):
            raise ValueError("unsupported manifest")
        declared: dict[str, dict[str, Any]] = {}
        for entry in manifest["included"]:
            if set(entry) != {"path", "bytes", "sha256"} or not isinstance(entry["path"], str):
                raise ValueError("invalid member declaration")
            if (
                entry["path"] in declared
                or not _allowed_name(entry["path"], directory=False)
                or entry["path"] == "manifest.json"
            ):
                raise ValueError("invalid member path")
            if (
                type(entry["bytes"]) is not int
                or entry["bytes"] < 0
                or not isinstance(entry["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
            ):
                raise ValueError("invalid member integrity declaration")
            declared[entry["path"]] = entry
        files = {info.filename for info in infos if not info.is_dir()} - {"manifest.json"}
        if files != set(declared) or not REQUIRED_NAMES.issubset(files):
            raise ValueError("manifest set mismatch")
        target_ids = [item["id"] for item in manifest["targets"]]
        if len(target_ids) != len(set(target_ids)) or any(
            not isinstance(item, str) or not TARGET_ID.fullmatch(item) for item in target_ids
        ):
            raise ValueError("invalid source IDs")
        if any(f"external/{target_id}" not in names for target_id in target_ids):
            raise ValueError("missing source directory")
        for info in infos:
            if (
                info.filename.startswith("external/")
                and len(PurePosixPath(info.filename).parts) >= 2
                and PurePosixPath(info.filename).parts[1] not in target_ids
            ):
                raise ValueError("undeclared source ID")
            if (
                not info.is_dir()
                and info.filename != "manifest.json"
                and info.file_size != declared[info.filename]["bytes"]
            ):
                raise ValueError("member size mismatch")
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise BackupError(
            "invalid_manifest", "The recovery manifest does not match its archive."
        ) from exc
    return manifest, infos


def _extract_verified(archive: zipfile.ZipFile, output: Path) -> dict[str, Any]:
    manifest, infos = _inspect(archive)
    expected = {item["path"]: item for item in manifest["included"]}
    for info in infos:
        path = output / info.filename
        if info.is_dir():
            private_directory(path)
            continue
        private_directory(path.parent)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        digest = hashlib.sha256()
        size = 0
        with archive.open(info) as source, os.fdopen(fd, "wb") as target:
            while chunk := source.read(CHUNK):
                size += len(chunk)
                if size > info.file_size:
                    raise BackupError(
                        "invalid_manifest", "A recovered file exceeded its declared size."
                    )
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        if info.filename != "manifest.json":
            entry = expected[info.filename]
            if size != entry["bytes"] or digest.hexdigest() != entry["sha256"]:
                raise BackupError(
                    "integrity_failed", "A recovered file failed its integrity check."
                )
    try:
        with closing(
            sqlite3.connect(
                (output / "data/stackos.db").as_uri() + "?mode=ro&immutable=1", uri=True
            )
        ) as database:
            if database.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise BackupError(
                    "invalid_database", "The recovered database failed its integrity check."
                )
    except sqlite3.Error as exc:
        raise BackupError("invalid_database", "The recovered database is invalid.") from exc
    if (output / "state/seed.bin").stat().st_size != 32:
        raise BackupError("invalid_seed", "The recovered encryption seed has an invalid size.")
    return manifest


def restore_backup(archive: Path, private_key_path: Path, output_dir: Path) -> dict[str, Any]:
    archive, output = canonical(archive), canonical(output_dir)
    if output.exists() or output.is_symlink():
        raise BackupError("output_exists", "Recovery requires a fresh output directory.")
    try:
        key = serialization.load_pem_private_key(read_regular(private_key_path), password=None)
    except (ValueError, TypeError) as exc:
        raise BackupError(
            "invalid_private_key", "Use an unencrypted X25519 recovery private key PEM."
        ) from exc
    if not isinstance(key, X25519PrivateKey):
        raise BackupError("invalid_private_key", "Use an X25519 recovery private key PEM.")
    private_directory(output.parent)
    reserved = False
    try:
        with tempfile.TemporaryDirectory(
            prefix=".stackos-recovery-", dir=output.parent
        ) as temporary:
            scratch = Path(temporary)
            os.chmod(scratch, 0o700)
            plaintext = scratch / "authenticated.zip"
            header = decrypt_payload(archive, plaintext, key)
            # No ZIP parsing or recovered files exist before full GCM authentication.
            recovered = scratch / "recovered"
            private_directory(recovered)
            with zipfile.ZipFile(plaintext) as payload:
                manifest = _extract_verified(payload, recovered)
            # Reserve without overwriting another recovery. Only validated files are moved.
            os.mkdir(output, mode=0o700)
            reserved = True
            for path in recovered.iterdir():
                os.rename(path, output / path.name)
            fsync_directory(output)
            fsync_directory(output.parent)
        return {
            "path": str(output),
            "verified": True,
            "archive_sha256": header["archive_sha256"],
            "created_at": manifest["created_at"],
            "target_ids": [target["id"] for target in manifest["targets"]],
            "encryption": {
                "format": MANIFEST_SCHEMA,
                "algorithm": header["algorithm"],
                "recipient_fingerprint": header["recipient_fingerprint"],
            },
            "adoption_required": True,
        }
    except Exception as exc:
        if reserved:
            shutil.rmtree(output)
        if isinstance(exc, BackupError):
            raise
        raise BackupError(
            "recovery_failed",
            "The backup could not be recovered safely.",
            "Use an intact backup and a fresh recovery directory.",
        ) from exc
