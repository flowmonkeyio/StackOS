"""Private files, no-follow reads and cooperating-writer snapshot fences."""

import fcntl
import hashlib
import json
import os
import stat
import tempfile
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .models import BackupError

CHUNK = 1024 * 1024
MAX_JSON = 1024 * 1024


def identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def canonical(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute() or path.is_symlink():
        raise BackupError("unsafe_path", "Backup paths must be absolute and cannot be symlinks.")
    for parent in path.parents:
        if parent.is_symlink() and str(parent) not in {"/tmp", "/var", "/etc"}:
            raise BackupError("unsafe_path", "Backup paths cannot traverse symlink directories.")
    # Resolve platform aliases once; saved configuration is canonical thereafter.
    return path.resolve()


def inside(path: Path, root: Path) -> bool:
    # Compare canonical caseless forms without changing filesystem spelling.
    # macOS also aliases canonically equivalent Unicode names; resolve() alone
    # does not merge those spellings, including for not-yet-created outputs.
    path_parts = tuple(
        unicodedata.normalize("NFD", unicodedata.normalize("NFD", part).casefold())
        for part in path.parts
    )
    root_parts = tuple(
        unicodedata.normalize("NFD", unicodedata.normalize("NFD", part).casefold())
        for part in root.parts
    )
    return path_parts[: len(root_parts)] == root_parts


def overlap(first: Path, second: Path) -> bool:
    return inside(first, second) or inside(second, first)


def private_directory(path: Path) -> None:
    """Create private directories without changing existing caller-owned parents."""
    if path.is_symlink():
        raise BackupError("unsafe_path", "A backup directory cannot be a symlink.")
    try:
        path.mkdir(mode=0o700)
    except FileNotFoundError:
        private_directory(path.parent)
        private_directory(path)
    except FileExistsError:
        if path.is_symlink() or not path.is_dir():
            raise BackupError(
                "unsafe_path", "A backup directory must be a regular directory."
            ) from None
    else:
        os.chmod(path, 0o700)


def open_regular(path: Path, *, writable: bool = False) -> int:
    flags = (os.O_RDWR if writable else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise BackupError("source_unavailable", "A required backup file is unavailable.") from exc
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise BackupError("unsafe_file", "Backup sources must contain only regular files.")
    return fd


def read_regular(path: Path, *, limit: int = MAX_JSON) -> bytes:
    fd = open_regular(path)
    try:
        before = identity(os.fstat(fd))
        if before[2] > limit:
            raise BackupError("file_too_large", "A local backup metadata file exceeds its limit.")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        if len(data) > limit or identity(os.fstat(fd)) != before:
            raise BackupError("source_changed", "A backup source changed during reading.")
        if identity(path.lstat()) != before:
            raise BackupError("source_changed", "A backup source changed during reading.")
        return data
    finally:
        os.close(fd)


def read_json(path: Path, default: Any, *, limit: int = MAX_JSON) -> Any:
    if not path.exists() and not path.is_symlink():
        return default
    try:
        return json.loads(read_regular(path, limit=limit))
    except (ValueError, UnicodeError) as exc:
        raise BackupError("invalid_metadata", "Local backup metadata is invalid.") from exc


def fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path: Path, value: Any) -> None:
    private_directory(path.parent)
    if path.is_symlink():
        raise BackupError("unsafe_path", "Backup metadata cannot be a symlink.")
    fd, temporary = tempfile.mkstemp(prefix=".backup-json-", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(json.dumps(value, sort_keys=True, indent=2).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        fsync_directory(path.parent)
    finally:
        temporary_path.unlink(missing_ok=True)


@contextmanager
def backup_lock(state_dir: Path) -> Iterator[None]:
    private_directory(state_dir)
    lock_path = state_dir / ".backup.lock"
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    except OSError as exc:
        raise BackupError("unsafe_lock", "The backup coordination lock is unavailable.") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise BackupError("unsafe_lock", "The backup coordination lock is unsafe.")
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError(
                "backup_busy", "Another backup or configuration change is running.", "Retry later."
            ) from exc
        yield
    finally:
        os.close(fd)


@contextmanager
def source_lock(path: Path) -> Iterator[None]:
    fd = open_regular(path)
    try:
        before = identity(os.fstat(fd))
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError(
                "source_busy", "A registered backup folder has an active writer.", "Retry later."
            ) from exc
        yield
        if identity(path.lstat()) != before:
            raise BackupError("source_changed", "A source coordination lock changed during backup.")
    finally:
        os.close(fd)


def directory_inventory(root: Path, excluded: Path | None = None) -> dict[str, tuple[int, ...]]:
    """Walk pinned directory descriptors; never follow a source symlink."""
    result: dict[str, tuple[int, ...]] = {}
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise BackupError(
            "source_unavailable", "A registered backup folder is unavailable."
        ) from exc

    def walk(directory_fd: int, prefix: str) -> None:
        for entry in sorted(os.scandir(directory_fd), key=lambda item: item.name):
            name = f"{prefix}{entry.name}"
            if excluded is not None and root / name == excluded:
                continue
            value = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(value.st_mode):
                result[f"{name}/"] = (value.st_dev, value.st_ino)
                child = os.open(
                    entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd
                )
                try:
                    walk(child, f"{name}/")
                finally:
                    os.close(child)
            elif stat.S_ISREG(value.st_mode):
                result[name] = identity(value)
            else:
                raise BackupError("unsafe_file", "Registered folders contain an unsafe file.")

    try:
        value = os.fstat(fd)
        result["/"] = (value.st_dev, value.st_ino)
        walk(fd, "")
        root_value = root.lstat()
        if not stat.S_ISDIR(root_value.st_mode) or result["/"] != (
            root_value.st_dev,
            root_value.st_ino,
        ):
            raise BackupError("source_changed", "A registered backup folder changed during backup.")
    except OSError as exc:
        raise BackupError(
            "source_changed", "A registered backup folder changed during backup."
        ) from exc
    finally:
        os.close(fd)
    return result


def open_relative(root: Path, relative: str) -> int:
    """Each directory component is opened without following links."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = Path(relative).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        final = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        if not stat.S_ISREG(os.fstat(final).st_mode):
            os.close(final)
            raise BackupError("unsafe_file", "Backup sources must contain only regular files.")
        return final
    except OSError as exc:
        raise BackupError("source_changed", "A backup source changed during reading.") from exc
    finally:
        os.close(fd)


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def copy_verified_file(source: Path, output: Path, *, sha256: str, size: int) -> None:
    """Verify the bytes copied from one no-follow descriptor before exclusive publication."""
    fd = open_regular(source)
    temporary: Path | None = None
    try:
        before = os.fstat(fd)
        if before.st_nlink != 1 or before.st_size != size:
            raise BackupError(
                "archive_changed", "The saved backup archive no longer matches its receipt."
            )
        private_directory(output.parent)
        output_fd, name = tempfile.mkstemp(prefix=".backup-export-", dir=output.parent)
        temporary = Path(name)
        digest, copied = hashlib.sha256(), 0
        with (
            os.fdopen(output_fd, "wb") as destination,
            os.fdopen(fd, "rb", closefd=False) as origin,
        ):
            while chunk := origin.read(CHUNK):
                copied += len(chunk)
                if copied > size:
                    raise BackupError(
                        "archive_changed", "The backup archive changed during export."
                    )
                destination.write(chunk)
                digest.update(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        if (
            copied != size
            or digest.hexdigest() != sha256
            or identity(os.fstat(fd)) != identity(before)
            or identity(source.lstat()) != identity(before)
        ):
            raise BackupError(
                "archive_changed", "The saved backup archive no longer matches its receipt."
            )
        os.link(temporary, output, follow_symlinks=False)
        fsync_directory(output.parent)
    except FileExistsError as exc:
        raise BackupError(
            "output_exists", "Backup export will not overwrite an existing file."
        ) from exc
    finally:
        os.close(fd)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
