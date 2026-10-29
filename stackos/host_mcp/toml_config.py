"""Bounded, preserving removal of an inspected native TOML MCP entry."""

from __future__ import annotations

import os
import stat
import tempfile
from contextlib import suppress
from pathlib import Path

import tomlkit
from tomlkit.container import Container
from tomlkit.exceptions import TOMLKitError
from tomlkit.items import Comment, Null, Table, Whitespace


def remove_mcp_server(
    path: Path, name: str, *, expected_entry: dict[str, object]
) -> tuple[bool, str | None, bool]:
    """Remove only the previously inspected entry, leaving user settings intact.

    A config symlink is intentional: replace its resolved regular-file target,
    retaining the link. Recheck the pathname, target identity, and original bytes
    immediately before replacement to refuse concurrent edits observed during
    preparation. Errors deliberately exclude config contents and parser text.
    """
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return True, None, False
    except OSError:
        return False, "Could not inspect the TOML configuration.", False

    try:
        target = path.resolve(strict=True)
        target_stat = target.lstat()
        if not stat.S_ISREG(target_stat.st_mode):
            return False, "The TOML configuration target must be a regular file.", False
        original = target.read_bytes()
    except (OSError, RuntimeError):
        return False, "Could not read the TOML configuration target.", False

    try:
        document = tomlkit.parse(original.decode("utf-8"))
        servers = document.get("mcp_servers")
        if servers is None:
            return True, None, False
        if not isinstance(servers, dict):
            return False, "mcp_servers must be a TOML table.", False
        if name not in servers:
            return True, None, False
        entry = servers[name]
        if not isinstance(entry, dict):
            return False, "The MCP server entry must be a TOML table.", False
        if not _same_value(document.unwrap()["mcp_servers"][name], expected_entry):
            return False, "The MCP server entry changed since inspection; inspect it again.", False

        # tomlkit attaches comments preceding the next table to the previous
        # table. Retain trailing trivia in deleted table slots, including tables
        # split across out-of-order declarations, so sibling comments survive.
        slots = _table_trivia_slots(document)
        del servers[name]
        for container, index, trailing in slots:
            if trailing and isinstance(container.body[index][1], Null):
                container.body[index] = (None, tomlkit.ws(trailing))
        replacement = tomlkit.dumps(document).encode("utf-8")
    except (TOMLKitError, UnicodeError, ValueError):
        return False, "Could not parse or safely update the TOML configuration.", False

    temporary: str | None = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
        with os.fdopen(fd, "wb") as handle:
            handle.write(replacement)
            handle.flush()
            os.fchmod(handle.fileno(), stat.S_IMODE(target_stat.st_mode))
            os.fsync(handle.fileno())
        if not _config_unchanged(path, target, path_stat, target_stat, original):
            return False, "The TOML configuration changed during cleanup; inspect it again.", False
        os.replace(temporary, target)
        temporary = None
    except OSError:
        return False, "Could not atomically update the TOML configuration.", False
    finally:
        if temporary is not None:
            with suppress(OSError):
                os.unlink(temporary)
    return True, None, True


def _same_value(actual: object, expected: object) -> bool:
    # Python otherwise treats true and 1 as equal, hiding an entry change.
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _same_value(value, expected[key]) for key, value in actual.items()
        )
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _same_value(value, other) for value, other in zip(actual, expected, strict=True)
        )
    return actual == expected


def _table_trivia_slots(container: Container) -> list[tuple[Container, int, str]]:
    slots: list[tuple[Container, int, str]] = []
    for index, (_, item) in enumerate(container.body):
        if isinstance(item, Table):
            slots.append((container, index, _trailing_table_trivia(item)))
            slots.extend(_table_trivia_slots(item.value))
    return slots


def _trailing_table_trivia(table: Table) -> str:
    parts: list[str] = []
    for key, item in reversed(table.value.body):
        if key is None and isinstance(item, (Comment, Whitespace)):
            parts.append(item.as_string())
        else:
            if isinstance(item, Table):
                parts.append(_trailing_table_trivia(item))
            break
    return "".join(reversed(parts))


def _config_unchanged(
    path: Path,
    target: Path,
    path_stat: os.stat_result,
    target_stat: os.stat_result,
    original: bytes,
) -> bool:
    try:
        return (
            path.resolve(strict=True) == target
            and _file_state(path.lstat()) == _file_state(path_stat)
            and _file_state(target.lstat()) == _file_state(target_stat)
            and target.read_bytes() == original
            and _file_state(target.lstat()) == _file_state(target_stat)
        )
    except (OSError, RuntimeError):
        return False


def _file_state(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    # Reading the config (or resolving its symlink) may update access time.
    # Identity, permissions, size, and nanosecond change times detect edits.
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )
