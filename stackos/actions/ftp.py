"""FTP and explicit-FTPS action connector.

Official protocol references:
- FTP commands and replies: https://www.rfc-editor.org/rfc/rfc959
- MLST/MLSD machine-readable listings: https://www.rfc-editor.org/rfc/rfc3659
- Explicit FTP over TLS: https://www.rfc-editor.org/rfc/rfc4217
- Python ``ftplib`` adapter: https://docs.python.org/3/library/ftplib.html
"""

from __future__ import annotations

import asyncio
import codecs
import ftplib
import os
import posixpath
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from stackos.actions import ftp_remote_tree
from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.provider_utils import (
    credential_config,
    credential_payload,
    credential_value,
    issue,
    unknown_operation,
)
from stackos.integrations.ftp import (
    close_ftp_client,
    open_ftp_client,
    validate_ftp_credential_config,
)
from stackos.mcp.errors import IntegrationDownError
from stackos.repositories.base import ValidationError

_OPERATIONS = {
    "directory.list",
    "file.upload",
    "file.download",
    "file.delete",
    "directory.create",
    "directory.delete",
    "path.rename",
}
_CONFLICT_POLICIES = {"overwrite", "skip", "fail"}
_ERROR_POLICIES = {"stop", "continue"}
_TLS_MODES = {"explicit", "none"}


class FtpActionConnector:
    """Decision-free adapter for remote browsing, transfer, and path management."""

    key = "ftp"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        if request.operation not in _OPERATIONS:
            return unknown_operation(request)
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        if request.operation in {"directory.list", "file.delete", "directory.create"}:
            _validate_remote_path(payload.get("remote_path"), "$.remote_path", issues)
            return issues
        if request.operation == "directory.delete":
            _validate_remote_path(payload.get("remote_path"), "$.remote_path", issues)
            if not isinstance(payload.get("recursive"), bool):
                issues.append(issue("$.recursive", "recursive must be a boolean", "type_error"))
            return issues
        if request.operation == "path.rename":
            _validate_remote_path(payload.get("source_path"), "$.source_path", issues)
            _validate_remote_path(
                payload.get("destination_path"),
                "$.destination_path",
                issues,
            )
            return issues

        _validate_transfer_options(payload, issues)
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            issues.append(issue("$.items", "items must be a non-empty array", "required"))
            return issues
        for index, item in enumerate(items):
            path = f"$.items[{index}]"
            if not isinstance(item, Mapping):
                issues.append(issue(path, "item must be an object", "type_error"))
                continue
            _validate_remote_path(item.get("remote_path"), f"{path}.remote_path", issues)
            _validate_local_path(item.get("local_path"), f"{path}.local_path", issues)
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.operation not in _OPERATIONS:
            raise ValidationError(f"unsupported FTP operation {request.operation!r}")
        return await asyncio.to_thread(_execute_sync, request)


@dataclass
class _TransferState:
    operation: str
    error_policy: str
    secret: str = field(repr=False)
    completed: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    created_directories: list[dict[str, str]] = field(default_factory=list)
    bytes_transferred: int = 0

    def output(self, *, status: str | None = None) -> dict[str, Any]:
        if status is None:
            status = "partial" if self.failed else "success"
        output = {
            "provider": "ftp",
            "operation": self.operation,
            "status": status,
            "completed_count": len(self.completed),
            "skipped_count": len(self.skipped),
            "failed_count": len(self.failed),
            "created_directory_count": len(self.created_directories),
            "bytes_transferred": self.bytes_transferred,
            "completed": self.completed,
            "skipped": self.skipped,
            "failed": self.failed,
            "created_directories": self.created_directories,
            "completed_paths": [item["target_path"] for item in self.completed],
            "skipped_paths": [item["target_path"] for item in self.skipped],
            "failed_paths": [item["target_path"] for item in self.failed],
        }
        return _redact_payload(output, secret=self.secret)


@dataclass
class _DirectoryDeleteState:
    remote_path: str
    recursive: bool
    secret: str = field(repr=False)
    deleted_paths: list[dict[str, str]] = field(default_factory=list)

    def output(self, *, status: str = "success") -> dict[str, Any]:
        output = {
            "provider": "ftp",
            "operation": "directory.delete",
            "status": status,
            "remote_path": self.remote_path,
            "recursive": self.recursive,
            "deleted_count": len(self.deleted_paths),
            "file_count": sum(item["type"] == "file" for item in self.deleted_paths),
            "directory_count": sum(item["type"] == "directory" for item in self.deleted_paths),
            "symlink_count": sum(item["type"] == "symlink" for item in self.deleted_paths),
            "deleted_paths": self.deleted_paths,
        }
        return _redact_payload(output, secret=self.secret)


class _StopTransfer(Exception):
    pass


class _UploadOutcomeUnknown(Exception):
    def __init__(self, cause: Exception, *, attempted_bytes: int) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.attempted_bytes = attempted_bytes


def _execute_sync(request: ActionConnectorRequest) -> ActionConnectorResult:
    settings = _ftp_settings(request)
    client: Any | None = None
    state: _TransferState | None = None
    try:
        client = _connect(settings)
        if request.operation == "directory.list":
            return _browse(client, request, settings)
        if request.operation == "file.delete":
            return _delete_remote_file(client, request, settings)
        if request.operation == "directory.create":
            return _create_remote_directory(client, request, settings)
        if request.operation == "directory.delete":
            return _delete_remote_directory(client, request, settings)
        if request.operation == "path.rename":
            return _rename_remote_path(client, request, settings)

        state = _TransferState(
            operation=request.operation,
            error_policy=str(request.input_json["error_policy"]),
            secret=str(settings["password"]),
        )
        try:
            if request.operation == "file.upload":
                _upload(client, request, state)
            else:
                _download(client, request, state)
        except _StopTransfer as exc:
            raise ActionConnectorError(
                "FTP transfer stopped after a failed path",
                provider_error={"type": "transfer_failure", "message": str(exc)},
                output_json=_failure_output(state, str(exc)),
                metadata_json=_metadata(request, settings),
            ) from exc

        if state.failed and not state.completed and not state.skipped:
            raise ActionConnectorError(
                "FTP transfer failed for every requested path",
                provider_error={"type": "transfer_failure", "message": "all paths failed"},
                output_json=_failure_output(state, "all paths failed"),
                metadata_json=_metadata(request, settings),
            )
        return ActionConnectorResult(
            output_json=state.output(),
            metadata_json=_metadata(request, settings),
        )
    except ActionConnectorError:
        raise
    except IntegrationDownError as exc:
        provider_error = {
            "type": str(exc.data.get("reason_code") or type(exc).__name__),
            "message": exc.detail,
        }
        for key in ("stage", "reason_code", "reply_code"):
            if key in exc.data:
                provider_error[key] = str(exc.data[key])
        output = state.output(status="failed") if state is not None else None
        raise ActionConnectorError(
            "FTP provider operation failed",
            provider_error=provider_error,
            output_json=output,
            metadata_json=_metadata(request, settings),
        ) from exc
    except (ftplib.Error, OSError, EOFError, ValueError, ValidationError) as exc:
        output = state.output(status="failed") if state is not None else None
        raise ActionConnectorError(
            "FTP provider operation failed",
            provider_error={
                "type": type(exc).__name__,
                "message": _safe_error(exc, secret=str(settings["password"])),
            },
            output_json=output,
            metadata_json=_metadata(request, settings),
        ) from exc
    finally:
        _close(client)


def _browse(
    client: Any,
    request: ActionConnectorRequest,
    settings: Mapping[str, Any],
) -> ActionConnectorResult:
    secret = str(settings["password"])
    remote_path = ftp_remote_tree.resolved_remote_path(
        client, str(request.input_json["remote_path"])
    )
    entries: list[dict[str, Any]] = []
    for entry in ftp_remote_tree.list_remote(client, remote_path):
        name = str(entry["name"])
        safe = ftp_remote_tree.is_safe_remote_child(name)
        item = dict(entry)
        item["type"] = ftp_remote_tree.public_entry_type(str(item.get("type") or "unknown"))
        item["safe_to_traverse"] = safe
        item["remote_path"] = posixpath.join(remote_path, name) if safe else None
        if _contains_secret(item, secret=secret):
            item["safe_to_traverse"] = False
            item["remote_path"] = None
        entries.append(_redact_payload(item, secret=secret))
    return ActionConnectorResult(
        output_json={
            "provider": "ftp",
            "operation": request.operation,
            "status": "success",
            "remote_path": _redact_text(remote_path, secret=secret),
            "entry_count": len(entries),
            "directory_count": sum(item["type"] == "directory" for item in entries),
            "file_count": sum(item["type"] == "file" for item in entries),
            "symlink_count": sum(item["type"] == "symlink" for item in entries),
            "entries": entries,
        },
        metadata_json=_metadata(request, settings),
    )


def _delete_remote_file(
    client: Any,
    request: ActionConnectorRequest,
    settings: Mapping[str, Any],
) -> ActionConnectorResult:
    remote_path = ftp_remote_tree.resolved_remote_path(
        client, str(request.input_json["remote_path"])
    )
    guidance = "Inspect or list the selected remote path before deciding whether to retry."
    try:
        client.delete(remote_path)
    except (OSError, EOFError) as exc:
        raise _mutation_error(
            request,
            settings,
            exc,
            stage="delete",
            target_path=remote_path,
            outcome_unknown=True,
            reconciliation_guidance=guidance,
        ) from exc
    except ftplib.Error as exc:
        outcome_unknown = not _is_explicit_ftp_failure(exc)
        raise _mutation_error(
            request,
            settings,
            exc,
            stage="delete",
            target_path=remote_path,
            outcome_unknown=outcome_unknown,
            reconciliation_guidance=guidance if outcome_unknown else None,
        ) from exc
    return ActionConnectorResult(
        output_json={
            "provider": "ftp",
            "operation": request.operation,
            "status": "success",
            "remote_path": _redact_text(
                remote_path,
                secret=str(settings["password"]),
            ),
        },
        metadata_json=_metadata(request, settings),
    )


def _create_remote_directory(
    client: Any,
    request: ActionConnectorRequest,
    settings: Mapping[str, Any],
) -> ActionConnectorResult:
    remote_path = ftp_remote_tree.resolved_remote_path(
        client, str(request.input_json["remote_path"])
    )
    guidance = "Inspect or list the selected remote path before deciding whether to retry."
    try:
        client.mkd(remote_path)
    except (OSError, EOFError) as exc:
        raise _mutation_error(
            request,
            settings,
            exc,
            stage="create",
            target_path=remote_path,
            outcome_unknown=True,
            reconciliation_guidance=guidance,
        ) from exc
    except ftplib.Error as exc:
        outcome_unknown = not _is_explicit_ftp_failure(exc)
        raise _mutation_error(
            request,
            settings,
            exc,
            stage="create",
            target_path=remote_path,
            outcome_unknown=outcome_unknown,
            reconciliation_guidance=guidance if outcome_unknown else None,
        ) from exc
    return ActionConnectorResult(
        output_json={
            "provider": "ftp",
            "operation": request.operation,
            "status": "success",
            "remote_path": _redact_text(
                remote_path,
                secret=str(settings["password"]),
            ),
        },
        metadata_json=_metadata(request, settings),
    )


def _delete_remote_directory(
    client: Any,
    request: ActionConnectorRequest,
    settings: Mapping[str, Any],
) -> ActionConnectorResult:
    remote_path = ftp_remote_tree.resolved_remote_path(
        client, str(request.input_json["remote_path"])
    )
    recursive = bool(request.input_json["recursive"])
    state = _DirectoryDeleteState(
        remote_path=remote_path,
        recursive=recursive,
        secret=str(settings["password"]),
    )
    if recursive:
        try:
            plan = ftp_remote_tree.recursive_delete_plan(client, remote_path)
        except (ValidationError, ftplib.Error, OSError, EOFError, ValueError) as exc:
            partial = state.output(status="failed")
            raise _mutation_error(
                request,
                settings,
                exc,
                stage="plan",
                target_path=remote_path,
                partial_result=partial,
            ) from exc
    else:
        plan = [{"remote_path": remote_path, "type": "directory"}]

    guidance = (
        "Inspect or list the selected remote path and its parent before deciding whether to retry."
    )
    for item in plan:
        target_path = item["remote_path"]
        target_type = item["type"]
        try:
            if target_type == "directory":
                client.rmd(target_path)
            else:
                client.delete(target_path)
        except (OSError, EOFError) as exc:
            partial = state.output(status="failed")
            raise _mutation_error(
                request,
                settings,
                exc,
                stage="delete",
                target_path=target_path,
                target_type=target_type,
                outcome_unknown=True,
                reconciliation_guidance=guidance,
                partial_result=partial,
            ) from exc
        except ftplib.Error as exc:
            partial = state.output(status="failed")
            outcome_unknown = not _is_explicit_ftp_failure(exc)
            raise _mutation_error(
                request,
                settings,
                exc,
                stage="delete",
                target_path=target_path,
                target_type=target_type,
                outcome_unknown=outcome_unknown,
                reconciliation_guidance=guidance if outcome_unknown else None,
                partial_result=partial,
            ) from exc
        state.deleted_paths.append(dict(item))

    return ActionConnectorResult(
        output_json=state.output(),
        metadata_json=_metadata(request, settings),
    )


def _rename_remote_path(
    client: Any,
    request: ActionConnectorRequest,
    settings: Mapping[str, Any],
) -> ActionConnectorResult:
    source_path = ftp_remote_tree.resolved_remote_path(
        client, str(request.input_json["source_path"])
    )
    destination_path = ftp_remote_tree.resolved_remote_path(
        client,
        str(request.input_json["destination_path"]),
    )
    guidance = "Inspect or list both selected remote paths before deciding whether to retry."
    try:
        client.rename(source_path, destination_path)
    except (OSError, EOFError) as exc:
        raise _mutation_error(
            request,
            settings,
            exc,
            stage="rename",
            source_path=source_path,
            destination_path=destination_path,
            outcome_unknown=True,
            reconciliation_guidance=guidance,
        ) from exc
    except ftplib.Error as exc:
        outcome_unknown = not _is_explicit_ftp_failure(exc)
        raise _mutation_error(
            request,
            settings,
            exc,
            stage="rename",
            source_path=source_path,
            destination_path=destination_path,
            outcome_unknown=outcome_unknown,
            reconciliation_guidance=guidance if outcome_unknown else None,
        ) from exc
    secret = str(settings["password"])
    return ActionConnectorResult(
        output_json={
            "provider": "ftp",
            "operation": request.operation,
            "status": "success",
            "source_path": _redact_text(source_path, secret=secret),
            "destination_path": _redact_text(destination_path, secret=secret),
        },
        metadata_json=_metadata(request, settings),
    )


def _upload(client: Any, request: ActionConnectorRequest, state: _TransferState) -> None:
    payload = request.input_json
    conflict_policy = str(payload["conflict_policy"])
    follow_symlinks = bool(payload.get("follow_symlinks", False))
    for item in payload["items"]:
        local_path = Path(str(item["local_path"]))
        remote_path = ftp_remote_tree.resolved_remote_path(client, str(item["remote_path"]))
        try:
            if local_path.is_symlink() and not follow_symlinks:
                _record_skip(
                    state,
                    source_path=str(local_path),
                    target_path=remote_path,
                    reason="symlink_not_followed",
                )
            elif local_path.is_dir():
                _upload_directory(
                    client,
                    local_path,
                    remote_path,
                    conflict_policy,
                    follow_symlinks,
                    state,
                    ancestry=set(),
                )
            elif local_path.is_file():
                _upload_file(client, local_path, remote_path, conflict_policy, state)
            else:
                raise FileNotFoundError(f"local path does not exist: {local_path}")
        except _StopTransfer:
            raise
        except Exception as exc:
            _record_failure(
                state,
                source_path=str(local_path),
                target_path=remote_path,
                exc=exc,
            )


def _upload_directory(
    client: Any,
    local_dir: Path,
    remote_dir: str,
    conflict_policy: str,
    follow_symlinks: bool,
    state: _TransferState,
    *,
    ancestry: set[tuple[int, int]],
) -> None:
    stat_result = local_dir.stat()
    identity = (stat_result.st_dev, stat_result.st_ino)
    if identity in ancestry:
        raise ValidationError(f"symlink cycle detected at {local_dir}")
    branch_ancestry = {*ancestry, identity}
    _ensure_remote_directory(client, remote_dir, state)

    with os.scandir(local_dir) as iterator:
        entries = sorted(iterator, key=lambda item: item.name)
    for entry in entries:
        local_child = local_dir / entry.name
        remote_child = posixpath.join(remote_dir, entry.name)
        try:
            ftp_remote_tree.require_safe_command_path(remote_child, "remote_path")
            if entry.is_symlink() and not follow_symlinks:
                _record_skip(
                    state,
                    source_path=str(local_child),
                    target_path=remote_child,
                    reason="symlink_not_followed",
                )
            elif entry.is_dir(follow_symlinks=follow_symlinks):
                _upload_directory(
                    client,
                    local_child,
                    remote_child,
                    conflict_policy,
                    follow_symlinks,
                    state,
                    ancestry=branch_ancestry,
                )
            elif entry.is_file(follow_symlinks=follow_symlinks):
                _upload_file(client, local_child, remote_child, conflict_policy, state)
            else:
                _record_skip(
                    state,
                    source_path=str(local_child),
                    target_path=remote_child,
                    reason="unsupported_local_file_type",
                )
        except _StopTransfer:
            raise
        except Exception as exc:
            _record_failure(
                state,
                source_path=str(local_child),
                target_path=remote_child,
                exc=exc,
            )


def _upload_file(
    client: Any,
    local_path: Path,
    remote_path: str,
    conflict_policy: str,
    state: _TransferState,
) -> None:
    ftp_remote_tree.require_safe_command_path(remote_path, "remote_path")
    parent = posixpath.dirname(remote_path) or "/"
    _ensure_remote_directory(client, parent, state)
    existing = ftp_remote_tree.remote_stat(client, remote_path)
    if existing is not None:
        if existing["type"] == "directory":
            raise FileExistsError(f"remote destination is a directory: {remote_path}")
        if conflict_policy == "skip":
            _record_skip(
                state,
                source_path=str(local_path),
                target_path=remote_path,
                reason="remote_file_exists",
            )
            return
        if conflict_policy == "fail":
            raise FileExistsError(f"remote file exists: {remote_path}")

    byte_count = 0

    def count(chunk: bytes) -> None:
        nonlocal byte_count
        byte_count += len(chunk)

    with local_path.open("rb") as file_obj:
        try:
            client.storbinary(f"STOR {remote_path}", file_obj, callback=count)
        except Exception as exc:
            raise _UploadOutcomeUnknown(exc, attempted_bytes=byte_count) from exc
    state.bytes_transferred += byte_count
    state.completed.append(
        {
            "source_path": str(local_path),
            "target_path": remote_path,
            "bytes": byte_count,
            "type": "file",
        }
    )


def _download(client: Any, request: ActionConnectorRequest, state: _TransferState) -> None:
    payload = request.input_json
    conflict_policy = str(payload["conflict_policy"])
    for item in payload["items"]:
        remote_path = ftp_remote_tree.resolved_remote_path(client, str(item["remote_path"]))
        local_path = Path(str(item["local_path"]))
        try:
            remote = ftp_remote_tree.remote_stat(client, remote_path)
            if remote is None:
                raise FileNotFoundError(f"remote path does not exist: {remote_path}")
            if remote["type"] == "symlink":
                _record_skip(
                    state,
                    source_path=remote_path,
                    target_path=str(local_path),
                    reason="remote_symlink_not_followed",
                )
            elif remote["type"] == "directory":
                _download_directory(
                    client,
                    remote_path,
                    local_path,
                    local_path.resolve(strict=False),
                    conflict_policy,
                    state,
                    ancestry=set(),
                )
            else:
                _download_file(client, remote_path, local_path, conflict_policy, state)
        except _StopTransfer:
            raise
        except Exception as exc:
            _record_failure(
                state,
                source_path=remote_path,
                target_path=str(local_path),
                exc=exc,
            )


def _download_directory(
    client: Any,
    remote_dir: str,
    local_dir: Path,
    destination_root: Path,
    conflict_policy: str,
    state: _TransferState,
    *,
    ancestry: set[str],
) -> None:
    identity = ftp_remote_tree.remote_directory_identity(client, remote_dir)
    if identity in ancestry:
        raise ValidationError(f"remote directory cycle detected at {remote_dir}")
    branch_ancestry = {*ancestry, identity}
    if local_dir.exists() and not local_dir.is_dir():
        raise FileExistsError(f"local destination is not a directory: {local_dir}")
    if not local_dir.exists():
        local_dir.mkdir(parents=True)
        state.created_directories.append({"source_path": remote_dir, "target_path": str(local_dir)})

    for entry in ftp_remote_tree.list_remote(client, remote_dir):
        name = str(entry["name"])
        if not ftp_remote_tree.is_safe_remote_child(name):
            _record_failure(
                state,
                source_path=f"{remote_dir}/{name}",
                target_path=str(local_dir),
                exc=ValidationError(f"unsafe remote child name: {name!r}"),
            )
            continue
        remote_child = posixpath.join(remote_dir, name)
        local_child = local_dir / name
        _assert_within_destination(local_child, destination_root)
        entry_type = str(entry.get("type") or "unknown")
        try:
            if entry_type == "symlink":
                _record_skip(
                    state,
                    source_path=remote_child,
                    target_path=str(local_child),
                    reason="remote_symlink_not_followed",
                )
            elif entry_type == "directory":
                _download_directory(
                    client,
                    remote_child,
                    local_child,
                    destination_root,
                    conflict_policy,
                    state,
                    ancestry=branch_ancestry,
                )
            elif entry_type == "file":
                _download_file(client, remote_child, local_child, conflict_policy, state)
            else:
                _record_skip(
                    state,
                    source_path=remote_child,
                    target_path=str(local_child),
                    reason=f"unsupported_remote_type:{entry_type}",
                )
        except _StopTransfer:
            raise
        except Exception as exc:
            _record_failure(
                state,
                source_path=remote_child,
                target_path=str(local_child),
                exc=exc,
            )


def _download_file(
    client: Any,
    remote_path: str,
    local_path: Path,
    conflict_policy: str,
    state: _TransferState,
) -> None:
    ftp_remote_tree.require_safe_command_path(remote_path, "remote_path")
    if local_path.exists():
        if local_path.is_dir():
            raise FileExistsError(f"local destination is a directory: {local_path}")
        if conflict_policy == "skip":
            _record_skip(
                state,
                source_path=remote_path,
                target_path=str(local_path),
                reason="local_file_exists",
            )
            return
        if conflict_policy == "fail":
            raise FileExistsError(f"local file exists: {local_path}")

    local_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = local_path.with_name(f".{local_path.name}.stackos-ftp-{uuid.uuid4().hex}.part")
    byte_count = 0
    try:
        with temporary.open("xb") as file_obj:

            def write(chunk: bytes) -> None:
                nonlocal byte_count
                file_obj.write(chunk)
                byte_count += len(chunk)

            client.retrbinary(f"RETR {remote_path}", write)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temporary, local_path)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()

    state.bytes_transferred += byte_count
    state.completed.append(
        {
            "source_path": remote_path,
            "target_path": str(local_path),
            "bytes": byte_count,
            "type": "file",
        }
    )


def _ensure_remote_directory(
    client: Any,
    remote_path: str,
    state: _TransferState,
) -> None:
    ftp_remote_tree.require_safe_command_path(remote_path, "remote_path")
    remote_path = posixpath.normpath(remote_path)
    if remote_path in {"", ".", "/"}:
        return
    current = "/" if remote_path.startswith("/") else ""
    for part in (item for item in remote_path.split("/") if item):
        current = posixpath.join(current, part)
        existing = ftp_remote_tree.remote_stat(client, current)
        if existing is None:
            client.mkd(current)
            state.created_directories.append({"source_path": "", "target_path": current})
        elif existing["type"] != "directory":
            raise FileExistsError(f"remote path component is not a directory: {current}")


def _ftp_settings(request: ActionConnectorRequest) -> dict[str, Any]:
    config = credential_config(request)
    payload = credential_payload(request)
    host = _config_text(config, payload, "host", required=True)
    username = _config_text(config, payload, "username", "user", required=True)
    port = _config_int(config, payload, "port", default=21, minimum=1, maximum=65_535)
    tls_mode = _config_text(config, payload, "tls_mode", default="explicit").lower()
    if tls_mode not in _TLS_MODES:
        raise ValidationError("ftp credential tls_mode must be explicit or none")
    timeout_s = _config_int(config, payload, "timeout_s", default=30, minimum=1, maximum=600)
    passive_mode = _config_bool(config, payload, "passive_mode", default=True)
    encoding = _config_text(config, payload, "encoding", default="utf-8")
    try:
        codecs.lookup(encoding)
    except LookupError as exc:
        raise ValidationError("ftp credential encoding is not recognized") from exc
    settings = {
        "host": host,
        "port": port,
        "tls_mode": tls_mode,
        "username": username,
        "password": credential_value(request, "password", "secret"),
        "timeout_s": float(timeout_s),
        "passive_mode": passive_mode,
        "encoding": encoding,
    }
    try:
        validate_ftp_credential_config(settings)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    return settings


def _connect(settings: Mapping[str, Any]) -> Any:
    return open_ftp_client(
        host=str(settings["host"]),
        port=int(settings["port"]),
        tls_mode=str(settings["tls_mode"]),
        username=str(settings["username"]),
        password=str(settings["password"]),
        passive_mode=bool(settings["passive_mode"]),
        timeout_s=float(settings["timeout_s"]),
        encoding=str(settings["encoding"]),
        ftp_module=ftplib,
    )


def _close(client: Any | None) -> None:
    close_ftp_client(client)


def _is_explicit_ftp_failure(exc: ftplib.Error) -> bool:
    return isinstance(exc, (ftplib.error_temp, ftplib.error_perm))


def _mutation_error(
    request: ActionConnectorRequest,
    settings: Mapping[str, Any],
    exc: BaseException,
    *,
    stage: str,
    target_path: str | None = None,
    target_type: str | None = None,
    source_path: str | None = None,
    destination_path: str | None = None,
    outcome_unknown: bool = False,
    reconciliation_guidance: str | None = None,
    partial_result: dict[str, Any] | None = None,
) -> ActionConnectorError:
    secret = str(settings["password"])
    provider_error: dict[str, Any] = {
        "type": type(exc).__name__,
        "message": _safe_error(exc, secret=secret),
        "stage": stage,
    }
    for key, value in (
        ("target_path", target_path),
        ("target_type", target_type),
        ("source_path", source_path),
        ("destination_path", destination_path),
    ):
        if value is not None:
            provider_error[key] = _redact_text(value, secret=secret)
    if outcome_unknown:
        provider_error["outcome_unknown"] = True
        provider_error["retry_safe"] = False
    if reconciliation_guidance is not None:
        provider_error["reconciliation_guidance"] = reconciliation_guidance
    output_json: dict[str, Any] | None = None
    if partial_result is not None:
        provider_error["partial_result"] = partial_result
        output_json = {**partial_result, "provider_error": provider_error}
    return ActionConnectorError(
        "FTP remote mutation failed",
        provider_error=provider_error,
        output_json=output_json,
        metadata_json=_metadata(request, settings),
    )


def _record_skip(
    state: _TransferState,
    *,
    source_path: str,
    target_path: str,
    reason: str,
) -> None:
    state.skipped.append({"source_path": source_path, "target_path": target_path, "reason": reason})


def _record_failure(
    state: _TransferState,
    *,
    source_path: str,
    target_path: str,
    exc: Exception,
) -> None:
    root_cause = exc.cause if isinstance(exc, _UploadOutcomeUnknown) else exc
    message = _safe_error(root_cause, secret=state.secret)
    failure: dict[str, Any] = {
        "source_path": source_path,
        "target_path": target_path,
        "error_type": type(root_cause).__name__,
        "message": message,
    }
    if isinstance(exc, _UploadOutcomeUnknown):
        failure.update(
            {
                "outcome_unknown": True,
                "remote_partial_possible": True,
                "retry_safe": False,
                "attempted_bytes": exc.attempted_bytes,
                "reconciliation_guidance": (
                    "Inspect the selected remote path before deciding whether to retry; "
                    "the server may have retained or truncated a partial STOR result."
                ),
            }
        )
    state.failed.append(failure)
    if state.error_policy == "stop":
        raise _StopTransfer(message) from exc


def _failure_output(state: _TransferState, message: str) -> dict[str, Any]:
    output = state.output(status="failed")
    output["provider_error"] = {
        "type": "transfer_failure",
        "message": message,
        "partial_result": dict(output),
    }
    return output


def _assert_within_destination(path: Path, root: Path) -> None:
    resolved = path.resolve(strict=False)
    try:
        common = os.path.commonpath([str(resolved), str(root)])
    except ValueError as exc:
        raise ValidationError("download path escapes the selected destination root") from exc
    if common != str(root):
        raise ValidationError("download path escapes the selected destination root")


def _validate_transfer_options(
    payload: Mapping[str, Any],
    issues: list[ActionValidationIssue],
) -> None:
    conflict_policy = payload.get("conflict_policy")
    if conflict_policy not in _CONFLICT_POLICIES:
        issues.append(
            issue(
                "$.conflict_policy",
                "conflict_policy must be overwrite, skip, or fail",
                "enum",
            )
        )
    error_policy = payload.get("error_policy")
    if error_policy not in _ERROR_POLICIES:
        issues.append(issue("$.error_policy", "error_policy must be stop or continue", "enum"))
    if "follow_symlinks" in payload and not isinstance(payload["follow_symlinks"], bool):
        issues.append(issue("$.follow_symlinks", "follow_symlinks must be a boolean", "type_error"))


def _validate_remote_path(
    value: Any,
    path: str,
    issues: list[ActionValidationIssue],
) -> None:
    if not isinstance(value, str) or not value:
        issues.append(issue(path, "remote_path must be a non-empty string", "required"))
    elif any(char in value for char in ("\x00", "\r", "\n")):
        issues.append(issue(path, "remote_path cannot contain NUL, CR, or LF", "format"))


def _validate_local_path(
    value: Any,
    path: str,
    issues: list[ActionValidationIssue],
) -> None:
    if not isinstance(value, str) or not value:
        issues.append(issue(path, "local_path must be a non-empty string", "required"))
    elif "\x00" in value:
        issues.append(issue(path, "local_path cannot contain NUL", "format"))


def _config_text(
    config: Mapping[str, Any],
    payload: Mapping[str, Any],
    *keys: str,
    default: str | None = None,
    required: bool = False,
) -> str:
    for source in (config, payload):
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if value is not None and not isinstance(value, (dict, list)):
                text = str(value).strip()
                if text:
                    return text
    if required:
        raise ValidationError(f"ftp credential missing {keys[0]}")
    return default or ""


def _config_int(
    config: Mapping[str, Any],
    payload: Mapping[str, Any],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = config.get(key, payload.get(key, default))
    if isinstance(raw, bool):
        raise ValidationError(f"ftp credential {key} must be an integer")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"ftp credential {key} must be an integer") from exc
    if value < minimum or value > maximum:
        raise ValidationError(f"ftp credential {key} must be between {minimum} and {maximum}")
    return value


def _config_bool(
    config: Mapping[str, Any],
    payload: Mapping[str, Any],
    key: str,
    *,
    default: bool,
) -> bool:
    value = config.get(key, payload.get(key, default))
    if not isinstance(value, bool):
        raise ValidationError(f"ftp credential {key} must be a boolean")
    return value


def _metadata(
    request: ActionConnectorRequest,
    settings: Mapping[str, Any],
) -> dict[str, Any]:
    secret = str(settings["password"])
    return {
        "vendor": "ftp",
        "operation": request.operation,
        "host": _redact_text(str(settings["host"]), secret=secret),
        "port": settings["port"],
        "tls_mode": settings["tls_mode"],
        "passive_mode": settings["passive_mode"],
    }


def _safe_error(exc: BaseException, *, secret: str = "") -> str:
    return _redact_text(str(exc), secret=secret)[:500]


def _redact_text(value: str, *, secret: str) -> str:
    message = value.replace("\r", " ").replace("\n", " ")
    return message.replace(secret, "[REDACTED]") if secret else message


def _redact_payload(value: Any, *, secret: str) -> Any:
    if isinstance(value, str):
        return _redact_text(value, secret=secret)
    if isinstance(value, list):
        return [_redact_payload(item, secret=secret) for item in value]
    if isinstance(value, dict):
        return {key: _redact_payload(item, secret=secret) for key, item in value.items()}
    return value


def _contains_secret(value: Any, *, secret: str) -> bool:
    if not secret:
        return False
    if isinstance(value, str):
        return secret in value
    if isinstance(value, list):
        return any(_contains_secret(item, secret=secret) for item in value)
    if isinstance(value, dict):
        return any(_contains_secret(item, secret=secret) for item in value.values())
    return False


__all__ = ["FtpActionConnector"]
