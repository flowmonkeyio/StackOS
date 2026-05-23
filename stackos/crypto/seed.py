"""Seed file lifecycle: ensure, load, rotate.

The seed is 32 bytes from ``os.urandom`` stored at
``~/.local/state/stackos/seed.bin`` mode 0600. We mirror the
``auth.ensure_token`` pattern: TOCTOU-safe ``O_EXCL`` create, refuse to
load if the file exists with the wrong mode (PLAN.md L1110-L1113).

Rotation (``stackos rotate-seed --reencrypt``) first re-encrypts
rows in memory, stages the new seed at ``seed.bin.new``, lets the CLI
commit the DB rows, then promotes the staged seed into place. The old
seed is kept as ``seed.bin.bak`` for one daemon boot — the next
``ensure_seed_file`` call deletes the backup so a stale seed can't sit
on disk indefinitely (PLAN.md L1140-L1142).
"""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path
from typing import Any

from stackos.repositories.base import RepositoryError

_SEED_BYTES = 32
_REQUIRED_MODE = 0o600


class SeedFileError(RepositoryError):
    """Raised when the seed file is missing or has the wrong permissions.

    The daemon refuses to start in this state — a too-wide seed file is
    a signal of a sloppy backup or a wrong-machine restore worth
    surfacing rather than silently fixing.
    """

    code = -32603
    http_status = 500
    retryable = False


def backup_seed_path(seed_path: Path) -> Path:
    """Return the rotation-backup path (``seed.bin.bak``) next to ``seed_path``."""
    return seed_path.with_suffix(seed_path.suffix + ".bak")


def _new_seed_path(seed_path: Path) -> Path:
    """Return the staging path used during rotation (``seed.bin.new``)."""
    return seed_path.with_suffix(seed_path.suffix + ".new")


def staged_seed_path(seed_path: Path) -> Path:
    """Return the staged rotation seed path (``seed.bin.new``)."""
    return _new_seed_path(seed_path)


def _file_mode_bits(path: Path) -> int:
    """Return the permission bits of ``path`` masked to standard rwx triplets."""
    return stat.S_IMODE(path.stat().st_mode)


def cleanup_old_backup(seed_path: Path) -> bool:
    """Delete ``seed.bin.bak`` if it exists. Returns ``True`` on deletion.

    Per PLAN.md L1142 the rotation backup is kept for **one daemon boot**
    so an operator can recover from a botched rotation; on the *next*
    boot the backup is auto-deleted. ``ensure_seed_file`` calls this on
    every successful load so the cleanup happens in the natural startup
    path.
    """
    backup = backup_seed_path(seed_path)
    if backup.exists():
        backup.unlink()
        return True
    return False


def ensure_seed_file(seed_path: Path) -> bytes:
    """Return the seed bytes, generating them if absent. Refuses bad modes.

    On generation: 32 bytes from ``os.urandom`` (via ``secrets.token_bytes``)
    written through ``os.open(O_CREAT|O_EXCL, 0o600)`` — TOCTOU-safe so a
    co-tenant cannot race us into reading a freshly-created world-readable
    file. We chmod 0600 again after write because some platforms apply
    umask to the ``os.open`` mode (see ``auth.ensure_token``).

    On existing file: load via ``load_seed`` (which validates mode) and
    delete any stale ``seed.bin.bak`` left over from the previous boot
    (PLAN.md L1142).

    Returns the 32-byte seed bytes.
    """
    staged = _new_seed_path(seed_path)
    if staged.exists():
        raise SeedFileError(
            f"incomplete seed rotation: staged seed exists at {staged}; "
            "finish the rotation or restore the database before starting the daemon",
            data={"seed_path": str(seed_path), "staged_seed_path": str(staged)},
        )

    if seed_path.exists():
        seed = load_seed(seed_path)
        cleanup_old_backup(seed_path)
        return seed

    seed_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        seed_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        _REQUIRED_MODE,
    )
    try:
        seed = secrets.token_bytes(_SEED_BYTES)
        os.write(fd, seed)
    finally:
        os.close(fd)
    os.chmod(seed_path, _REQUIRED_MODE)
    return seed


def load_seed(seed_path: Path) -> bytes:
    """Read the seed bytes; raise ``SeedFileError`` on missing or bad mode.

    The mode check is identical to the auth-token contract: too-wide
    permissions are a security signal we surface rather than silently
    repair (PLAN.md L1112-L1113).
    """
    if not seed_path.exists():
        raise SeedFileError(
            f"seed file at {seed_path} is missing — run `stackos init` to generate one",
            data={"seed_path": str(seed_path)},
        )
    mode = _file_mode_bits(seed_path)
    if mode != _REQUIRED_MODE:
        raise SeedFileError(
            f"seed file at {seed_path} has mode {oct(mode)}; expected {oct(_REQUIRED_MODE)}",
            data={"seed_path": str(seed_path), "mode": oct(mode)},
        )
    blob = seed_path.read_bytes()
    if len(blob) != _SEED_BYTES:
        raise SeedFileError(
            f"seed file at {seed_path} has length {len(blob)}; expected {_SEED_BYTES}",
            data={"seed_path": str(seed_path), "length": len(blob)},
        )
    return blob


def rotate_seed(
    seed_path: Path,
    *,
    rows: list[dict[str, Any]],
) -> tuple[bytes, list[dict[str, Any]]]:
    """Rotate the seed, re-encrypting every credential row in memory.

    The caller passes a list of dicts (one per ``integration_credentials``
    row) shaped as::

        {"id": int, "project_id": int|None, "kind": str,
         "encrypted_payload": bytes, "nonce": bytes}

    We:

    1. Decrypt every row under the *current* seed. Any decryption
       failure aborts the rotation and propagates the underlying
       ``CryptoError`` — the seed file is left untouched so the
       caller can retry.
    2. Generate a fresh 32-byte seed.
    3. Re-encrypt every row under the new seed (new nonce per row).
    4. Stage and atomically promote the seed file: write to
       ``seed.bin.new``, fsync, rename ``seed.bin → seed.bin.bak`` and
       ``seed.bin.new → seed.bin``. ``seed.bin.bak`` is auto-deleted on
       the next boot via ``cleanup_old_backup``.

    Returns ``(new_seed, rotated_rows)`` where ``rotated_rows`` is the
    same list of dicts with ``encrypted_payload`` and ``nonce`` updated.
    The DB write is the *caller's* responsibility. The CLI uses the
    lower-level stage/commit helpers so it can commit the DB rows before
    promoting the staged seed.
    """
    new_seed, rotated_rows = reencrypt_rows_for_seed_rotation(seed_path, rows=rows)
    stage_seed_rotation(seed_path, new_seed)
    commit_staged_seed_rotation(seed_path)
    return new_seed, rotated_rows


def reencrypt_rows_for_seed_rotation(
    seed_path: Path,
    *,
    rows: list[dict[str, Any]],
) -> tuple[bytes, list[dict[str, Any]]]:
    """Return ``(new_seed, rotated_rows)`` without mutating seed files."""
    # Local import to avoid a circular import at module load time.
    from stackos.crypto.aes_gcm import decrypt as _decrypt
    from stackos.crypto.aes_gcm import encrypt as _encrypt

    if not seed_path.exists():
        raise SeedFileError(
            f"cannot rotate: seed file at {seed_path} is missing",
            data={"seed_path": str(seed_path)},
        )

    old_seed = load_seed(seed_path)

    plaintexts: list[bytes] = []
    for row in rows:
        plaintexts.append(
            _decrypt(
                row["encrypted_payload"],
                nonce=row["nonce"],
                project_id=row["project_id"],
                kind=row["kind"],
                seed=old_seed,
            )
        )

    new_seed = secrets.token_bytes(_SEED_BYTES)
    rotated_rows: list[dict[str, Any]] = []
    for plaintext, row in zip(plaintexts, rows, strict=True):
        new_payload, new_nonce = _encrypt(
            plaintext,
            project_id=row["project_id"],
            kind=row["kind"],
            seed=new_seed,
        )
        rotated_rows.append(
            {
                **row,
                "encrypted_payload": new_payload,
                "nonce": new_nonce,
            }
        )
    return new_seed, rotated_rows


def stage_seed_rotation(seed_path: Path, new_seed: bytes) -> Path:
    """Write ``new_seed`` to ``seed.bin.new`` and fsync it."""
    if len(new_seed) != _SEED_BYTES:
        raise SeedFileError(
            f"new seed has length {len(new_seed)}; expected {_SEED_BYTES}",
            data={"seed_path": str(seed_path), "length": len(new_seed)},
        )
    new_path = _new_seed_path(seed_path)
    if new_path.exists():
        raise SeedFileError(
            f"cannot stage seed rotation: {new_path} already exists",
            data={"seed_path": str(seed_path), "staged_seed_path": str(new_path)},
        )
    fd = os.open(
        new_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        _REQUIRED_MODE,
    )
    try:
        os.write(fd, new_seed)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(new_path, _REQUIRED_MODE)
    _fsync_parent(seed_path)
    return new_path


def commit_staged_seed_rotation(seed_path: Path) -> None:
    """Promote ``seed.bin.new`` to ``seed.bin`` and keep old seed as backup."""
    new_path = _new_seed_path(seed_path)
    backup = backup_seed_path(seed_path)
    if not new_path.exists():
        raise SeedFileError(
            f"cannot commit seed rotation: staged seed at {new_path} is missing",
            data={"seed_path": str(seed_path), "staged_seed_path": str(new_path)},
        )
    load_seed(new_path)
    # Move current seed into the backup slot, then move new seed into place.
    if backup.exists():
        backup.unlink()
    os.rename(seed_path, backup)
    os.rename(new_path, seed_path)
    _fsync_parent(seed_path)


def abort_staged_seed_rotation(seed_path: Path) -> bool:
    """Delete ``seed.bin.new`` after a pre-commit rotation failure."""
    new_path = _new_seed_path(seed_path)
    if new_path.exists():
        new_path.unlink()
        _fsync_parent(seed_path)
        return True
    return False


def _fsync_parent(seed_path: Path) -> None:
    """fsync the parent directory so seed-file renames are durable."""
    parent_fd = os.open(str(seed_path.parent), os.O_RDONLY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


__all__ = [
    "SeedFileError",
    "abort_staged_seed_rotation",
    "backup_seed_path",
    "cleanup_old_backup",
    "commit_staged_seed_rotation",
    "ensure_seed_file",
    "load_seed",
    "reencrypt_rows_for_seed_rotation",
    "rotate_seed",
    "stage_seed_rotation",
    "staged_seed_path",
]
