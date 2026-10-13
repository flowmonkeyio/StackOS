"""The single owner of local encrypted backup policy, snapshots and automation."""

import json
import os
import sqlite3
import stat
import tempfile
import time
import uuid
import zipfile
from collections.abc import Sequence
from contextlib import ExitStack, closing, suppress
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from pydantic import ValidationError

from stackos.config import Settings

from . import limits
from .envelope import archive_header, encrypt_payload, fingerprint
from .filesystem import (
    CHUNK,
    atomic_json,
    backup_lock,
    canonical,
    digest_file,
    directory_inventory,
    fsync_directory,
    identity,
    inside,
    open_regular,
    open_relative,
    overlap,
    private_directory,
    read_json,
    read_regular,
    source_lock,
)
from .models import BackupError, BackupPolicy, BackupTarget, parse_public_key

POLICY_FILE = "backup-policy.json"
OUTCOME_FILE = "backup-outcome.json"
KEY_EXCLUSIONS_FILE = "backup-key-exclusions.json"
MANIFEST_SCHEMA = "stackos.local-backup.v2"
PRIVATE_PEM_BEGIN = b"-----BEGIN PRIVATE KEY-----"
PRIVATE_PEM_END = b"-----END PRIVATE KEY-----"
MAX_PRIVATE_PEM_BYTES = 8192


def load_policy(settings: Settings) -> BackupPolicy:
    try:
        return BackupPolicy.model_validate(read_json(settings.state_dir / POLICY_FILE, {}))
    except ValidationError as exc:
        raise BackupError("invalid_policy", "The saved backup policy is invalid.") from exc


def _exclusions(settings: Settings) -> list[Path]:
    record = read_json(settings.state_dir / KEY_EXCLUSIONS_FILE, {"paths": []})
    if not isinstance(record, dict) or not isinstance(record.get("paths"), list):
        raise BackupError("invalid_metadata", "Recovery-key custody metadata is invalid.")
    if any(not isinstance(item, str) or not Path(item).is_absolute() for item in record["paths"]):
        raise BackupError("invalid_metadata", "Recovery-key custody metadata is invalid.")
    return [Path(item) for item in record["paths"]]


def _destination(settings: Settings, policy: BackupPolicy) -> Path:
    return canonical(policy.destination or settings.state_dir / "backups")


def _validate_policy(
    settings: Settings, policy: BackupPolicy, *, check_sources: bool = True
) -> BackupPolicy:
    state, data = canonical(settings.state_dir), canonical(settings.data_dir)
    destination = _destination(settings, policy)
    public = canonical(settings.generated_assets_dir)
    if destination == Path("/") or inside(destination, public) or inside(destination, data):
        raise BackupError(
            "unsafe_destination", "Choose a private backup destination outside daemon data."
        )
    if destination == state or inside(state, destination) or inside(data, destination):
        raise BackupError("unsafe_destination", "The backup destination cannot contain live state.")
    if destination.exists() and not destination.is_dir():
        raise BackupError("unsafe_destination", "The backup destination must be a directory.")
    excluded = _exclusions(settings)
    if any(inside(key, destination) for key in excluded):
        raise BackupError(
            "recovery_key_capture", "The destination contains a recovery private key."
        )
    ids: set[str] = set()
    roots: list[Path] = []
    targets: list[BackupTarget] = []
    for target in policy.targets:
        root = canonical(target.path)
        if root == Path("/") or (root.exists() and not root.is_dir()):
            raise BackupError("source_unavailable", "A registered backup folder is unavailable.")
        if check_sources and not root.is_dir():
            raise BackupError("source_unavailable", "A registered backup folder is unavailable.")
        if target.id in ids or any(overlap(root, other) for other in roots):
            raise BackupError(
                "overlapping_targets", "Backup folders and IDs must be unique and nonoverlapping."
            )
        if overlap(root, state) or overlap(root, data) or overlap(root, destination):
            raise BackupError(
                "overlapping_targets",
                "External folders cannot overlap live state or the destination.",
            )
        if any(inside(key, root) for key in excluded):
            raise BackupError(
                "recovery_key_capture", "A registered folder contains a recovery private key."
            )
        lock = canonical(target.lock_file) if target.lock_file is not None else None
        if lock is not None and (check_sources or lock.exists()):
            fd = open_regular(lock)
            os.close(fd)
        ids.add(target.id)
        roots.append(root)
        targets.append(target.model_copy(update={"path": root, "lock_file": lock}))
    if policy.enabled and policy.recipient_public_key is None:
        raise BackupError(
            "key_required", "Configure a recovery public key before enabling backups."
        )
    return policy.model_copy(update={"destination": destination, "targets": targets})


def save_policy(settings: Settings, policy: BackupPolicy) -> BackupPolicy:
    # Exclusion state has a separate owner and cannot be overwritten by an API patch.
    with backup_lock(settings.state_dir):
        validated = _validate_policy(
            settings, BackupPolicy.model_validate(policy), check_sources=policy.enabled
        )
        atomic_json(settings.state_dir / POLICY_FILE, validated.model_dump(mode="json"))
        return validated


def update_policy(
    settings: Settings, patch: dict[str, Any], *, upsert_target: BackupTarget | None = None
) -> BackupPolicy:
    """Apply explicit fields against current durable state under one lock."""
    with backup_lock(settings.state_dir):
        current = load_policy(settings).model_dump(mode="json")
        current.update(patch)
        if upsert_target is not None:
            if "targets" in patch:
                raise BackupError("invalid_policy", "Choose either target replacement or upsert.")
            current["targets"] = [
                item for item in current["targets"] if item["id"] != upsert_target.id
            ] + [upsert_target.model_dump(mode="json")]
        try:
            policy = BackupPolicy.model_validate(current)
        except ValidationError as exc:
            raise BackupError("invalid_policy", "The backup configuration is invalid.") from exc
        policy = _validate_policy(settings, policy, check_sources=policy.enabled)
        atomic_json(settings.state_dir / POLICY_FILE, policy.model_dump(mode="json"))
        return policy


def backup_status(settings: Settings) -> dict[str, Any]:
    policy = load_policy(settings)
    ready = policy.recipient_public_key is not None
    readiness_error: dict[str, str] | None = None
    try:
        policy = _validate_policy(settings, policy)
        for path in (settings.db_path, settings.seed_path, settings.token_path):
            fd = open_regular(path)
            os.close(fd)
        marker = settings.seed_path.with_suffix(settings.seed_path.suffix + ".new")
        if marker.exists() or marker.is_symlink():
            raise BackupError(
                "seed_rotation", "Finish the pending seed rotation before backing up."
            )
    except BackupError as exc:
        ready = False
        readiness_error = exc.as_dict()
    outcome = read_json(settings.state_dir / OUTCOME_FILE, {})
    return {
        "configured": policy.recipient_public_key is not None,
        "ready": ready,
        "enabled": policy.enabled,
        "interval_hours": policy.interval_hours,
        "keep_last": policy.keep_last,
        "destination": str(policy.destination or settings.state_dir / "backups"),
        "targets": [target.model_dump(mode="json") for target in policy.targets],
        "recipient_fingerprint": fingerprint(parse_public_key(policy.recipient_public_key))
        if policy.recipient_public_key
        else None,
        "last_attempt": outcome.get("last_attempt"),
        "last_success": outcome.get("last_success"),
        "last_error": outcome.get("last_error") or readiness_error,
    }


def generate_recovery_key(
    settings: Settings, private_key_path: Path, public_key_path: Path
) -> dict[str, str]:
    private, public = canonical(private_key_path), canonical(public_key_path)
    with backup_lock(settings.state_dir):
        policy = load_policy(settings)
        roots = [
            canonical(settings.data_dir),
            canonical(settings.state_dir),
            _destination(settings, policy),
        ]
        roots.extend(canonical(target.path) for target in policy.targets)
        if private == public or any(inside(private, root) for root in roots):
            raise BackupError(
                "recovery_key_capture",
                "Store the recovery private key outside all backup and live-data folders.",
            )
        if private.exists() or public.exists():
            raise BackupError("output_exists", "Recovery key files must not already exist.")
        private_directory(private.parent)
        private_directory(public.parent)
        key = X25519PrivateKey.generate()
        # Record custody first. A failed key write leaves a conservative exclusion.
        exclusions = _exclusions(settings)
        if private not in exclusions:
            exclusions.append(private)
        atomic_json(
            settings.state_dir / KEY_EXCLUSIONS_FILE, {"paths": [str(item) for item in exclusions]}
        )
        created: list[Path] = []
        try:
            values = (
                (
                    private,
                    key.private_bytes(
                        serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption(),
                    ),
                ),
                (
                    public,
                    key.public_key().public_bytes(
                        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
                    ),
                ),
            )
            for path, content in values:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                created.append(path)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                fsync_directory(path.parent)
        except OSError as exc:
            for path in created:
                path.unlink(missing_ok=True)
            raise BackupError(
                "key_write_failed", "Recovery key files could not be written safely."
            ) from exc
        return {
            "private_key_path": str(private),
            "public_key_path": str(public),
            "recipient_fingerprint": fingerprint(key.public_key()),
        }


def _scan_private_keys(content: bytes, recipient: X25519PublicKey, *, final: bool = False) -> bytes:
    """Examine every PEM block and return only bounded unfinished scan state."""
    position = 0
    while (begin := content.find(PRIVATE_PEM_BEGIN, position)) >= 0:
        end = content.find(PRIVATE_PEM_END, begin + len(PRIVATE_PEM_BEGIN))
        if end < 0:
            remaining = content[begin:]
            if len(remaining) > MAX_PRIVATE_PEM_BYTES or final:
                raise BackupError(
                    "recovery_key_capture",
                    "A backup source contains an oversized or incomplete private-key block.",
                    "Move recovery private keys outside backup sources.",
                )
            return remaining
        block = content[begin : end + len(PRIVATE_PEM_END)]
        if len(block) > MAX_PRIVATE_PEM_BYTES:
            raise BackupError(
                "recovery_key_capture",
                "A backup source contains an oversized private-key block.",
                "Move recovery private keys outside backup sources.",
            )
        try:
            key = serialization.load_pem_private_key(block, password=None)
        except (ValueError, TypeError):
            # An invalid outer block cannot hide a valid nested begin marker.
            position = begin + len(PRIVATE_PEM_BEGIN)
            continue
        if isinstance(key, X25519PrivateKey) and fingerprint(key.public_key()) == fingerprint(
            recipient
        ):
            raise BackupError(
                "recovery_key_capture",
                "A backup source contains the recovery private key.",
                "Move recovery private keys outside backup sources.",
            )
        position = end + len(PRIVATE_PEM_END)
    return b"" if final else content[-(len(PRIVATE_PEM_BEGIN) - 1) :]


def _copy_file(
    fd: int, destination: Path, expected: tuple[int, ...], recipient: X25519PublicKey
) -> None:
    if identity(os.fstat(fd)) != expected:
        raise BackupError("source_changed", "A backup source changed before reading.")
    private_directory(destination.parent)
    with os.fdopen(fd, "rb", closefd=False) as source, destination.open("xb") as output:
        os.chmod(destination, 0o600)
        # Keep bounded partial markers/blocks, including PEMs across read boundaries.
        previous = b""
        while chunk := source.read(CHUNK):
            previous = _scan_private_keys(previous + chunk, recipient)
            output.write(chunk)
        _scan_private_keys(previous, recipient, final=True)
        if identity(os.fstat(fd)) != expected:
            raise BackupError("source_changed", "A backup source changed during reading.")


def _snapshot_database(source: Path, destination: Path) -> None:
    fd = open_regular(source)
    value = os.fstat(fd)
    os.close(fd)
    private_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(private_fd)
    # SQLite's online backup incorporates committed WAL records consistently.
    deadline = time.monotonic() + 30

    def progress(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() > deadline:
            raise BackupError(
                "database_busy", "The database snapshot exceeded its time limit.", "Retry later."
            )

    try:
        with (
            closing(
                sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)
            ) as source_db,
            closing(sqlite3.connect(destination)) as target_db,
        ):
            source_db.backup(target_db, pages=256, progress=progress)
            target_db.execute("PRAGMA journal_mode=DELETE")
            if target_db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise BackupError(
                    "invalid_database", "The database snapshot failed its integrity check."
                )
    except sqlite3.Error as exc:
        raise BackupError("database_failed", "The database snapshot could not be created.") from exc
    current = source.lstat()
    if not stat.S_ISREG(current.st_mode) or (value.st_dev, value.st_ino) != (
        current.st_dev,
        current.st_ino,
    ):
        raise BackupError("source_changed", "The database file changed identity during backup.")
    os.chmod(destination, 0o600)


def _snapshot(
    settings: Settings,
    policy: BackupPolicy,
    staging: Path,
    recipient: X25519PublicKey,
    created_at: str,
) -> Path:
    from stackos import __version__

    payload = staging / "payload"
    private_directory(payload / "data")
    private_directory(payload / "state")
    rotation = settings.seed_path.with_suffix(settings.seed_path.suffix + ".new")
    if rotation.exists() or rotation.is_symlink():
        raise BackupError("seed_rotation", "Finish the pending seed rotation before backing up.")
    seed = read_regular(settings.seed_path)
    if len(seed) != 32:
        raise BackupError("invalid_seed", "The database encryption seed has an invalid size.")
    seed_identity = identity(settings.seed_path.lstat())
    with ExitStack() as locks:
        for target in policy.targets:
            if target.lock_file is not None:
                locks.enter_context(source_lock(target.lock_file))
        inventories = {
            target.id: directory_inventory(target.path, target.lock_file)
            for target in policy.targets
        }
        _snapshot_database(settings.db_path, payload / "data" / "stackos.db")
        if (
            rotation.exists()
            or rotation.is_symlink()
            or identity(settings.seed_path.lstat()) != seed_identity
            or read_regular(settings.seed_path) != seed
        ):
            raise BackupError(
                "seed_rotation",
                "The database seed changed during backup.",
                "Retry after seed rotation completes.",
            )
        core = [(settings.seed_path, "state/seed.bin"), (settings.token_path, "state/auth.token")]
        seed_backup = settings.seed_path.with_suffix(settings.seed_path.suffix + ".bak")
        if seed_backup.exists() or seed_backup.is_symlink():
            core.append((seed_backup, "state/seed.bin.bak"))
        for source, name in core:
            fd = open_regular(source)
            try:
                expected: tuple[int, ...] = identity(os.fstat(fd))
                _copy_file(fd, payload / name, expected, recipient)
                if identity(source.lstat()) != expected:
                    raise BackupError(
                        "source_changed", "A core backup source changed during reading."
                    )
            finally:
                os.close(fd)
        for target in policy.targets:
            target_payload = payload / "external" / target.id
            private_directory(target_payload)
            for relative, expected in inventories[target.id].items():
                if relative == "/":
                    continue
                if relative.endswith("/"):
                    private_directory(target_payload / relative)
                    continue
                fd = open_relative(target.path, relative)
                try:
                    _copy_file(fd, target_payload / relative, expected, recipient)
                finally:
                    os.close(fd)
            if directory_inventory(target.path, target.lock_file) != inventories[target.id]:
                raise BackupError("source_changed", "A registered folder changed during backup.")
        for target in policy.targets:
            if directory_inventory(target.path, target.lock_file) != inventories[target.id]:
                raise BackupError("source_changed", "A registered folder changed during backup.")
        if (
            rotation.exists()
            or rotation.is_symlink()
            or identity(settings.seed_path.lstat()) != seed_identity
            or read_regular(settings.seed_path) != seed
        ):
            raise BackupError("seed_rotation", "The database seed changed during backup.")
    atomic_json(payload / "state" / "backup-policy.reference.json", policy.model_dump(mode="json"))
    members = sorted(path for path in payload.rglob("*") if path.is_file())
    directories = sorted(path for path in payload.rglob("*") if path.is_dir())
    if len(members) + len(directories) + 1 > limits.MAX_MEMBERS:
        raise BackupError("payload_too_large", "The backup exceeds supported archive limits.")
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "created_at": created_at,
        "stackos_version": __version__,
        "source": {"data_dir": str(settings.data_dir), "state_dir": str(settings.state_dir)},
        "targets": [target.model_dump(mode="json") for target in policy.targets],
        "included": [
            {
                "path": str(path.relative_to(payload)),
                "bytes": path.stat().st_size,
                "sha256": digest_file(path),
            }
            for path in members
        ],
        "excluded": [
            "daemon logs",
            "provider caches/TDLib state outside the database",
            "plugin/skill mirrors",
            "generated assets",
            "coordination lock files",
        ],
        "restore_status": "inert recovery directory; separate local adoption required",
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
    if (
        len(manifest_bytes) > limits.MAX_MANIFEST_BYTES
        or len(manifest_bytes) + sum(path.stat().st_size for path in members)
        > limits.MAX_EXPANDED_BYTES
    ):
        raise BackupError("payload_too_large", "The backup exceeds supported archive limits.")
    archive_path = staging / "payload.zip"
    fd = os.open(archive_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with (
        os.fdopen(fd, "wb") as stream,
        # Stored entries keep creation coherent with hostile-ZIP expansion guards.
        zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive,
    ):
        archive.writestr("manifest.json", manifest_bytes)
        for path in members:
            archive.write(path, str(path.relative_to(payload)))
        # Preserve empty enrolled directories too, without trusting source permissions.
        for path in directories:
            info = zipfile.ZipInfo(str(path.relative_to(payload)) + "/")
            info.external_attr = (stat.S_IFDIR | 0o700) << 16
            archive.writestr(info, b"")
    if archive_path.stat().st_size > limits.MAX_ARCHIVE_BYTES:
        raise BackupError("payload_too_large", "The backup exceeds supported archive limits.")
    return archive_path


def _safe_output(settings: Settings, policy: BackupPolicy, output: Path) -> Path:
    output = canonical(output)
    if output.exists() or output.is_symlink():
        raise BackupError("output_exists", "A backup will not overwrite an existing output.")
    if inside(output, canonical(settings.generated_assets_dir)) or inside(
        output, canonical(settings.data_dir)
    ):
        raise BackupError(
            "unsafe_destination", "Backups cannot be written into public or live-data folders."
        )
    if any(inside(output, target.path) for target in policy.targets) or any(
        output == path for path in _exclusions(settings)
    ):
        raise BackupError("unsafe_destination", "The backup output overlaps a protected source.")
    if any(inside(path, output.parent) for path in _exclusions(settings)):
        raise BackupError(
            "recovery_key_capture", "Store backup outputs separately from recovery private keys."
        )
    if output in {
        settings.seed_path.resolve(),
        settings.token_path.resolve(),
        (settings.state_dir / POLICY_FILE).resolve(),
    }:
        raise BackupError("unsafe_destination", "The backup output overlaps live state.")
    return output


def _create_locked(
    settings: Settings,
    policy: BackupPolicy,
    *,
    output: Path | None,
    recipient_public_key: str | None,
    scheduled: bool,
    now: datetime,
) -> dict[str, Any]:
    outcome = read_json(settings.state_dir / OUTCOME_FILE, {})
    prior_outcome = deepcopy(outcome)
    attempt = {"started_at": now.isoformat(), "scheduled": scheduled, "status": "running"}
    outcome["last_attempt"] = attempt
    atomic_json(settings.state_dir / OUTCOME_FILE, outcome)
    temporary: Path | None = None
    published: Path | None = None
    try:
        policy = _validate_policy(settings, policy)
        key_pem = recipient_public_key or policy.recipient_public_key
        if key_pem is None:
            raise BackupError(
                "key_required", "Configure a recovery public key before creating a backup."
            )
        recipient = parse_public_key(key_pem)
        if output is None:
            stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
            prefix = "stackos-auto" if scheduled else "stackos-backup"
            output = (
                _destination(settings, policy) / f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}.sbak"
            )
        output = _safe_output(settings, policy, output)
        private_directory(output.parent)
        with tempfile.TemporaryDirectory(
            prefix=".stackos-backup-", dir=settings.state_dir
        ) as scratch:
            staging = Path(scratch)
            os.chmod(staging, 0o700)
            plaintext = _snapshot(settings, policy, staging, recipient, now.isoformat())
            fd, name = tempfile.mkstemp(prefix=".backup-encrypted-", dir=output.parent)
            temporary = Path(name)
            with os.fdopen(fd, "wb") as stream:
                descriptor = encrypt_payload(plaintext, stream, recipient)
                stream.flush()
                os.fsync(stream.fileno())
            digest, size = digest_file(temporary), temporary.stat().st_size
            # Link provides exclusive atomic publication; a race can never overwrite.
            os.link(temporary, output, follow_symlinks=False)
            published = output
            temporary.unlink()
            temporary = None
            fsync_directory(output.parent)
        result = {
            "path": str(output),
            "sha256": digest,
            "bytes": size,
            "created_at": now.isoformat(),
            "target_ids": [target.id for target in policy.targets],
            "encryption": descriptor,
        }
        attempt.update({"finished_at": datetime.now(UTC).isoformat(), "status": "success"})
        outcome.update({"last_attempt": attempt, "last_success": result, "last_error": None})
        if scheduled:
            history = outcome.get("scheduled_archives", [])
            history.append(result)
            outcome["scheduled_archives"] = history
            outcome["last_scheduled_success"] = now.isoformat()
        atomic_json(settings.state_dir / OUTCOME_FILE, outcome)
        if scheduled:
            # Retention failure cannot invalidate a published successful archive.
            with suppress(OSError, BackupError):
                _prune(settings, policy, outcome)
        return result
    except Exception as exc:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if published is not None:
            published.unlink(missing_ok=True)
        error = (
            exc
            if isinstance(exc, BackupError)
            else BackupError(
                "backup_failed",
                "The backup could not be completed safely.",
                "Check disk space, source availability and configuration, then retry.",
            )
        )
        attempt.update({"finished_at": datetime.now(UTC).isoformat(), "status": "failed"})
        outcome = prior_outcome
        outcome.update({"last_attempt": attempt, "last_error": error.as_dict()})
        atomic_json(settings.state_dir / OUTCOME_FILE, outcome)
        raise error from (None if isinstance(exc, BackupError) else exc)


def create_backup(
    settings: Settings,
    *,
    output: Path | None = None,
    recipient_public_key: str | None = None,
    extra_targets: Sequence[BackupTarget] = (),
) -> dict[str, Any]:
    with backup_lock(settings.state_dir):
        policy = load_policy(settings)
        if extra_targets:
            policy = policy.model_copy(update={"targets": [*policy.targets, *extra_targets]})
        return _create_locked(
            settings,
            policy,
            output=output,
            recipient_public_key=recipient_public_key,
            scheduled=False,
            now=datetime.now(UTC),
        )


def _prune(settings: Settings, policy: BackupPolicy, outcome: dict[str, Any]) -> None:
    history = outcome.get("scheduled_archives", [])
    eligible = [
        item for item in history if Path(item["path"]).parent == _destination(settings, policy)
    ]
    remove = eligible[: -policy.keep_last]
    removed: list[str] = []
    for item in remove:
        path = Path(item["path"])
        try:
            if path.is_symlink() or not path.name.startswith("stackos-auto-") or not path.is_file():
                continue
            if (
                digest_file(path) != item["sha256"]
                or archive_header(path)["recipient_fingerprint"]
                != item["encryption"]["recipient_fingerprint"]
            ):
                continue
            path.unlink()
            removed.append(item["path"])
        except (OSError, BackupError):
            continue
    if removed:
        outcome["scheduled_archives"] = [item for item in history if item["path"] not in removed]
        atomic_json(settings.state_dir / OUTCOME_FILE, outcome)


def run_due_backup(settings: Settings, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise BackupError("invalid_time", "Backup scheduling requires a timezone-aware time.")
    try:
        with backup_lock(settings.state_dir):
            policy = load_policy(settings)
            if not policy.enabled:
                return {"status": "skipped", "reason": "disabled"}
            outcome = read_json(settings.state_dir / OUTCOME_FILE, {})
            success = outcome.get("last_scheduled_success")
            if success and now < datetime.fromisoformat(success) + timedelta(
                hours=policy.interval_hours
            ):
                return {"status": "skipped", "reason": "not_due"}
            attempt = outcome.get("last_attempt")
            if (
                attempt
                and attempt.get("status") in {"failed", "running"}
                and now < datetime.fromisoformat(attempt["started_at"]) + timedelta(hours=1)
            ):
                return {"status": "skipped", "reason": "retry_backoff"}
            try:
                result = _create_locked(
                    settings,
                    policy,
                    output=None,
                    recipient_public_key=None,
                    scheduled=True,
                    now=now,
                )
            except BackupError as exc:
                return {"status": "failed", "error": exc.as_dict()}
            return {"status": "success", **result}
    except BackupError as exc:
        if exc.code == "backup_busy":
            return {"status": "skipped", "reason": "busy"}
        raise
