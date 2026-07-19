"""Decision-free FTP remote-tree inspection and deletion planning.

The connector owns credentials, connections, dispatch, transfers, mutations,
redaction, and provider-facing results.  These helpers operate only on an
already-connected FTP client and return normalized remote-tree data.
"""

from __future__ import annotations

import ftplib
import posixpath
from collections.abc import Mapping
from contextlib import suppress
from typing import Any

from stackos.repositories.base import ValidationError

_MLSD_FACTS = ["type", "size", "modify", "unique", "perm"]


def resolved_remote_path(client: Any, value: str) -> str:
    require_safe_command_path(value, "remote_path")
    if value.startswith("/"):
        candidate = value
    else:
        current = _safe_pwd(client)
        if current is None:
            raise ValidationError("FTP server PWD is required to resolve relative remote paths")
        candidate = posixpath.join(current, value)
    normalized = posixpath.normpath(candidate)
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return normalized


def remote_stat(client: Any, remote_path: str) -> dict[str, Any] | None:
    require_safe_command_path(remote_path, "remote_path")
    try:
        response = client.sendcmd(f"MLST {remote_path}")
    except ftplib.all_errors:
        response = None
    if isinstance(response, str):
        parsed = _parse_mlst(response)
        if parsed is not None:
            return parsed

    original = _safe_pwd(client)
    try:
        client.cwd(remote_path)
    except ftplib.all_errors:
        pass
    else:
        if original is not None:
            with suppress(ftplib.Error, OSError, EOFError):
                client.cwd(original)
        return {"type": "directory"}
    finally:
        if original is not None and _safe_pwd(client) != original:
            with suppress(ftplib.Error, OSError, EOFError):
                client.cwd(original)

    try:
        size = client.size(remote_path)
    except ftplib.all_errors:
        return None
    return {"type": "file", "size": size}


def list_remote(client: Any, remote_path: str) -> list[dict[str, Any]]:
    require_safe_command_path(remote_path, "remote_path")
    try:
        raw_entries = list(client.mlsd(remote_path, facts=_MLSD_FACTS))
    except (AttributeError, ftplib.Error, OSError, EOFError):
        return _list_remote_fallback(client, remote_path)
    entries: list[dict[str, Any]] = []
    for name, facts in raw_entries:
        entry_type = _entry_type(facts)
        if entry_type in {"current", "parent"}:
            continue
        entries.append(
            {
                "name": name,
                "type": entry_type,
                "size": _optional_int(facts.get("size")),
                "modified": facts.get("modify"),
                "unique": facts.get("unique"),
                "permissions": facts.get("perm"),
            }
        )
    return sorted(entries, key=lambda item: str(item["name"]))


def recursive_delete_plan(client: Any, remote_path: str) -> list[dict[str, str]]:
    return _plan_remote_directory_delete(
        client,
        remote_path,
        root_identity=None,
        visited_identities=set(),
    )


def remote_directory_identity(client: Any, remote_path: str) -> str:
    original = _safe_pwd(client)
    resolved: str | None = None
    try:
        client.cwd(remote_path)
        resolved = _safe_pwd(client)
    finally:
        if original is not None and _safe_pwd(client) != original:
            with suppress(ftplib.Error, OSError, EOFError):
                client.cwd(original)
    if resolved is None:
        raise ValidationError("FTP server PWD is required to verify recursive directory identity")
    return posixpath.normpath(resolved)


def is_safe_remote_child(name: str) -> bool:
    return bool(
        name
        and name not in {".", ".."}
        and "/" not in name
        and "\\" not in name
        and not any(ord(char) < 32 or ord(char) == 127 for char in name)
    )


def public_entry_type(value: str) -> str:
    return value if value in {"directory", "file", "symlink"} else "unknown"


def require_safe_command_path(value: str, field_name: str) -> None:
    if not value:
        raise ValidationError(f"{field_name} must be a non-empty string")
    if any(char in value for char in ("\x00", "\r", "\n")):
        raise ValidationError(f"{field_name} cannot contain NUL, CR, or LF")


def _list_remote_fallback(client: Any, remote_path: str) -> list[dict[str, Any]]:
    names = client.nlst(remote_path)
    entries: list[dict[str, Any]] = []
    for raw_name in names:
        name = _listed_remote_child_name(remote_path, raw_name)
        if not name:
            continue
        path = posixpath.join(remote_path, name)
        stat = remote_stat(client, path) or {"type": "unknown"}
        entries.append(
            {
                "name": name,
                "type": stat.get("type", "unknown"),
                "size": stat.get("size"),
                "modified": None,
                "unique": None,
                "permissions": None,
            }
        )
    return sorted(entries, key=lambda item: str(item["name"]))


def _plan_remote_directory_delete(
    client: Any,
    remote_path: str,
    *,
    root_identity: str | None,
    visited_identities: set[str],
) -> list[dict[str, str]]:
    identity = remote_directory_identity(client, remote_path)
    if root_identity is None:
        root_identity = identity
    elif not _is_within_remote_directory(identity, root_identity):
        raise ValidationError(f"remote directory {remote_path} resolves outside the selected root")
    if identity in visited_identities:
        raise ValidationError(f"remote directory cycle or alias detected at {remote_path}")
    visited_identities.add(identity)
    plan: list[dict[str, str]] = []
    seen_names: set[str] = set()
    for entry in _list_remote_for_recursive_delete(client, remote_path):
        name = str(entry["name"])
        if not is_safe_remote_child(name):
            raise ValidationError(f"unsafe remote child name: {name!r}")
        if name in seen_names:
            raise ValidationError(f"duplicate remote child name: {name!r}")
        seen_names.add(name)
        entry_type = str(entry.get("type") or "unknown")
        if entry_type not in {"file", "directory", "symlink"}:
            raise ValidationError(
                "FTP recursive directory delete requires machine-readable MLSD or "
                f"MLST entry types; {name!r} has type {entry_type!r}"
            )
        child_path = posixpath.join(remote_path, name)
        require_safe_command_path(child_path, "remote_path")
        if entry_type == "directory":
            plan.extend(
                _plan_remote_directory_delete(
                    client,
                    child_path,
                    root_identity=root_identity,
                    visited_identities=visited_identities,
                )
            )
        else:
            plan.append({"remote_path": child_path, "type": entry_type})
    plan.append({"remote_path": remote_path, "type": "directory"})
    return plan


def _list_remote_for_recursive_delete(
    client: Any,
    remote_path: str,
) -> list[dict[str, Any]]:
    require_safe_command_path(remote_path, "remote_path")
    try:
        raw_entries = list(client.mlsd(remote_path, facts=_MLSD_FACTS))
    except (AttributeError, ftplib.Error):
        return _list_remote_with_mlst_types(client, remote_path)
    entries: list[dict[str, Any]] = []
    for name, facts in raw_entries:
        entry_type = _entry_type(facts)
        if entry_type in {"current", "parent"}:
            continue
        entries.append({"name": name, "type": entry_type})
    return sorted(entries, key=lambda item: str(item["name"]))


def _list_remote_with_mlst_types(
    client: Any,
    remote_path: str,
) -> list[dict[str, Any]]:
    try:
        names = client.nlst(remote_path)
    except AttributeError as exc:
        raise ValidationError(
            "FTP recursive directory delete requires machine-readable MLSD or MLST entry types"
        ) from exc
    entries: list[dict[str, Any]] = []
    for raw_name in names:
        name = _listed_remote_child_name(remote_path, raw_name)
        if not is_safe_remote_child(name):
            raise ValidationError(f"unsafe remote child name: {name!r}")
        path = posixpath.join(remote_path, name)
        try:
            response = client.sendcmd(f"MLST {path}")
        except (AttributeError, ftplib.Error) as exc:
            raise ValidationError(
                "FTP recursive directory delete requires machine-readable MLSD or MLST entry types"
            ) from exc
        parsed = _parse_mlst(str(response))
        entry_type = str(parsed.get("type") if parsed is not None else "unknown")
        if entry_type not in {"file", "directory", "symlink"}:
            raise ValidationError(
                "FTP recursive directory delete requires machine-readable MLSD or "
                f"MLST entry types; {name!r} has type {entry_type!r}"
            )
        entries.append({"name": name, "type": entry_type})
    return sorted(entries, key=lambda item: str(item["name"]))


def _listed_remote_child_name(remote_path: str, raw_name: Any) -> str:
    name = str(raw_name)
    prefix = remote_path.rstrip("/") + "/"
    if name.startswith(prefix):
        return name[len(prefix) :]
    if "/" in name:
        return posixpath.basename(name)
    return name


def _parse_mlst(response: str) -> dict[str, Any] | None:
    for line in response.splitlines():
        stripped = line.strip()
        if "type=" not in stripped.lower() or ";" not in stripped:
            continue
        fact_text = stripped.split(" ", 1)[0]
        facts: dict[str, str] = {}
        for part in fact_text.split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            facts[key.lower()] = value
        if "type" in facts:
            return {
                "type": _entry_type(facts),
                "size": _optional_int(facts.get("size")),
                "modified": facts.get("modify"),
            }
    return None


def _entry_type(facts: Mapping[str, Any]) -> str:
    raw = str(facts.get("type") or "unknown").lower()
    if raw == "cdir":
        return "current"
    if raw == "pdir":
        return "parent"
    if raw == "dir":
        return "directory"
    if raw == "file":
        return "file"
    if "slink" in raw or "symlink" in raw:
        return "symlink"
    return "unknown"


def _safe_pwd(client: Any) -> str | None:
    try:
        value = client.pwd()
    except ftplib.all_errors:
        return None
    resolved = str(value)
    require_safe_command_path(resolved, "server PWD")
    return resolved


def _is_within_remote_directory(path: str, root: str) -> bool:
    normalized_path = posixpath.normpath(path)
    normalized_root = posixpath.normpath(root)
    if normalized_root == "/":
        return normalized_path.startswith("/")
    return normalized_path == normalized_root or normalized_path.startswith(
        normalized_root.rstrip("/") + "/"
    )


def _optional_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "is_safe_remote_child",
    "list_remote",
    "public_entry_type",
    "recursive_delete_plan",
    "remote_directory_identity",
    "remote_stat",
    "require_safe_command_path",
    "resolved_remote_path",
]
