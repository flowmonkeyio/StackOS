"""structlog configuration for the daemon.

JSON output for the file sink (machine-grep friendly), pretty console output
when stderr is a TTY (developer ergonomics). `run_id` and `project_id` are
contextvars that run-plan/tool execution can set so downstream log lines
inherit them without explicit threading.
"""

from __future__ import annotations

import contextvars
import fcntl
import json
import logging
import os
import stat
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog

# Contextvars exposed to the rest of the codebase. Run execution binds these
# around work so everything logged inside inherits.
run_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)
project_id_var: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "project_id", default=None
)

_LOG_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
_LOG_FILE_BACKUPS = 5

_configured = False


def _open_private_log(path: Path) -> int:
    fd = os.open(
        path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
    )
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise OSError("Log target must be a private regular file.")
        os.fchmod(fd, 0o600)
    except BaseException:
        os.close(fd)
        raise
    return fd


def append_json_log(log_path: Path, event: dict[str, Any]) -> None:
    """Persist one pre-sanitized event with the shared 10 MiB / five-backup policy.

    Bridge processes cannot share the daemon's open RotatingFileHandler safely.
    A short interprocess lock serializes rotation and append; reopen each time
    so another process's rollover cannot strand writes in an old inode. Raise
    failures to the caller rather than logging raw exceptions or losing them.
    """
    data = (json.dumps(event, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    if len(data) > _LOG_FILE_BYTES:
        raise ValueError("Log event exceeds the retention bound.")
    log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_fd = _open_private_log(log_path.with_name(log_path.name + ".lock"))
    try:
        deadline = time.monotonic() + 0.25
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Log lock unavailable.") from None
                time.sleep(0.01)
        fd = _open_private_log(log_path)
        try:
            if os.fstat(fd).st_size + len(data) > _LOG_FILE_BYTES:
                for index in range(_LOG_FILE_BACKUPS, 0, -1):
                    source = log_path if index == 1 else Path(f"{log_path}.{index - 1}")
                    if source.exists() or source.is_symlink():
                        source.replace(Path(f"{log_path}.{index}"))
                os.close(fd)
                fd = -1
                fd = _open_private_log(log_path)
            with os.fdopen(fd, "ab", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
        finally:
            if fd >= 0:
                os.close(fd)
        directory_fd = os.open(log_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        os.close(lock_fd)


def _inject_contextvars(
    _logger: object,
    _name: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    """Add `run_id` and `project_id` to every log entry when set."""
    rid = run_id_var.get()
    pid = project_id_var.get()
    if rid is not None:
        event_dict.setdefault("run_id", rid)
    if pid is not None:
        event_dict.setdefault("project_id", pid)
    return event_dict


def configure_logging(*, log_path: Path, level: str = "INFO") -> None:
    """Configure structlog + stdlib logging once.

    Idempotent: re-invoking is a no-op so tests that build multiple apps in
    the same process do not stack handlers.
    """
    global _configured
    if _configured:
        return

    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=_LOG_FILE_BYTES,
        backupCount=_LOG_FILE_BACKUPS,
        encoding="utf-8",
    )
    # File renderer: JSON, no colours.
    file_handler.setFormatter(
        logging.Formatter("%(message)s"),
    )

    handlers: list[logging.Handler] = [file_handler]
    if sys.stderr.isatty():
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(logging.Formatter("%(message)s"))
        handlers.append(console_handler)

    root = logging.getLogger()
    # Wipe pre-existing handlers — uvicorn's reloader can leave duplicates
    # across reloads.
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in handlers:
        root.addHandler(h)
    root.setLevel(level)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _inject_contextvars,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level, logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog bound logger; thin wrapper for import ergonomics."""
    return structlog.get_logger(name)
