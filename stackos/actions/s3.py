"""Amazon S3 action connector.

Amazon S3 has a flat key namespace. This adapter preserves provider-native
object and prefix semantics while exposing the same capability categories as
the built-in FTP connector where S3 can support them truthfully.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from botocore.exceptions import BotoCoreError, ClientError

from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionProgressCallback,
    ActionValidationIssue,
)
from stackos.actions.provider_utils import credential_config, issue, unknown_operation
from stackos.integrations.s3 import (
    create_s3_client,
    normalize_s3_prefix,
    parse_s3_credentials,
    validate_s3_credential_config,
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
_DOWNLOAD_OBJECT_LIMIT = 10_000
_UPLOAD_OBJECT_LIMIT = 10_000
_IO_CHUNK_SIZE = 1024 * 1024
_MULTIPART_THRESHOLD = 16 * 1024 * 1024
_MULTIPART_PART_SIZE = 8 * 1024 * 1024
_SINGLE_COPY_MAX_BYTES = 5 * 1000 * 1000 * 1000
_COPY_PART_SIZE = 64 * 1024 * 1024
_MAX_MULTIPART_PARTS = 10_000
_MAX_MULTIPART_PART_SIZE = 5 * 1024 * 1024 * 1024
_MAX_OBJECT_SIZE = _MAX_MULTIPART_PARTS * _MAX_MULTIPART_PART_SIZE
_PART_SIZE_ALIGNMENT = 1024 * 1024


@dataclass(frozen=True)
class _S3Settings:
    bucket: str
    region: str
    prefix: str
    payload: bytes = field(repr=False)
    secret_values: tuple[str, ...] = field(repr=False)


class _ScopedS3Client:
    """Enforce one Account bucket/prefix boundary around allowed S3 calls."""

    _ALLOWED_METHODS = frozenset(
        {
            "abort_multipart_upload",
            "complete_multipart_upload",
            "copy_object",
            "create_multipart_upload",
            "delete_object",
            "delete_objects",
            "get_object",
            "head_object",
            "list_objects_v2",
            "put_object",
            "upload_part",
            "upload_part_copy",
        }
    )

    def __init__(self, client: Any, *, bucket: str, prefix: str) -> None:
        self._client = client
        self._bucket = bucket
        self._prefix = normalize_s3_prefix(prefix)

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._client, name)
        if not callable(target):
            return target
        if name not in self._ALLOWED_METHODS:
            raise AttributeError(f"Amazon S3 method {name!r} is not allowed by the scoped client")

        def call(**kwargs: Any) -> Any:
            params = self._translate_request(kwargs)
            response = target(**params)
            return self._translate_response(name, response)

        return call

    def _translate_request(self, kwargs: Mapping[str, Any]) -> dict[str, Any]:
        params = dict(kwargs)
        if params.get("Bucket") != self._bucket:
            raise ValidationError("Amazon S3 request must use the Account's bound bucket")
        if "Key" in params:
            params["Key"] = self._provider_key(params["Key"])
        if "Prefix" in params:
            params["Prefix"] = self._provider_key(params["Prefix"])
        if "CopySource" in params:
            source = params["CopySource"]
            if not isinstance(source, Mapping) or source.get("Bucket") != self._bucket:
                raise ValidationError("Amazon S3 copy source must use the Account's bound bucket")
            copied_source = dict(source)
            copied_source["Key"] = self._provider_key(source.get("Key"))
            params["CopySource"] = copied_source
        if "Delete" in params:
            delete = params["Delete"]
            if not isinstance(delete, Mapping):
                raise ValidationError("Amazon S3 delete batch must be an object")
            objects = delete.get("Objects")
            if not isinstance(objects, list):
                raise ValidationError("Amazon S3 delete batch must include object keys")
            translated_objects: list[dict[str, Any]] = []
            for item in objects:
                if not isinstance(item, Mapping):
                    raise ValidationError("Amazon S3 delete batch object must be an object")
                translated = dict(item)
                translated["Key"] = self._provider_key(item.get("Key"))
                translated_objects.append(translated)
            translated_delete = dict(delete)
            translated_delete["Objects"] = translated_objects
            params["Delete"] = translated_delete
        return params

    def _translate_response(self, method: str, response: Any) -> Any:
        if not isinstance(response, Mapping):
            return response
        translated = dict(response)
        if method == "list_objects_v2":
            url_encoded = response.get("EncodingType") == "url"
            translated["Contents"] = self._translate_key_entries(
                response.get("Contents"),
                field_name="Key",
                url_encoded=url_encoded,
            )
            translated["CommonPrefixes"] = self._translate_key_entries(
                response.get("CommonPrefixes"),
                field_name="Prefix",
                url_encoded=url_encoded,
            )
            for field_name in ("Prefix", "StartAfter"):
                if isinstance(response.get(field_name), str):
                    translated[field_name] = self._logical_key(
                        response[field_name],
                        url_encoded=url_encoded,
                    )
        elif method == "delete_objects":
            translated["Deleted"] = self._translate_key_entries(
                response.get("Deleted"),
                field_name="Key",
                url_encoded=False,
            )
            translated["Errors"] = self._translate_key_entries(
                response.get("Errors"),
                field_name="Key",
                url_encoded=False,
            )
        return translated

    def _translate_key_entries(
        self,
        value: Any,
        *,
        field_name: str,
        url_encoded: bool,
    ) -> list[Any]:
        if not isinstance(value, list):
            return []
        translated: list[Any] = []
        for item in value:
            if not isinstance(item, Mapping) or not isinstance(item.get(field_name), str):
                translated.append(item)
                continue
            entry = dict(item)
            entry[field_name] = self._logical_key(
                item[field_name],
                url_encoded=url_encoded,
            )
            translated.append(entry)
        return translated

    def _provider_key(self, value: Any) -> str:
        if not isinstance(value, str):
            raise ValidationError("Amazon S3 key or prefix must be a string")
        provider_key = f"{self._prefix}{value}"
        if len(provider_key.encode("utf-8")) > 1024:
            raise ValidationError(
                "Amazon S3 configured prefix plus key must be at most 1024 UTF-8 bytes"
            )
        return provider_key

    def _logical_key(self, value: str, *, url_encoded: bool) -> str:
        decoded = _decode_listing_text(value, url_encoded=url_encoded)
        if self._prefix and not decoded.startswith(self._prefix):
            raise ValidationError("Amazon S3 returned an object outside the configured prefix")
        logical = decoded[len(self._prefix) :] if self._prefix else decoded
        return quote(logical, safe="/") if url_encoded else logical


@dataclass
class _TransferState:
    operation: str
    callback: ActionProgressCallback | None
    secrets: tuple[str, ...] = field(repr=False)
    completed: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    bytes_transferred: int = 0
    request_count: int = 0

    def progress(
        self,
        *,
        phase: str,
        current_source_path: str | None = None,
        current_target_path: str | None = None,
    ) -> None:
        if self.callback is None:
            return
        self.callback(
            {
                "phase": phase,
                "operation": self.operation,
                "items_completed": len(self.completed),
                "items_skipped": len(self.skipped),
                "items_failed": len(self.failed),
                "bytes_transferred": self.bytes_transferred,
                "request_count": self.request_count,
                "current_source_path": _redact_text(current_source_path, self.secrets),
                "current_target_path": _redact_text(current_target_path, self.secrets),
            }
        )

    def output(self) -> dict[str, Any]:
        if self.failed and (self.completed or self.skipped):
            status = "partial"
        elif self.failed:
            status = "failed"
        else:
            status = "success"
        receipt_items = [*self.completed, *self.skipped, *self.failed]
        outcome_unknown = any(item.get("outcome_unknown") is True for item in receipt_items)
        cleanup_unverified = any(
            item.get("cleanup_unverified") is True
            or (
                isinstance(item.get("abort"), Mapping)
                and item["abort"].get("cleanup_unverified") is True
            )
            for item in receipt_items
        )
        return _redact_payload(
            {
                "provider": "aws-s3",
                "operation": self.operation,
                "status": status,
                "completed_count": len(self.completed),
                "skipped_count": len(self.skipped),
                "failed_count": len(self.failed),
                "bytes_transferred": self.bytes_transferred,
                "request_count": self.request_count,
                "completed": self.completed,
                "skipped": self.skipped,
                "failed": self.failed,
                **_provider_failure_fields(self.failed),
                **({"outcome_unknown": True} if outcome_unknown else {}),
                **({"cleanup_unverified": True} if cleanup_unverified else {}),
            },
            self.secrets,
        )


@dataclass(frozen=True)
class _UploadCandidate:
    local_path: Path
    destination_key: str
    kind: str
    size: int


class _UploadSkipped(Exception):
    def __init__(self, entry: dict[str, Any]) -> None:
        super().__init__("Amazon S3 upload destination already exists")
        self.entry = entry


class _UploadItemFailure(Exception):
    def __init__(self, failure: dict[str, Any]) -> None:
        super().__init__("Amazon S3 upload item failed")
        self.failure = failure


class _UploadTraversalStopped(Exception):
    pass


class _UploadObjectLimitExceeded(ValidationError):
    pass


class S3ActionConnector:
    """Decision-free Amazon S3 object and prefix adapter."""

    key = "aws-s3"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        if request.operation not in _OPERATIONS:
            return unknown_operation(request)
        payload = request.input_json
        issues: list[ActionValidationIssue] = []

        if request.operation == "directory.list":
            _validate_key(
                payload.get("prefix", ""),
                "$.prefix",
                issues,
                allow_empty=True,
                allow_exact_soap=True,
            )
            delimiter = payload.get("delimiter", "/")
            if not isinstance(delimiter, str) or not delimiter:
                issues.append(
                    issue("$.delimiter", "delimiter must be a non-empty string", "type_error")
                )
            else:
                _validate_no_control(delimiter, "$.delimiter", issues)
            _validate_int(
                payload.get("page_size", 1000),
                "$.page_size",
                issues,
                minimum=1,
                maximum=1000,
            )
            cursor = payload.get("cursor")
            if cursor is not None:
                if not isinstance(cursor, str) or not cursor:
                    issues.append(
                        issue("$.cursor", "cursor must be a non-empty string", "type_error")
                    )
                else:
                    _validate_no_control(cursor, "$.cursor", issues)
            return issues

        if request.operation == "file.delete":
            _validate_key(payload.get("key"), "$.key", issues)
            if_match = payload.get("if_match")
            if if_match is not None and (not isinstance(if_match, str) or not if_match):
                issues.append(
                    issue("$.if_match", "if_match must be a non-empty string", "type_error")
                )
            return issues

        if request.operation == "directory.create":
            prefix = payload.get("prefix")
            normalized = _directory_prefix(prefix) if isinstance(prefix, str) and prefix else prefix
            _validate_key(normalized, "$.prefix", issues)
            return issues

        if request.operation == "directory.delete":
            prefix = payload.get("prefix")
            normalized = _directory_prefix(prefix) if isinstance(prefix, str) and prefix else prefix
            _validate_key(normalized, "$.prefix", issues)
            if not isinstance(payload.get("recursive"), bool):
                issues.append(issue("$.recursive", "recursive must be a boolean", "type_error"))
            _validate_int(
                payload.get("max_objects"),
                "$.max_objects",
                issues,
                minimum=1,
                maximum=10_000,
            )
            return issues

        if request.operation == "path.rename":
            source = payload.get("source_key")
            destination = payload.get("destination_key")
            _validate_key(source, "$.source_key", issues)
            _validate_key(destination, "$.destination_key", issues)
            if isinstance(source, str) and source.endswith("/"):
                issues.append(
                    issue(
                        "$.source_key",
                        "source_key must identify one object, not a prefix marker",
                        "unsupported",
                    )
                )
            if isinstance(destination, str) and destination.endswith("/"):
                issues.append(
                    issue(
                        "$.destination_key",
                        "destination_key must identify one object, not a prefix marker",
                        "unsupported",
                    )
                )
            if source == destination and isinstance(source, str):
                issues.append(
                    issue(
                        "$.destination_key",
                        "destination_key must differ from source_key",
                        "conflict",
                    )
                )
            _validate_policy(
                payload.get("conflict_policy"),
                "$.conflict_policy",
                _CONFLICT_POLICIES,
                issues,
            )
            return issues

        _validate_policy(
            payload.get("conflict_policy"),
            "$.conflict_policy",
            _CONFLICT_POLICIES,
            issues,
        )
        _validate_policy(
            payload.get("error_policy"),
            "$.error_policy",
            _ERROR_POLICIES,
            issues,
        )
        if request.operation == "file.upload" and not isinstance(
            payload.get("follow_symlinks", False),
            bool,
        ):
            issues.append(
                issue("$.follow_symlinks", "follow_symlinks must be a boolean", "type_error")
            )

        items = payload.get("items")
        if not isinstance(items, list) or not items:
            issues.append(issue("$.items", "items must be a non-empty array", "required"))
            return issues
        for index, item in enumerate(items):
            path = f"$.items[{index}]"
            if not isinstance(item, Mapping):
                issues.append(issue(path, "item must be an object", "type_error"))
                continue
            _validate_local_path(item.get("local_path"), f"{path}.local_path", issues)
            if request.operation == "file.upload":
                _validate_key(
                    item.get("destination_key"),
                    f"{path}.destination_key",
                    issues,
                    allow_exact_soap=True,
                )
            else:
                remote_kind = item.get("remote_kind")
                remote_key = item.get("remote_key")
                normalized = (
                    _directory_prefix(remote_key)
                    if remote_kind == "prefix" and isinstance(remote_key, str) and remote_key
                    else remote_key
                )
                _validate_key(normalized, f"{path}.remote_key", issues)
                if remote_kind not in {"object", "prefix"}:
                    issues.append(
                        issue(
                            f"{path}.remote_kind",
                            "remote_kind must be object or prefix",
                            "enum",
                        )
                    )
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.operation not in _OPERATIONS:
            raise ValidationError(f"unsupported Amazon S3 operation {request.operation!r}")
        return await asyncio.to_thread(_execute_sync, request)


def _execute_sync(request: ActionConnectorRequest) -> ActionConnectorResult:
    settings = _settings(request)
    if request.operation == "directory.list":
        return _list_directory(request, settings)
    if request.operation == "file.upload":
        return _upload(request, settings)
    if request.operation == "file.download":
        return _download(request, settings)
    if request.operation == "file.delete":
        return _delete_file(request, settings)
    if request.operation == "directory.create":
        return _create_directory_marker(request, settings)
    if request.operation == "directory.delete":
        return _delete_directory(request, settings)
    if request.operation == "path.rename":
        return _move_object(request, settings)
    raise ValidationError(
        f"Amazon S3 operation {request.operation!r} is not implemented by this delivery slice"
    )


def _list_directory(
    request: ActionConnectorRequest,
    settings: _S3Settings,
) -> ActionConnectorResult:
    client = _read_client(settings)
    payload = request.input_json
    params: dict[str, Any] = {
        "Bucket": settings.bucket,
        "Prefix": str(payload.get("prefix", "")),
        "MaxKeys": int(payload.get("page_size", 1000)),
        "EncodingType": "url",
    }
    delimiter = payload.get("delimiter", "/")
    if delimiter is not None:
        params["Delimiter"] = str(delimiter)
    if payload.get("cursor"):
        params["ContinuationToken"] = str(payload["cursor"])
    try:
        response = client.list_objects_v2(**params)
    except (ClientError, BotoCoreError) as exc:
        raise _connector_error(
            exc,
            operation="directory.list",
            bucket=settings.bucket,
            retry_safe=True,
        ) from exc

    url_encoded = response.get("EncodingType") == "url"
    objects = [
        _object_entry(
            item,
            settings.secret_values,
            url_encoded=url_encoded,
        )
        for item in response.get("Contents") or []
    ]
    common_prefixes = []
    for item in response.get("CommonPrefixes") or []:
        if not isinstance(item, Mapping) or item.get("Prefix") is None:
            continue
        raw_prefix = _decode_listing_text(
            str(item["Prefix"]),
            url_encoded=url_encoded,
        )
        safe_prefix = _redact_text(raw_prefix, settings.secret_values)
        problem = _key_problem(
            raw_prefix,
            allow_empty=False,
            allow_exact_soap=True,
        )
        prefix_entry = {
            "prefix": safe_prefix,
            "prefix_redacted": safe_prefix != raw_prefix,
            "safe_to_use": safe_prefix == raw_prefix and problem is None,
        }
        if problem is not None:
            prefix_entry["unsafe_reason"] = problem[1]
        common_prefixes.append(prefix_entry)
    metadata = _response_metadata(response)
    output = {
        "provider": "aws-s3",
        "operation": "directory.list",
        "status": "success",
        "bucket": settings.bucket,
        "prefix": params["Prefix"],
        "delimiter": params.get("Delimiter"),
        "object_count": len(objects),
        "common_prefix_count": len(common_prefixes),
        "key_count": int(response.get("KeyCount") or 0),
        "is_truncated": bool(response.get("IsTruncated")),
        "next_cursor": (
            str(response["NextContinuationToken"])
            if response.get("NextContinuationToken")
            else None
        ),
        "objects": objects,
        "common_prefixes": common_prefixes,
        **metadata,
    }
    return ActionConnectorResult(
        output_json=_redact_payload(output, settings.secret_values),
        metadata_json={
            "provider": "aws-s3",
            "operation": "directory.list",
            "bucket": settings.bucket,
            "request_count": 1,
            **metadata,
        },
    )


def _upload(
    request: ActionConnectorRequest,
    settings: _S3Settings,
) -> ActionConnectorResult:
    client = _mutation_client(settings)
    conflict_policy = str(request.input_json["conflict_policy"])
    error_policy = str(request.input_json["error_policy"])
    follow_symlinks = bool(request.input_json.get("follow_symlinks", False))
    state = _TransferState(
        operation="file.upload",
        callback=request.progress_callback,
        secrets=settings.secret_values,
    )
    state.progress(phase="preparing")

    try:
        candidates = _collect_upload_candidates(
            items=request.input_json["items"],
            follow_symlinks=follow_symlinks,
            error_policy=error_policy,
            state=state,
        )
    except _UploadTraversalStopped as exc:
        raise _transfer_error("Amazon S3 upload traversal failed", state) from exc
    except Exception as exc:
        state.failed.append(
            _transfer_failure(
                exc,
                source="local upload traversal",
                target=settings.bucket,
                retry_safe=True,
            )
        )
        raise _transfer_error("Amazon S3 upload traversal failed", state) from exc

    for candidate in candidates:
        try:
            entry = _upload_candidate(
                client=client,
                settings=settings,
                candidate=candidate,
                conflict_policy=conflict_policy,
                state=state,
            )
            state.completed.append(entry)
            state.progress(
                phase="transferred",
                current_source_path=str(candidate.local_path),
                current_target_path=candidate.destination_key,
            )
        except _UploadSkipped as exc:
            state.skipped.append(exc.entry)
            state.progress(
                phase="skipped",
                current_source_path=str(candidate.local_path),
                current_target_path=candidate.destination_key,
            )
        except Exception as exc:
            failure = (
                exc.failure
                if isinstance(exc, _UploadItemFailure)
                else _transfer_failure(
                    exc,
                    source=str(candidate.local_path),
                    target=candidate.destination_key,
                    retry_safe=not isinstance(exc, BotoCoreError),
                )
            )
            state.failed.append(failure)
            state.progress(
                phase="failed",
                current_source_path=str(candidate.local_path),
                current_target_path=candidate.destination_key,
            )
            if error_policy == "stop":
                raise _transfer_error(
                    "Amazon S3 upload stopped after a failed object",
                    state,
                ) from exc

    output = state.output()
    state.progress(phase="complete")
    if state.failed and not state.completed and not state.skipped:
        raise _transfer_error("Amazon S3 upload failed", state)
    return ActionConnectorResult(
        output_json=output,
        metadata_json={
            "provider": "aws-s3",
            "operation": "file.upload",
            "bucket": settings.bucket,
            "request_count": state.request_count,
            "bytes_transferred": state.bytes_transferred,
        },
    )


def _collect_upload_candidates(
    *,
    items: list[dict[str, Any]],
    follow_symlinks: bool,
    error_policy: str,
    state: _TransferState,
) -> list[_UploadCandidate]:
    candidates: list[_UploadCandidate] = []
    for item in items:
        local_path = Path(str(item["local_path"])).expanduser()
        destination_key = str(item["destination_key"])
        item_candidates: list[_UploadCandidate] = []
        item_skipped: list[dict[str, Any]] = []
        try:
            if local_path.is_symlink() and not follow_symlinks:
                item_skipped.append(
                    {
                        "local_path": str(local_path),
                        "destination_key": destination_key,
                        "reason_code": "symlink_skipped",
                    }
                )
            elif local_path.is_file():
                item_candidates.append(
                    _UploadCandidate(
                        local_path=local_path,
                        destination_key=destination_key,
                        kind="file",
                        size=local_path.stat().st_size,
                    )
                )
            elif local_path.is_dir():
                _walk_upload_directory(
                    directory=local_path,
                    destination_prefix=_directory_prefix(destination_key),
                    relative_parts=(),
                    follow_symlinks=follow_symlinks,
                    ancestry=set(),
                    candidates=item_candidates,
                    skipped=item_skipped,
                )
            elif not local_path.exists() and not local_path.is_symlink():
                raise FileNotFoundError(f"local upload path does not exist: {local_path}")
            else:
                raise ValidationError(
                    f"local upload path must be a regular file or directory: {local_path}"
                )
            for candidate in item_candidates:
                _require_valid_key(
                    candidate.destination_key,
                    context="derived Amazon S3 upload destination",
                )
        except _UploadObjectLimitExceeded:
            raise
        except Exception as exc:
            state.failed.append(
                _transfer_failure(
                    exc,
                    source=str(local_path),
                    target=destination_key,
                    retry_safe=True,
                )
            )
            state.progress(
                phase="failed",
                current_source_path=str(local_path),
                current_target_path=destination_key,
            )
            if error_policy == "stop":
                raise _UploadTraversalStopped from exc
            continue
        if len(candidates) + len(item_candidates) > _UPLOAD_OBJECT_LIMIT:
            raise _UploadObjectLimitExceeded(
                f"Amazon S3 upload exceeds the {_UPLOAD_OBJECT_LIMIT} object bound"
            )
        candidates.extend(item_candidates)
        state.skipped.extend(item_skipped)
    return candidates


def _walk_upload_directory(
    *,
    directory: Path,
    destination_prefix: str,
    relative_parts: tuple[str, ...],
    follow_symlinks: bool,
    ancestry: set[tuple[int, int]],
    candidates: list[_UploadCandidate],
    skipped: list[dict[str, Any]],
) -> None:
    stat_result = directory.stat(follow_symlinks=follow_symlinks)
    identity = (stat_result.st_dev, stat_result.st_ino)
    if identity in ancestry:
        raise ValidationError(f"local upload directory cycle detected: {directory}")
    next_ancestry = {*ancestry, identity}
    entries = sorted(directory.iterdir(), key=lambda value: value.name)
    traversable = 0

    for child in entries:
        child_relative = (*relative_parts, child.name)
        child_key = destination_prefix + "/".join(child_relative)
        if child.is_symlink() and not follow_symlinks:
            skipped.append(
                {
                    "local_path": str(child),
                    "destination_key": child_key,
                    "reason_code": "symlink_skipped",
                }
            )
            continue
        if child.is_file():
            candidates.append(
                _UploadCandidate(
                    local_path=child,
                    destination_key=child_key,
                    kind="file",
                    size=child.stat().st_size,
                )
            )
            traversable += 1
        elif child.is_dir():
            before = len(candidates)
            _walk_upload_directory(
                directory=child,
                destination_prefix=destination_prefix,
                relative_parts=child_relative,
                follow_symlinks=follow_symlinks,
                ancestry=next_ancestry,
                candidates=candidates,
                skipped=skipped,
            )
            traversable += max(1, len(candidates) - before)
        else:
            skipped.append(
                {
                    "local_path": str(child),
                    "destination_key": child_key,
                    "reason_code": "unsupported_local_type",
                }
            )
        if len(candidates) > _UPLOAD_OBJECT_LIMIT:
            raise _UploadObjectLimitExceeded(
                f"Amazon S3 upload exceeds the {_UPLOAD_OBJECT_LIMIT} object bound"
            )

    if traversable == 0:
        marker_key = destination_prefix + "/".join(relative_parts)
        marker_key = _directory_prefix(marker_key)
        candidates.append(
            _UploadCandidate(
                local_path=directory,
                destination_key=marker_key,
                kind="directory_marker",
                size=0,
            )
        )


def _upload_candidate(
    *,
    client: Any,
    settings: _S3Settings,
    candidate: _UploadCandidate,
    conflict_policy: str,
    state: _TransferState,
) -> dict[str, Any]:
    if candidate.kind == "directory_marker" or candidate.size < _MULTIPART_THRESHOLD:
        return _put_object(
            client=client,
            settings=settings,
            candidate=candidate,
            conflict_policy=conflict_policy,
            state=state,
        )
    return _multipart_upload(
        client=client,
        settings=settings,
        candidate=candidate,
        conflict_policy=conflict_policy,
        state=state,
    )


def _put_object(
    *,
    client: Any,
    settings: _S3Settings,
    candidate: _UploadCandidate,
    conflict_policy: str,
    state: _TransferState,
) -> dict[str, Any]:
    data = b"" if candidate.kind == "directory_marker" else candidate.local_path.read_bytes()
    params: dict[str, Any] = {
        "Bucket": settings.bucket,
        "Key": candidate.destination_key,
        "Body": data,
    }
    if conflict_policy in {"skip", "fail"}:
        params["IfNoneMatch"] = "*"
    state.request_count += 1
    try:
        response = client.put_object(**params)
    except ClientError as exc:
        if _is_precondition_failure(exc) and conflict_policy == "skip":
            raise _UploadSkipped(
                {
                    "local_path": str(candidate.local_path),
                    "destination_key": candidate.destination_key,
                    "reason_code": "destination_exists",
                }
            ) from exc
        raise
    state.bytes_transferred += len(data)
    metadata = _response_metadata(response)
    return {
        "local_path": str(candidate.local_path),
        "destination_key": candidate.destination_key,
        "type": candidate.kind,
        "bytes": len(data),
        "multipart": False,
        "part_count": 1,
        "etag": _optional_text(response.get("ETag")),
        "version_id": _optional_text(response.get("VersionId")),
        "checksum_crc32": _optional_text(response.get("ChecksumCRC32")),
        "checksum_crc32c": _optional_text(response.get("ChecksumCRC32C")),
        "checksum_sha1": _optional_text(response.get("ChecksumSHA1")),
        "checksum_sha256": _optional_text(response.get("ChecksumSHA256")),
        **metadata,
    }


def _multipart_upload(
    *,
    client: Any,
    settings: _S3Settings,
    candidate: _UploadCandidate,
    conflict_policy: str,
    state: _TransferState,
) -> dict[str, Any]:
    try:
        part_size = _multipart_part_size(
            candidate.size,
            minimum=_MULTIPART_PART_SIZE,
        )
    except ValidationError as exc:
        failure = _transfer_failure(
            exc,
            source=str(candidate.local_path),
            target=candidate.destination_key,
            retry_safe=True,
        )
        failure.update(
            {
                "reason_code": "object_size_exceeded",
                "multipart": True,
                "multipart_upload_id": None,
                "uploaded_part_count": 0,
                "completion_started": False,
                "outcome_unknown": False,
                "reconciliation": "No multipart upload was created.",
            }
        )
        raise _UploadItemFailure(failure) from exc

    upload_id: str | None = None
    completion_started = False
    parts: list[dict[str, Any]] = []
    try:
        state.request_count += 1
        created = client.create_multipart_upload(
            Bucket=settings.bucket,
            Key=candidate.destination_key,
        )
        upload_id_value = created.get("UploadId")
        if not isinstance(upload_id_value, str) or not upload_id_value:
            raise ValidationError("Amazon S3 did not return a multipart upload id")
        upload_id = upload_id_value

        with candidate.local_path.open("rb") as source:
            part_number = 1
            while True:
                chunk = source.read(part_size)
                if not chunk:
                    break
                if part_number > _MAX_MULTIPART_PARTS:
                    raise ValidationError("Amazon S3 multipart upload exceeds 10,000 parts")
                state.request_count += 1
                response = client.upload_part(
                    Bucket=settings.bucket,
                    Key=candidate.destination_key,
                    PartNumber=part_number,
                    UploadId=upload_id,
                    Body=chunk,
                )
                etag = response.get("ETag")
                if not isinstance(etag, str) or not etag:
                    raise ValidationError("Amazon S3 upload part did not return an ETag")
                parts.append({"ETag": etag, "PartNumber": part_number})
                state.bytes_transferred += len(chunk)
                state.progress(
                    phase="transferring",
                    current_source_path=str(candidate.local_path),
                    current_target_path=candidate.destination_key,
                )
                part_number += 1

        complete_params: dict[str, Any] = {
            "Bucket": settings.bucket,
            "Key": candidate.destination_key,
            "UploadId": upload_id,
            "MultipartUpload": {"Parts": parts},
        }
        if conflict_policy in {"skip", "fail"}:
            complete_params["IfNoneMatch"] = "*"
        state.request_count += 1
        completion_started = True
        try:
            completed = client.complete_multipart_upload(**complete_params)
        except ClientError as exc:
            if _is_precondition_failure(exc) and conflict_policy == "skip":
                abort = _abort_upload(
                    client=client,
                    settings=settings,
                    key=candidate.destination_key,
                    upload_id=upload_id,
                    state=state,
                )
                raise _UploadSkipped(
                    {
                        "local_path": str(candidate.local_path),
                        "destination_key": candidate.destination_key,
                        "reason_code": "destination_exists",
                        "multipart_upload_id": upload_id,
                        "abort": abort,
                    }
                ) from exc
            raise
        metadata = _response_metadata(completed)
        return {
            "local_path": str(candidate.local_path),
            "destination_key": candidate.destination_key,
            "type": candidate.kind,
            "bytes": candidate.size,
            "multipart": True,
            "part_count": len(parts),
            "etag": _optional_text(completed.get("ETag")),
            "version_id": _optional_text(completed.get("VersionId")),
            "checksum_crc32": _optional_text(completed.get("ChecksumCRC32")),
            "checksum_crc32c": _optional_text(completed.get("ChecksumCRC32C")),
            "checksum_sha1": _optional_text(completed.get("ChecksumSHA1")),
            "checksum_sha256": _optional_text(completed.get("ChecksumSHA256")),
            **metadata,
        }
    except _UploadSkipped:
        raise
    except Exception as exc:
        transport_uncertain = isinstance(exc, BotoCoreError)
        completion_ambiguous = completion_started and transport_uncertain
        failure = _transfer_failure(
            exc,
            source=str(candidate.local_path),
            target=candidate.destination_key,
            retry_safe=False,
        )
        failure.update(
            {
                "multipart": True,
                "multipart_upload_id": upload_id,
                "uploaded_part_count": len(parts),
                "completion_started": completion_started,
                "outcome_unknown": transport_uncertain,
                "cleanup_unverified": upload_id is not None,
                "reconciliation": (
                    "Inspect the destination object and multipart upload state before retrying."
                    if completion_ambiguous
                    else (
                        "The connector attempted one abort request. Confirm that no parts "
                        "remain with ListParts or bucket lifecycle cleanup before treating "
                        "multipart storage cleanup as complete."
                    )
                ),
            }
        )
        if upload_id and not completion_ambiguous:
            failure["abort"] = _abort_upload(
                client=client,
                settings=settings,
                key=candidate.destination_key,
                upload_id=upload_id,
                state=state,
            )
        raise _UploadItemFailure(failure) from exc


def _abort_upload(
    *,
    client: Any,
    settings: _S3Settings,
    key: str,
    upload_id: str,
    state: _TransferState,
) -> dict[str, Any]:
    state.request_count += 1
    try:
        response = client.abort_multipart_upload(
            Bucket=settings.bucket,
            Key=key,
            UploadId=upload_id,
        )
    except Exception as exc:
        failure = _transfer_failure(
            exc,
            source=key,
            target=upload_id,
            retry_safe=False,
        )
        return {
            **failure,
            "status": "failed",
            "cleanup_unverified": True,
            "outcome_unknown": isinstance(exc, BotoCoreError),
            "reconciliation": (
                "The abort request did not confirm cleanup. Inspect ListParts and retry "
                "the abort or rely on an explicit bucket lifecycle rule."
            ),
        }
    return {
        "status": "success",
        "cleanup_unverified": True,
        "reconciliation": (
            "Amazon S3 accepted one abort request, but in-flight parts can remain. "
            "Confirm cleanup with ListParts or an explicit bucket lifecycle rule."
        ),
        **_response_metadata(response),
    }


def _delete_file(
    request: ActionConnectorRequest,
    settings: _S3Settings,
) -> ActionConnectorResult:
    key = str(request.input_json["key"])
    read_client = _read_client(settings)
    try:
        observed = read_client.head_object(
            Bucket=settings.bucket,
            Key=key,
        )
    except (ClientError, BotoCoreError) as exc:
        error = _connector_error(
            exc,
            operation="file.delete",
            bucket=settings.bucket,
            retry_safe=True,
        )
        if isinstance(exc, ClientError) and _is_not_found(exc):
            error.output_json["reason_code"] = "object_not_found"
            error.output_json["next_action"] = "Confirm the exact current object key."
        raise error from exc

    observed_etag = _optional_text(observed.get("ETag"))
    requested_etag = request.input_json.get("if_match")
    if requested_etag is not None and requested_etag != observed_etag:
        raise ActionConnectorError(
            "Amazon S3 object ETag does not match the requested identity",
            output_json={
                "provider": "aws-s3",
                "operation": "file.delete",
                "status": "failed",
                "bucket": settings.bucket,
                "key": _redact_text(key, settings.secret_values),
                "reason_code": "etag_mismatch",
                "retry_safe": True,
                "outcome_unknown": False,
                "request_count": 1,
                "observed_etag": observed_etag,
            },
        )
    if not observed_etag:
        raise ActionConnectorError(
            "Amazon S3 object did not expose an ETag for conditional deletion",
            output_json={
                "provider": "aws-s3",
                "operation": "file.delete",
                "status": "failed",
                "bucket": settings.bucket,
                "key": _redact_text(key, settings.secret_values),
                "reason_code": "missing_object_identity",
                "retry_safe": True,
                "outcome_unknown": False,
                "request_count": 1,
            },
        )

    mutation_client = _mutation_client(settings)
    try:
        deleted = mutation_client.delete_object(
            Bucket=settings.bucket,
            Key=key,
            IfMatch=str(requested_etag or observed_etag),
        )
    except (ClientError, BotoCoreError) as exc:
        raise _connector_error(
            exc,
            operation="file.delete",
            bucket=settings.bucket,
            retry_safe=False,
            partial={
                "key": _redact_text(key, settings.secret_values),
                "observed_etag": observed_etag,
                "observed_version_id": _optional_text(observed.get("VersionId")),
                "request_count": 2,
                "reconciliation": "Head the exact key before deciding whether to retry.",
            },
        ) from exc

    metadata = _response_metadata(deleted)
    output = {
        "provider": "aws-s3",
        "operation": "file.delete",
        "status": "success",
        "bucket": settings.bucket,
        "key": key,
        "deleted_current_object": True,
        "observed_etag": observed_etag,
        "observed_version_id": _optional_text(observed.get("VersionId")),
        "delete_marker": bool(deleted.get("DeleteMarker")),
        "version_id": _optional_text(deleted.get("VersionId")),
        "historical_versions_purged": False,
        "request_count": 2,
        **metadata,
    }
    output = _redact_payload(output, settings.secret_values)
    return ActionConnectorResult(
        output_json=output,
        metadata_json={
            "provider": "aws-s3",
            "operation": "file.delete",
            "bucket": settings.bucket,
            "request_count": 2,
            **metadata,
        },
    )


def _create_directory_marker(
    request: ActionConnectorRequest,
    settings: _S3Settings,
) -> ActionConnectorResult:
    marker_key = _directory_prefix(str(request.input_json["prefix"]))
    _require_valid_key(marker_key, context="Amazon S3 directory marker")
    client = _mutation_client(settings)
    try:
        response = client.put_object(
            Bucket=settings.bucket,
            Key=marker_key,
            Body=b"",
            IfNoneMatch="*",
        )
    except (ClientError, BotoCoreError) as exc:
        error = _connector_error(
            exc,
            operation="directory.create",
            bucket=settings.bucket,
            retry_safe=not isinstance(exc, BotoCoreError),
            partial={
                "marker_key": _redact_text(marker_key, settings.secret_values),
                "marker_created": False,
                "request_count": 1,
            },
        )
        if isinstance(exc, ClientError) and _is_precondition_failure(exc):
            error.output_json["reason_code"] = "marker_exists"
            error.output_json["outcome_unknown"] = False
            error.output_json["retry_safe"] = True
        raise error from exc

    metadata = _response_metadata(response)
    output = _redact_payload(
        {
            "provider": "aws-s3",
            "operation": "directory.create",
            "status": "success",
            "bucket": settings.bucket,
            "prefix": marker_key,
            "marker_key": marker_key,
            "marker_created": True,
            "bytes": 0,
            "etag": _optional_text(response.get("ETag")),
            "version_id": _optional_text(response.get("VersionId")),
            "historical_versions_purged": False,
            "request_count": 1,
            **metadata,
        },
        settings.secret_values,
    )
    return ActionConnectorResult(
        output_json=output,
        metadata_json={
            "provider": "aws-s3",
            "operation": "directory.create",
            "bucket": settings.bucket,
            "request_count": 1,
            **metadata,
        },
    )


def _delete_directory(
    request: ActionConnectorRequest,
    settings: _S3Settings,
) -> ActionConnectorResult:
    prefix = _directory_prefix(str(request.input_json["prefix"]))
    _require_valid_key(prefix, context="Amazon S3 directory-delete prefix")
    recursive = bool(request.input_json["recursive"])
    max_objects = int(request.input_json["max_objects"])
    read_client = _read_client(settings)
    try:
        objects, inventory_requests = _inventory_prefix(
            client=read_client,
            settings=settings,
            prefix=prefix,
            max_objects=max_objects,
        )
    except (ClientError, BotoCoreError, ValidationError) as exc:
        if isinstance(exc, (ClientError, BotoCoreError)):
            raise _connector_error(
                exc,
                operation="directory.delete",
                bucket=settings.bucket,
                retry_safe=True,
                partial={
                    "prefix": _redact_text(prefix, settings.secret_values),
                    "recursive": recursive,
                    "max_objects": max_objects,
                    "mutation_started": False,
                },
            ) from exc
        raise ActionConnectorError(
            "Amazon S3 prefix inventory failed before deletion",
            output_json=_redact_payload(
                {
                    "provider": "aws-s3",
                    "operation": "directory.delete",
                    "status": "failed",
                    "bucket": settings.bucket,
                    "prefix": prefix,
                    "recursive": recursive,
                    "max_objects": max_objects,
                    "reason_code": "object_bound_exceeded",
                    "mutation_started": False,
                    "retry_safe": True,
                    "outcome_unknown": False,
                },
                settings.secret_values,
            ),
        ) from exc

    if not objects:
        raise ActionConnectorError(
            "Amazon S3 prefix has no current marker or objects",
            output_json=_redact_payload(
                {
                    "provider": "aws-s3",
                    "operation": "directory.delete",
                    "status": "failed",
                    "bucket": settings.bucket,
                    "prefix": prefix,
                    "recursive": recursive,
                    "reason_code": "prefix_not_found",
                    "mutation_started": False,
                    "request_count": inventory_requests,
                    "retry_safe": True,
                    "outcome_unknown": False,
                },
                settings.secret_values,
            ),
        )

    if not recursive:
        return _delete_empty_marker(
            settings=settings,
            prefix=prefix,
            objects=objects,
            inventory_requests=inventory_requests,
        )
    return _delete_prefix_objects(
        request=request,
        settings=settings,
        prefix=prefix,
        objects=objects,
        inventory_requests=inventory_requests,
        max_objects=max_objects,
        read_client=read_client,
    )


def _delete_empty_marker(
    *,
    settings: _S3Settings,
    prefix: str,
    objects: list[dict[str, Any]],
    inventory_requests: int,
) -> ActionConnectorResult:
    children = [item for item in objects if item.get("Key") != prefix]
    marker = next((item for item in objects if item.get("Key") == prefix), None)
    if children or marker is None:
        raise ActionConnectorError(
            "Amazon S3 prefix is not an empty directory marker",
            output_json=_redact_payload(
                {
                    "provider": "aws-s3",
                    "operation": "directory.delete",
                    "status": "failed",
                    "bucket": settings.bucket,
                    "prefix": prefix,
                    "recursive": False,
                    "reason_code": "prefix_not_empty" if children else "marker_not_found",
                    "visible_child_count": len(children),
                    "mutation_started": False,
                    "request_count": inventory_requests,
                    "retry_safe": True,
                    "outcome_unknown": False,
                },
                settings.secret_values,
            ),
        )
    if int(marker.get("Size") or 0) != 0:
        raise ActionConnectorError(
            "Amazon S3 trailing-slash object is not an empty directory marker",
            output_json=_redact_payload(
                {
                    "provider": "aws-s3",
                    "operation": "directory.delete",
                    "status": "failed",
                    "bucket": settings.bucket,
                    "prefix": prefix,
                    "recursive": False,
                    "reason_code": "invalid_marker",
                    "mutation_started": False,
                    "request_count": inventory_requests,
                    "retry_safe": True,
                    "outcome_unknown": False,
                },
                settings.secret_values,
            ),
        )
    etag = marker.get("ETag")
    if not isinstance(etag, str) or not etag:
        raise ActionConnectorError(
            "Amazon S3 marker listing did not expose an ETag for conditional deletion",
            output_json={
                "provider": "aws-s3",
                "operation": "directory.delete",
                "status": "failed",
                "reason_code": "missing_object_identity",
                "mutation_started": False,
                "request_count": inventory_requests,
                "retry_safe": True,
                "outcome_unknown": False,
            },
        )

    client = _mutation_client(settings)
    try:
        response = client.delete_object(
            Bucket=settings.bucket,
            Key=prefix,
            IfMatch=etag,
        )
    except (ClientError, BotoCoreError) as exc:
        raise _connector_error(
            exc,
            operation="directory.delete",
            bucket=settings.bucket,
            retry_safe=False,
            partial={
                "prefix": _redact_text(prefix, settings.secret_values),
                "recursive": False,
                "mutation_started": True,
                "request_count": inventory_requests + 1,
                "reconciliation": "List the exact prefix before deciding whether to retry.",
            },
        ) from exc

    metadata = _response_metadata(response)
    output = _redact_payload(
        {
            "provider": "aws-s3",
            "operation": "directory.delete",
            "status": "success",
            "bucket": settings.bucket,
            "prefix": prefix,
            "recursive": False,
            "marker_deleted": True,
            "deleted_count": 1,
            "deleted": [
                {
                    "key": prefix,
                    "delete_marker": bool(response.get("DeleteMarker")),
                    "version_id": _optional_text(response.get("VersionId")),
                }
            ],
            "historical_versions_purged": False,
            "request_count": inventory_requests + 1,
            **metadata,
        },
        settings.secret_values,
    )
    return ActionConnectorResult(
        output_json=output,
        metadata_json={
            "provider": "aws-s3",
            "operation": "directory.delete",
            "bucket": settings.bucket,
            "request_count": inventory_requests + 1,
            **metadata,
        },
    )


def _delete_prefix_objects(
    *,
    request: ActionConnectorRequest,
    settings: _S3Settings,
    prefix: str,
    objects: list[dict[str, Any]],
    inventory_requests: int,
    max_objects: int,
    read_client: Any,
) -> ActionConnectorResult:
    conditional_objects: list[dict[str, Any]] = []
    for item in objects:
        key = item.get("Key")
        etag = item.get("ETag")
        if not isinstance(key, str) or not isinstance(etag, str) or not etag:
            raise ActionConnectorError(
                "Amazon S3 prefix inventory lacks a conditional object identity",
                output_json={
                    "provider": "aws-s3",
                    "operation": "directory.delete",
                    "status": "failed",
                    "reason_code": "missing_object_identity",
                    "mutation_started": False,
                    "request_count": inventory_requests,
                    "retry_safe": True,
                    "outcome_unknown": False,
                },
            )
        # General-purpose buckets support ETag conditions for DeleteObjects.
        # Size and LastModifiedTime are directory-bucket-only preconditions.
        conditional_objects.append({"Key": key, "ETag": etag})

    client = _mutation_client(settings)
    deleted: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    request_count = inventory_requests
    outcome_unknown = False
    for offset in range(0, len(conditional_objects), 1000):
        batch = conditional_objects[offset : offset + 1000]
        request_count += 1
        try:
            response = client.delete_objects(
                Bucket=settings.bucket,
                Delete={"Objects": batch, "Quiet": False},
            )
        except (ClientError, BotoCoreError) as exc:
            failure = _transfer_failure(
                exc,
                source=prefix,
                target=settings.bucket,
                retry_safe=False,
            )
            errors.append(
                {
                    "batch_offset": offset,
                    "batch_size": len(batch),
                    **failure,
                }
            )
            outcome_unknown = isinstance(exc, BotoCoreError)
            break
        response_metadata = response.get("ResponseMetadata")
        provider_status_code = (
            response_metadata.get("HTTPStatusCode")
            if isinstance(response_metadata, Mapping)
            and isinstance(response_metadata.get("HTTPStatusCode"), int)
            else None
        )
        for item in response.get("Deleted") or []:
            if not isinstance(item, Mapping):
                continue
            deleted.append(
                {
                    "key": _optional_text(item.get("Key")),
                    "version_id": _optional_text(item.get("VersionId")),
                    "delete_marker": bool(item.get("DeleteMarker")),
                    "delete_marker_version_id": _optional_text(item.get("DeleteMarkerVersionId")),
                    **_response_metadata(response),
                }
            )
        for item in response.get("Errors") or []:
            if not isinstance(item, Mapping):
                continue
            errors.append(
                {
                    "key": _optional_text(item.get("Key")),
                    "version_id": _optional_text(item.get("VersionId")),
                    "aws_error_code": _optional_text(item.get("Code")),
                    "provider_status_code": provider_status_code,
                    **_response_metadata(response),
                }
            )
        if request.progress_callback is not None:
            request.progress_callback(
                _redact_payload(
                    {
                        "phase": "deleting",
                        "operation": "directory.delete",
                        "prefix": prefix,
                        "planned_count": len(objects),
                        "deleted_count": len(deleted),
                        "failed_count": len(errors),
                        "request_count": request_count,
                    },
                    settings.secret_values,
                )
            )

    remaining: list[dict[str, Any]] = []
    reconciliation_error: dict[str, Any] | None = None
    try:
        remaining, reconciliation_requests = _inventory_prefix(
            client=read_client,
            settings=settings,
            prefix=prefix,
            max_objects=max_objects,
        )
        request_count += reconciliation_requests
    except Exception as exc:
        reconciliation_error = _transfer_failure(
            exc,
            source=prefix,
            target=settings.bucket,
            retry_safe=True,
        )

    unconfirmed_count = max(len(objects) - len(deleted) - len(errors), 0)
    status = (
        "success"
        if not errors and not remaining and not reconciliation_error and unconfirmed_count == 0
        else "partial"
    )
    provider_fields = _provider_failure_fields(errors)
    output = _redact_payload(
        {
            "provider": "aws-s3",
            "operation": "directory.delete",
            "status": status,
            "bucket": settings.bucket,
            "prefix": prefix,
            "recursive": True,
            "planned_count": len(objects),
            "deleted_count": len(deleted),
            "failed_count": len(errors),
            "unconfirmed_count": unconfirmed_count,
            "remaining_count": len(remaining),
            "deleted": deleted,
            "errors": errors,
            "remaining_keys": [item.get("Key") for item in remaining],
            "reconciliation_error": reconciliation_error,
            "historical_versions_purged": False,
            "outcome_unknown": outcome_unknown or reconciliation_error is not None,
            "retry_safe": status == "success",
            "request_count": request_count,
            **provider_fields,
            "reconciliation": (
                None
                if status == "success"
                else "Inspect the remaining current keys and per-object errors before retrying."
            ),
        },
        settings.secret_values,
    )
    metadata = {
        "provider": "aws-s3",
        "operation": "directory.delete",
        "bucket": settings.bucket,
        "request_count": request_count,
        "planned_count": len(objects),
        "deleted_count": len(deleted),
        "failed_count": len(errors),
        "unconfirmed_count": unconfirmed_count,
        "remaining_count": len(remaining),
    }
    if status != "success":
        raise ActionConnectorError(
            "Amazon S3 recursive prefix deletion was partial",
            provider_status_code=provider_fields.get("provider_status_code"),
            provider_error=provider_fields.get("provider_error"),
            output_json=output,
            metadata_json=metadata,
        )
    if request.progress_callback is not None:
        request.progress_callback(
            {
                "phase": "complete",
                "operation": "directory.delete",
                "prefix": _redact_text(prefix, settings.secret_values),
                "planned_count": len(objects),
                "deleted_count": len(deleted),
                "failed_count": 0,
                "request_count": request_count,
            }
        )
    return ActionConnectorResult(
        output_json=output,
        metadata_json=metadata,
    )


def _move_object(
    request: ActionConnectorRequest,
    settings: _S3Settings,
) -> ActionConnectorResult:
    source_key = str(request.input_json["source_key"])
    destination_key = str(request.input_json["destination_key"])
    conflict_policy = str(request.input_json["conflict_policy"])
    read_client = _read_client(settings)
    try:
        source = read_client.head_object(
            Bucket=settings.bucket,
            Key=source_key,
        )
    except (ClientError, BotoCoreError) as exc:
        error = _connector_error(
            exc,
            operation="path.rename",
            bucket=settings.bucket,
            retry_safe=True,
            partial={
                "source_key": _redact_text(source_key, settings.secret_values),
                "destination_key": _redact_text(
                    destination_key,
                    settings.secret_values,
                ),
                "copy_completed": False,
                "source_delete_completed": False,
                "request_count": 1,
            },
        )
        if isinstance(exc, ClientError) and _is_not_found(exc):
            error.output_json["reason_code"] = "source_not_found"
        raise error from exc

    source_etag = source.get("ETag")
    if not isinstance(source_etag, str) or not source_etag:
        raise ActionConnectorError(
            "Amazon S3 source object did not expose an ETag for conditional move",
            output_json=_redact_payload(
                {
                    "provider": "aws-s3",
                    "operation": "path.rename",
                    "status": "failed",
                    "source_key": source_key,
                    "destination_key": destination_key,
                    "reason_code": "missing_source_identity",
                    "copy_completed": False,
                    "source_delete_completed": False,
                    "request_count": 1,
                    "retry_safe": True,
                    "outcome_unknown": False,
                },
                settings.secret_values,
            ),
        )
    size = source.get("ContentLength")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ActionConnectorError(
            "Amazon S3 source object did not expose a valid content length",
            output_json={
                "provider": "aws-s3",
                "operation": "path.rename",
                "status": "failed",
                "reason_code": "missing_source_size",
                "copy_completed": False,
                "source_delete_completed": False,
                "request_count": 1,
                "retry_safe": True,
                "outcome_unknown": False,
            },
        )

    client = _mutation_client(settings)
    if size <= _SINGLE_COPY_MAX_BYTES:
        copy_result, copy_requests = _copy_object_once(
            client=client,
            settings=settings,
            source_key=source_key,
            destination_key=destination_key,
            source_etag=source_etag,
            source_version_id=_optional_text(source.get("VersionId")),
            size=size,
            conflict_policy=conflict_policy,
        )
    else:
        copy_result, copy_requests = _copy_object_multipart(
            request=request,
            client=client,
            settings=settings,
            source_key=source_key,
            destination_key=destination_key,
            source_etag=source_etag,
            source_version_id=_optional_text(source.get("VersionId")),
            size=size,
            conflict_policy=conflict_policy,
        )
    if copy_result["status"] == "skipped":
        output = _redact_payload(
            {
                "provider": "aws-s3",
                "operation": "path.rename",
                "status": "skipped",
                "bucket": settings.bucket,
                "source_key": source_key,
                "destination_key": destination_key,
                "reason_code": "destination_exists",
                "copy_completed": False,
                "source_delete_completed": False,
                "non_atomic": True,
                "request_count": 1 + copy_requests,
                "retry_safe": True,
                "outcome_unknown": False,
                **copy_result,
            },
            settings.secret_values,
        )
        return ActionConnectorResult(
            output_json=output,
            metadata_json={
                "provider": "aws-s3",
                "operation": "path.rename",
                "bucket": settings.bucket,
                "request_count": 1 + copy_requests,
            },
        )

    request_count = 1 + copy_requests + 1
    try:
        deleted = client.delete_object(
            Bucket=settings.bucket,
            Key=source_key,
            IfMatch=source_etag,
        )
    except (ClientError, BotoCoreError) as exc:
        provider_failure = _transfer_failure(
            exc,
            source=source_key,
            target=destination_key,
            retry_safe=False,
        )
        provider_fields = _provider_failure_fields([provider_failure])
        output = _redact_payload(
            {
                "provider": "aws-s3",
                "operation": "path.rename",
                "status": "partial",
                "bucket": settings.bucket,
                "source_key": source_key,
                "destination_key": destination_key,
                "bytes": size,
                "source_etag": source_etag,
                "source_version_id": _optional_text(source.get("VersionId")),
                "destination_etag": copy_result.get("etag"),
                "destination_version_id": copy_result.get("version_id"),
                "copy_completed": True,
                "source_delete_completed": False,
                "non_atomic": True,
                "request_count": request_count,
                "retry_safe": False,
                "outcome_unknown": isinstance(exc, BotoCoreError),
                "source_delete_error": provider_failure,
                "reconciliation": (
                    "The destination copy exists. Inspect both exact keys and remove "
                    "the source only after confirming identities; do not blindly "
                    "repeat the whole move."
                ),
                "historical_versions_purged": False,
                "copy": copy_result,
                **provider_fields,
            },
            settings.secret_values,
        )
        raise ActionConnectorError(
            "Amazon S3 copied the destination but could not confirm source deletion",
            provider_status_code=provider_fields.get("provider_status_code"),
            provider_error=provider_fields.get("provider_error"),
            output_json=output,
            metadata_json={
                "provider": "aws-s3",
                "operation": "path.rename",
                "bucket": settings.bucket,
                "request_count": request_count,
                "copy_completed": True,
                "source_delete_completed": False,
            },
        ) from exc

    metadata = _response_metadata(deleted)
    output = _redact_payload(
        {
            "provider": "aws-s3",
            "operation": "path.rename",
            "status": "success",
            "bucket": settings.bucket,
            "source_key": source_key,
            "destination_key": destination_key,
            "bytes": size,
            "source_etag": source_etag,
            "source_version_id": _optional_text(source.get("VersionId")),
            "destination_etag": copy_result.get("etag"),
            "destination_version_id": copy_result.get("version_id"),
            "source_delete_marker": bool(deleted.get("DeleteMarker")),
            "source_delete_version_id": _optional_text(deleted.get("VersionId")),
            "copy_completed": True,
            "source_delete_completed": True,
            "non_atomic": True,
            "request_count": request_count,
            "retry_safe": False,
            "outcome_unknown": False,
            "historical_versions_purged": False,
            "copy": copy_result,
            "source_delete": metadata,
        },
        settings.secret_values,
    )
    if request.progress_callback is not None:
        request.progress_callback(
            {
                "phase": "complete",
                "operation": "path.rename",
                "source_key": _redact_text(source_key, settings.secret_values),
                "destination_key": _redact_text(
                    destination_key,
                    settings.secret_values,
                ),
                "bytes_copied": size,
                "copy_completed": True,
                "source_delete_completed": True,
                "request_count": request_count,
            }
        )
    return ActionConnectorResult(
        output_json=output,
        metadata_json={
            "provider": "aws-s3",
            "operation": "path.rename",
            "bucket": settings.bucket,
            "request_count": request_count,
            "bytes_copied": size,
            "copy_completed": True,
            "source_delete_completed": True,
            **metadata,
        },
    )


def _copy_object_once(
    *,
    client: Any,
    settings: _S3Settings,
    source_key: str,
    destination_key: str,
    source_etag: str,
    source_version_id: str | None,
    size: int,
    conflict_policy: str,
) -> tuple[dict[str, Any], int]:
    copy_source: dict[str, str] = {
        "Bucket": settings.bucket,
        "Key": source_key,
    }
    if source_version_id:
        copy_source["VersionId"] = source_version_id
    params: dict[str, Any] = {
        "Bucket": settings.bucket,
        "Key": destination_key,
        "CopySource": copy_source,
        "CopySourceIfMatch": source_etag,
    }
    if conflict_policy in {"skip", "fail"}:
        params["IfNoneMatch"] = "*"
    try:
        response = client.copy_object(**params)
    except ClientError as exc:
        if _is_precondition_failure(exc) and conflict_policy == "skip":
            return {
                "status": "skipped",
                "multipart": False,
                "part_count": 0,
            }, 1
        raise _move_copy_error(
            exc,
            settings=settings,
            source_key=source_key,
            destination_key=destination_key,
            size=size,
            request_count=2,
            multipart=False,
            completion_started=True,
        ) from exc
    except BotoCoreError as exc:
        raise _move_copy_error(
            exc,
            settings=settings,
            source_key=source_key,
            destination_key=destination_key,
            size=size,
            request_count=2,
            multipart=False,
            completion_started=True,
        ) from exc

    raw_result = response.get("CopyObjectResult")
    result = raw_result if isinstance(raw_result, Mapping) else {}
    etag = result.get("ETag")
    if not isinstance(etag, str) or not etag:
        raise ActionConnectorError(
            "Amazon S3 copy response did not confirm a destination ETag",
            output_json=_redact_payload(
                {
                    "provider": "aws-s3",
                    "operation": "path.rename",
                    "status": "partial",
                    "source_key": source_key,
                    "destination_key": destination_key,
                    "reason_code": "copy_unverified",
                    "copy_completed": False,
                    "source_delete_completed": False,
                    "non_atomic": True,
                    "request_count": 2,
                    "retry_safe": False,
                    "outcome_unknown": True,
                    "reconciliation": "Inspect the destination before retrying the move.",
                },
                settings.secret_values,
            ),
        )
    return {
        "status": "success",
        "multipart": False,
        "part_count": 1,
        "bytes": size,
        "etag": etag,
        "version_id": _optional_text(response.get("VersionId")),
        "copy_source_version_id": _optional_text(response.get("CopySourceVersionId")),
        "checksum_crc32": _optional_text(result.get("ChecksumCRC32")),
        "checksum_crc32c": _optional_text(result.get("ChecksumCRC32C")),
        "checksum_sha1": _optional_text(result.get("ChecksumSHA1")),
        "checksum_sha256": _optional_text(result.get("ChecksumSHA256")),
        **_response_metadata(response),
    }, 1


def _copy_object_multipart(
    *,
    request: ActionConnectorRequest,
    client: Any,
    settings: _S3Settings,
    source_key: str,
    destination_key: str,
    source_etag: str,
    source_version_id: str | None,
    size: int,
    conflict_policy: str,
) -> tuple[dict[str, Any], int]:
    copy_source: dict[str, str] = {
        "Bucket": settings.bucket,
        "Key": source_key,
    }
    if source_version_id:
        copy_source["VersionId"] = source_version_id
    request_count = 1
    upload_id: str | None = None
    parts: list[dict[str, Any]] = []
    completion_started = False
    try:
        part_size = _multipart_part_size(
            size,
            minimum=_COPY_PART_SIZE,
        )
        created = client.create_multipart_upload(
            Bucket=settings.bucket,
            Key=destination_key,
        )
        upload_id_value = created.get("UploadId")
        if not isinstance(upload_id_value, str) or not upload_id_value:
            raise ValidationError("Amazon S3 did not return a multipart copy upload id")
        upload_id = upload_id_value

        start = 0
        part_number = 1
        while start < size:
            if part_number > _MAX_MULTIPART_PARTS:
                raise ValidationError("Amazon S3 multipart copy exceeds 10,000 parts")
            end = min(start + part_size, size) - 1
            request_count += 1
            response = client.upload_part_copy(
                Bucket=settings.bucket,
                Key=destination_key,
                PartNumber=part_number,
                UploadId=upload_id,
                CopySource=copy_source,
                CopySourceRange=f"bytes={start}-{end}",
                CopySourceIfMatch=source_etag,
            )
            raw_part = response.get("CopyPartResult")
            part_result = raw_part if isinstance(raw_part, Mapping) else {}
            etag = part_result.get("ETag")
            if not isinstance(etag, str) or not etag:
                raise ValidationError("Amazon S3 copied part did not return an ETag")
            parts.append({"ETag": etag, "PartNumber": part_number})
            if request.progress_callback is not None:
                request.progress_callback(
                    {
                        "phase": "copying",
                        "operation": "path.rename",
                        "source_key": _redact_text(
                            source_key,
                            settings.secret_values,
                        ),
                        "destination_key": _redact_text(
                            destination_key,
                            settings.secret_values,
                        ),
                        "bytes_copied": end + 1,
                        "bytes_total": size,
                        "part_count": len(parts),
                        "request_count": 1 + request_count,
                    }
                )
            start = end + 1
            part_number += 1

        params: dict[str, Any] = {
            "Bucket": settings.bucket,
            "Key": destination_key,
            "UploadId": upload_id,
            "MultipartUpload": {"Parts": parts},
        }
        if conflict_policy in {"skip", "fail"}:
            params["IfNoneMatch"] = "*"
        request_count += 1
        completion_started = True
        try:
            completed = client.complete_multipart_upload(**params)
        except ClientError as exc:
            if _is_precondition_failure(exc) and conflict_policy == "skip":
                skip_abort, abort_requests = _abort_move_copy(
                    client=client,
                    settings=settings,
                    destination_key=destination_key,
                    upload_id=upload_id,
                )
                return {
                    "status": "skipped",
                    "multipart": True,
                    "part_count": len(parts),
                    "multipart_upload_id": upload_id,
                    "cleanup_unverified": True,
                    "abort": skip_abort,
                }, request_count + abort_requests
            raise

        etag = completed.get("ETag")
        if not isinstance(etag, str) or not etag:
            raise ValidationError("Amazon S3 multipart copy completion did not return an ETag")
        return {
            "status": "success",
            "multipart": True,
            "part_count": len(parts),
            "bytes": size,
            "etag": etag,
            "version_id": _optional_text(completed.get("VersionId")),
            "checksum_crc32": _optional_text(completed.get("ChecksumCRC32")),
            "checksum_crc32c": _optional_text(completed.get("ChecksumCRC32C")),
            "checksum_sha1": _optional_text(completed.get("ChecksumSHA1")),
            "checksum_sha256": _optional_text(completed.get("ChecksumSHA256")),
            **_response_metadata(completed),
        }, request_count
    except ActionConnectorError:
        raise
    except Exception as exc:
        completion_ambiguous = completion_started and isinstance(exc, BotoCoreError)
        cleanup_abort: dict[str, Any] | None = None
        abort_requests = 0
        if upload_id and not completion_ambiguous:
            cleanup_abort, abort_requests = _abort_move_copy(
                client=client,
                settings=settings,
                destination_key=destination_key,
                upload_id=upload_id,
            )
        raise _move_copy_error(
            exc,
            settings=settings,
            source_key=source_key,
            destination_key=destination_key,
            size=size,
            request_count=1 + request_count + abort_requests,
            multipart=True,
            completion_started=completion_started,
            upload_id=upload_id,
            part_count=len(parts),
            abort=cleanup_abort,
        ) from exc


def _abort_move_copy(
    *,
    client: Any,
    settings: _S3Settings,
    destination_key: str,
    upload_id: str,
) -> tuple[dict[str, Any], int]:
    try:
        response = client.abort_multipart_upload(
            Bucket=settings.bucket,
            Key=destination_key,
            UploadId=upload_id,
        )
    except Exception as exc:
        failure = _transfer_failure(
            exc,
            source=destination_key,
            target=upload_id,
            retry_safe=False,
        )
        return {
            **failure,
            "status": "failed",
            "cleanup_unverified": True,
            "outcome_unknown": isinstance(exc, BotoCoreError),
            "reconciliation": (
                "The abort request did not confirm cleanup. Inspect ListParts and retry "
                "the abort or rely on an explicit bucket lifecycle rule."
            ),
        }, 1
    return {
        "status": "success",
        "cleanup_unverified": True,
        "reconciliation": (
            "Amazon S3 accepted one abort request, but in-flight parts can remain. "
            "Confirm cleanup with ListParts or an explicit bucket lifecycle rule."
        ),
        **_response_metadata(response),
    }, 1


def _move_copy_error(
    exc: Exception,
    *,
    settings: _S3Settings,
    source_key: str,
    destination_key: str,
    size: int,
    request_count: int,
    multipart: bool,
    completion_started: bool,
    upload_id: str | None = None,
    part_count: int = 0,
    abort: dict[str, Any] | None = None,
) -> ActionConnectorError:
    failure = _transfer_failure(
        exc,
        source=source_key,
        target=destination_key,
        retry_safe=False,
    )
    provider_fields = _provider_failure_fields([failure])
    output = _redact_payload(
        {
            "provider": "aws-s3",
            "operation": "path.rename",
            "status": "failed",
            "bucket": settings.bucket,
            "source_key": source_key,
            "destination_key": destination_key,
            "bytes": size,
            "multipart": multipart,
            "multipart_upload_id": upload_id,
            "part_count": part_count,
            "completion_started": completion_started,
            "copy_completed": False,
            "source_delete_completed": False,
            "non_atomic": True,
            "request_count": request_count,
            "retry_safe": False,
            "outcome_unknown": isinstance(exc, BotoCoreError),
            "cleanup_unverified": multipart and upload_id is not None,
            "copy_error": failure,
            "abort": abort,
            "reconciliation": (
                "Inspect the exact destination and source identities before retrying. "
                "For multipart copy, also confirm no retained parts with ListParts or "
                "an explicit bucket lifecycle rule."
            ),
            "historical_versions_purged": False,
            **provider_fields,
        },
        settings.secret_values,
    )
    return ActionConnectorError(
        "Amazon S3 object copy failed",
        provider_status_code=provider_fields.get("provider_status_code"),
        provider_error=provider_fields.get("provider_error"),
        output_json=output,
        metadata_json={
            "provider": "aws-s3",
            "operation": "path.rename",
            "bucket": settings.bucket,
            "request_count": request_count,
            "copy_completed": False,
            "source_delete_completed": False,
        },
    )


def _download(
    request: ActionConnectorRequest,
    settings: _S3Settings,
) -> ActionConnectorResult:
    client = _read_client(settings)
    conflict_policy = str(request.input_json["conflict_policy"])
    error_policy = str(request.input_json["error_policy"])
    state = _TransferState(
        operation="file.download",
        callback=request.progress_callback,
        secrets=settings.secret_values,
    )
    state.progress(phase="preparing")

    for item in request.input_json["items"]:
        remote_key = str(item["remote_key"])
        local_path = Path(str(item["local_path"])).expanduser()
        try:
            if item["remote_kind"] == "object":
                _download_one(
                    client=client,
                    settings=settings,
                    key=remote_key,
                    target=local_path,
                    conflict_policy=conflict_policy,
                    state=state,
                )
            else:
                _download_prefix(
                    client=client,
                    settings=settings,
                    prefix=remote_key,
                    destination=local_path,
                    conflict_policy=conflict_policy,
                    state=state,
                )
        except Exception as exc:
            failure = _transfer_failure(
                exc,
                source=remote_key,
                target=str(local_path),
                retry_safe=True,
            )
            state.failed.append(failure)
            state.progress(
                phase="failed",
                current_source_path=remote_key,
                current_target_path=str(local_path),
            )
            if error_policy == "stop":
                raise _transfer_error(
                    "Amazon S3 download stopped after a failed mapping",
                    state,
                ) from exc

    output = state.output()
    state.progress(phase="complete")
    if state.failed and not state.completed and not state.skipped:
        raise _transfer_error("Amazon S3 download failed", state)
    return ActionConnectorResult(
        output_json=output,
        metadata_json={
            "provider": "aws-s3",
            "operation": "file.download",
            "bucket": settings.bucket,
            "request_count": state.request_count,
            "bytes_transferred": state.bytes_transferred,
        },
    )


def _download_one(
    *,
    client: Any,
    settings: _S3Settings,
    key: str,
    target: Path,
    conflict_policy: str,
    state: _TransferState,
) -> None:
    disposition = _local_conflict(target, conflict_policy)
    if disposition == "skip":
        state.skipped.append(
            {
                "remote_key": key,
                "local_path": str(target),
                "reason_code": "destination_exists",
            }
        )
        state.progress(
            phase="skipped",
            current_source_path=key,
            current_target_path=str(target),
        )
        return
    if disposition == "fail":
        raise FileExistsError(f"local destination already exists: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    _assert_parent_safe(target.parent, target.parent)
    temporary = target.with_name(f".{target.name}.stackos-{uuid.uuid4().hex}.part")
    body: Any | None = None
    try:
        state.request_count += 1
        response = client.get_object(
            Bucket=settings.bucket,
            Key=key,
        )
        body = response["Body"]
        written = _write_stream(
            body=body,
            temporary=temporary,
            state=state,
            source=key,
            target=target,
        )
        commit = _commit_download(
            temporary=temporary,
            target=target,
            conflict_policy=conflict_policy,
        )
        if commit == "skip":
            state.skipped.append(
                {
                    "remote_key": key,
                    "local_path": str(target),
                    "reason_code": "destination_race",
                }
            )
            return
        metadata = _response_metadata(response)
        state.completed.append(
            {
                "remote_key": key,
                "local_path": str(target),
                "bytes": written,
                "etag": _optional_text(response.get("ETag")),
                "version_id": _optional_text(response.get("VersionId")),
                **metadata,
            }
        )
    finally:
        if body is not None and callable(getattr(body, "close", None)):
            with suppress(Exception):
                body.close()
        with suppress(FileNotFoundError):
            temporary.unlink()
    state.progress(
        phase="transferred",
        current_source_path=key,
        current_target_path=str(target),
    )


def _download_prefix(
    *,
    client: Any,
    settings: _S3Settings,
    prefix: str,
    destination: Path,
    conflict_policy: str,
    state: _TransferState,
) -> None:
    normalized = _directory_prefix(prefix)
    _require_valid_key(normalized, context="Amazon S3 download prefix")
    objects, list_requests = _inventory_prefix(
        client=client,
        settings=settings,
        prefix=normalized,
        max_objects=_DOWNLOAD_OBJECT_LIMIT,
    )
    state.request_count += list_requests
    if not objects:
        raise FileNotFoundError(f"Amazon S3 prefix has no current objects: {normalized}")
    if destination.exists() and not destination.is_dir():
        raise NotADirectoryError(f"local prefix destination is not a directory: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve(strict=False)

    for item in objects:
        key = str(item["Key"])
        relative = key[len(normalized) :]
        if not relative:
            state.completed.append(
                {
                    "remote_key": key,
                    "local_path": str(destination),
                    "type": "directory_marker",
                    "bytes": 0,
                }
            )
            continue
        if key.endswith("/"):
            if int(item.get("Size") or 0) != 0:
                raise ValidationError(
                    "Amazon S3 trailing-slash object must be empty to project as a marker"
                )
            marker_relative = relative[:-1]
            marker_target = (
                destination
                if not marker_relative
                else destination.joinpath(*_safe_relative_parts(marker_relative))
            )
            _assert_parent_safe(root, marker_target)
            if marker_target.exists() and not marker_target.is_dir():
                raise NotADirectoryError(
                    f"local marker destination is not a directory: {marker_target}"
                )
            marker_target.mkdir(parents=True, exist_ok=True)
            state.completed.append(
                {
                    "remote_key": key,
                    "local_path": str(marker_target),
                    "type": "directory_marker",
                    "bytes": 0,
                }
            )
            continue
        parts = _safe_relative_parts(relative)
        target = destination.joinpath(*parts)
        _assert_parent_safe(root, target.parent)
        _download_one(
            client=client,
            settings=settings,
            key=key,
            target=target,
            conflict_policy=conflict_policy,
            state=state,
        )


def _inventory_prefix(
    *,
    client: Any,
    settings: _S3Settings,
    prefix: str,
    max_objects: int,
) -> tuple[list[dict[str, Any]], int]:
    objects: list[dict[str, Any]] = []
    cursor: str | None = None
    requests = 0
    while True:
        params: dict[str, Any] = {
            "Bucket": settings.bucket,
            "Prefix": prefix,
            "MaxKeys": min(1000, max_objects + 1),
            "EncodingType": "url",
        }
        if cursor is not None:
            params["ContinuationToken"] = cursor
        requests += 1
        response = client.list_objects_v2(**params)
        url_encoded = response.get("EncodingType") == "url"
        page = response.get("Contents") or []
        for item in page:
            if not isinstance(item, Mapping) or not isinstance(item.get("Key"), str):
                raise ValidationError("Amazon S3 returned an object without a valid key")
            key = _decode_listing_text(
                str(item["Key"]),
                url_encoded=url_encoded,
            )
            if not key.startswith(prefix):
                raise ValidationError("Amazon S3 returned an object outside the selected prefix")
            _require_valid_key(key, context="provider-returned Amazon S3 object key")
            decoded_item = dict(item)
            decoded_item["Key"] = key
            objects.append(decoded_item)
            if len(objects) > max_objects:
                raise ValidationError(
                    f"Amazon S3 prefix exceeds the {max_objects} object operation bound"
                )
        cursor_value = response.get("NextContinuationToken")
        if not response.get("IsTruncated"):
            break
        if not isinstance(cursor_value, str) or not cursor_value:
            raise ValidationError("Amazon S3 truncated a listing without a continuation cursor")
        cursor = cursor_value
    return objects, requests


def _write_stream(
    *,
    body: Any,
    temporary: Path,
    state: _TransferState,
    source: str,
    target: Path,
) -> int:
    written = 0
    with temporary.open("xb") as output:
        while True:
            chunk = body.read(_IO_CHUNK_SIZE)
            if not chunk:
                break
            if not isinstance(chunk, bytes):
                raise OSError("Amazon S3 response body returned non-byte content")
            output.write(chunk)
            written += len(chunk)
            state.bytes_transferred += len(chunk)
            state.progress(
                phase="transferring",
                current_source_path=source,
                current_target_path=str(target),
            )
        output.flush()
        os.fsync(output.fileno())
    return written


def _commit_download(
    *,
    temporary: Path,
    target: Path,
    conflict_policy: str,
) -> str:
    if conflict_policy == "overwrite":
        os.replace(temporary, target)
        return "complete"
    try:
        os.link(temporary, target)
    except FileExistsError:
        if conflict_policy == "skip":
            return "skip"
        raise
    temporary.unlink()
    return "complete"


def _settings(request: ActionConnectorRequest) -> _S3Settings:
    if request.credential is None:
        raise ValidationError("aws-s3 requires a credential")
    config = credential_config(request)
    try:
        validate_s3_credential_config(config)
        credentials = parse_s3_credentials(request.credential.secret_payload)
    except (ValueError, IntegrationDownError) as exc:
        raise ValidationError("Amazon S3 Account configuration or credential is invalid") from exc
    return _S3Settings(
        bucket=str(config["bucket"]).strip(),
        region=str(config["region"]).strip(),
        prefix=normalize_s3_prefix(config.get("prefix")),
        payload=request.credential.secret_payload,
        secret_values=tuple(
            value
            for value in (
                credentials.access_key_id,
                credentials.secret_access_key,
                credentials.session_token,
            )
            if value
        ),
    )


def _multipart_part_size(size: int, *, minimum: int) -> int:
    if size < 0 or size > _MAX_OBJECT_SIZE:
        raise ValidationError(
            "Amazon S3 objects must not exceed the 50,000 GiB (about 48.8 TiB) provider limit"
        )
    required = (size + _MAX_MULTIPART_PARTS - 1) // _MAX_MULTIPART_PARTS
    selected = max(minimum, required)
    alignment = _PART_SIZE_ALIGNMENT if minimum >= _PART_SIZE_ALIGNMENT else 1
    aligned = ((selected + alignment - 1) // alignment) * alignment
    if aligned > _MAX_MULTIPART_PART_SIZE:
        raise ValidationError("Amazon S3 multipart parts must not exceed 5 GiB")
    return aligned


def _read_client(settings: _S3Settings) -> Any:
    return _ScopedS3Client(
        create_s3_client(
            payload=settings.payload,
            region=settings.region,
            total_max_attempts=3,
        ),
        bucket=settings.bucket,
        prefix=settings.prefix,
    )


def _mutation_client(settings: _S3Settings) -> Any:
    return _ScopedS3Client(
        create_s3_client(
            payload=settings.payload,
            region=settings.region,
            total_max_attempts=1,
        ),
        bucket=settings.bucket,
        prefix=settings.prefix,
    )


def _is_precondition_failure(exc: ClientError) -> bool:
    response = exc.response if isinstance(exc.response, Mapping) else {}
    error = response.get("Error")
    code = str(error.get("Code") or "") if isinstance(error, Mapping) else ""
    metadata = response.get("ResponseMetadata")
    status = metadata.get("HTTPStatusCode") if isinstance(metadata, Mapping) else None
    return status == 412 or code in {"PreconditionFailed", "412"}


def _is_not_found(exc: ClientError) -> bool:
    response = exc.response if isinstance(exc.response, Mapping) else {}
    error = response.get("Error")
    code = str(error.get("Code") or "") if isinstance(error, Mapping) else ""
    metadata = response.get("ResponseMetadata")
    status = metadata.get("HTTPStatusCode") if isinstance(metadata, Mapping) else None
    return status == 404 or code in {"NoSuchKey", "NotFound", "404"}


def _object_entry(
    item: Any,
    secrets: tuple[str, ...],
    *,
    url_encoded: bool,
) -> dict[str, Any]:
    if not isinstance(item, Mapping) or not isinstance(item.get("Key"), str):
        raise ValidationError("Amazon S3 returned an object without a valid key")
    raw_key = _decode_listing_text(
        str(item["Key"]),
        url_encoded=url_encoded,
    )
    safe_key = _redact_text(raw_key, secrets)
    problem = _key_problem(raw_key, allow_empty=False)
    entry = {
        "key": safe_key,
        "key_redacted": safe_key != raw_key,
        "safe_to_use": safe_key == raw_key and problem is None,
        "size": int(item.get("Size") or 0),
        "etag": _optional_text(item.get("ETag")),
        "last_modified": _json_time(item.get("LastModified")),
        "storage_class": _optional_text(item.get("StorageClass")),
        "checksum_algorithms": [str(value) for value in item.get("ChecksumAlgorithm") or []],
    }
    if problem is not None:
        entry["unsafe_reason"] = problem[1]
    return entry


def _response_metadata(response: Mapping[str, Any]) -> dict[str, Any]:
    raw = response.get("ResponseMetadata")
    metadata = raw if isinstance(raw, Mapping) else {}
    headers_raw = metadata.get("HTTPHeaders")
    headers = headers_raw if isinstance(headers_raw, Mapping) else {}
    request_id = metadata.get("RequestId") or headers.get("x-amz-request-id")
    extended_request_id = metadata.get("HostId") or headers.get("x-amz-id-2")
    return {
        "request_id": _optional_text(request_id),
        "extended_request_id": _optional_text(extended_request_id),
    }


def _connector_error(
    exc: Exception,
    *,
    operation: str,
    bucket: str,
    retry_safe: bool,
    partial: dict[str, Any] | None = None,
) -> ActionConnectorError:
    provider_status: int | None = None
    error_code = type(exc).__name__
    metadata: dict[str, Any] = {}
    if isinstance(exc, ClientError):
        response = exc.response if isinstance(exc.response, Mapping) else {}
        error = response.get("Error")
        if isinstance(error, Mapping):
            error_code = str(error.get("Code") or "Unknown")[:160]
        response_metadata = response.get("ResponseMetadata")
        if isinstance(response_metadata, Mapping):
            status = response_metadata.get("HTTPStatusCode")
            if isinstance(status, int):
                provider_status = status
        metadata = _response_metadata(response)
    output = {
        "provider": "aws-s3",
        "operation": operation,
        "status": "failed",
        "bucket": bucket,
        "aws_error_code": error_code,
        "provider_status_code": provider_status,
        "provider_error": {"code": error_code},
        "outcome_unknown": isinstance(exc, BotoCoreError) and not retry_safe,
        "retry_safe": retry_safe,
        **metadata,
    }
    if partial:
        output.update(partial)
    return ActionConnectorError(
        f"Amazon S3 {operation} failed",
        provider_status_code=provider_status,
        provider_error={"code": error_code},
        output_json=output,
        metadata_json={
            "provider": "aws-s3",
            "operation": operation,
            "bucket": bucket,
            **metadata,
        },
    )


def _transfer_failure(
    exc: Exception,
    *,
    source: str,
    target: str,
    retry_safe: bool,
) -> dict[str, Any]:
    failure = {
        "source_path": source,
        "target_path": target,
        "error_type": type(exc).__name__,
        "retry_safe": retry_safe,
    }
    if isinstance(exc, ClientError):
        response = exc.response if isinstance(exc.response, Mapping) else {}
        error = response.get("Error")
        if isinstance(error, Mapping):
            failure["aws_error_code"] = str(error.get("Code") or "Unknown")[:160]
        response_metadata = response.get("ResponseMetadata")
        if isinstance(response_metadata, Mapping) and isinstance(
            response_metadata.get("HTTPStatusCode"),
            int,
        ):
            failure["provider_status_code"] = response_metadata["HTTPStatusCode"]
        failure.update(_response_metadata(response))
    elif isinstance(exc, BotoCoreError):
        failure["reason_code"] = "transport_error"
        failure["outcome_unknown"] = not retry_safe
        if not retry_safe:
            failure["reconciliation"] = (
                "Inspect provider state before retrying because the request outcome is unknown."
            )
    elif isinstance(exc, OSError):
        failure["reason_code"] = "filesystem_error"
    else:
        failure["reason_code"] = "validation_error"
    return failure


def _transfer_error(detail: str, state: _TransferState) -> ActionConnectorError:
    output = state.output()
    output["retry_safe"] = all(bool(item.get("retry_safe", False)) for item in state.failed)
    provider_fields = _provider_failure_fields(state.failed)
    output.update(provider_fields)
    return ActionConnectorError(
        detail,
        provider_status_code=provider_fields.get("provider_status_code"),
        provider_error=provider_fields.get("provider_error"),
        output_json=output,
        metadata_json={
            "provider": "aws-s3",
            "operation": state.operation,
            "request_count": state.request_count,
            "bytes_transferred": state.bytes_transferred,
        },
    )


def _provider_failure_fields(
    failures: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    for failure in failures:
        raw_status = failure.get("provider_status_code", failure.get("http_status"))
        status = raw_status if isinstance(raw_status, int) else None
        raw_code = failure.get("aws_error_code")
        code = str(raw_code)[:160] if raw_code is not None else None
        if status is None and code is None:
            continue
        fields: dict[str, Any] = {}
        if status is not None:
            fields["provider_status_code"] = status
        if code is not None:
            fields["provider_error"] = {"code": code}
        return fields
    return {}


def _decode_listing_text(value: str, *, url_encoded: bool) -> str:
    if not url_encoded:
        return value
    try:
        return unquote(value, encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValidationError("Amazon S3 returned an invalid URL-encoded object key") from exc


def _directory_prefix(value: str) -> str:
    return value if value.endswith("/") else f"{value}/"


def _safe_relative_parts(relative: str) -> tuple[str, ...]:
    if not relative:
        raise ValidationError("Amazon S3 relative object key must not be empty")
    parts = tuple(relative.split("/"))
    for part in parts:
        if (
            part in {"", ".", ".."}
            or "\\" in part
            or ":" in part
            or any(ord(char) < 32 or ord(char) == 127 for char in part)
        ):
            raise ValidationError("Amazon S3 object key cannot be projected safely to a local path")
    return parts


def _assert_parent_safe(root: Path, parent: Path) -> None:
    resolved_root = root.resolve(strict=False)
    resolved_parent = parent.resolve(strict=False)
    try:
        resolved_parent.relative_to(resolved_root)
    except ValueError as exc:
        raise ValidationError(
            "Amazon S3 object key escaped the selected local destination"
        ) from exc


def _local_conflict(path: Path, policy: str) -> str:
    if not path.exists() and not path.is_symlink():
        return "write"
    if policy == "skip":
        return "skip"
    if policy == "fail":
        return "fail"
    if path.is_dir() and not path.is_symlink():
        raise IsADirectoryError(f"local destination is a directory: {path}")
    return "write"


def _validate_key(
    value: Any,
    path: str,
    issues: list[ActionValidationIssue],
    *,
    allow_empty: bool = False,
    allow_exact_soap: bool = False,
) -> None:
    problem = _key_problem(
        value,
        allow_empty=allow_empty,
        allow_exact_soap=allow_exact_soap,
    )
    if problem is not None:
        message, code = problem
        issues.append(issue(path, message, code))


def _require_valid_key(value: str, *, context: str) -> None:
    problem = _key_problem(value, allow_empty=False)
    if problem is not None:
        message, _code = problem
        raise ValidationError(f"{context}: {message}")


def _key_problem(
    value: Any,
    *,
    allow_empty: bool,
    allow_exact_soap: bool = False,
) -> tuple[str, str] | None:
    if not isinstance(value, str) or (not value and not allow_empty):
        return "S3 key or prefix must be a string", "required"
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return "value contains unsafe control characters", "unsafe"
    if len(value.encode("utf-8")) > 1024:
        return "S3 key or prefix must be at most 1024 UTF-8 bytes", "max_length"
    if value == "soap" and not allow_exact_soap:
        return (
            "exact S3 object key 'soap' requires unsupported path-style addressing",
            "unsupported",
        )
    return None


def _validate_no_control(
    value: str,
    path: str,
    issues: list[ActionValidationIssue],
) -> None:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        issues.append(issue(path, "value contains unsafe control characters", "unsafe"))


def _validate_local_path(
    value: Any,
    path: str,
    issues: list[ActionValidationIssue],
) -> None:
    if not isinstance(value, str) or not value:
        issues.append(issue(path, "local_path must be a non-empty string", "required"))
        return
    _validate_no_control(value, path, issues)


def _validate_policy(
    value: Any,
    path: str,
    allowed: set[str],
    issues: list[ActionValidationIssue],
) -> None:
    if value not in allowed:
        issues.append(issue(path, f"value must be one of {', '.join(sorted(allowed))}", "enum"))


def _validate_int(
    value: Any,
    path: str,
    issues: list[ActionValidationIssue],
    *,
    minimum: int,
    maximum: int,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        issues.append(issue(path, "value must be an integer", "type_error"))
    elif not minimum <= value <= maximum:
        issues.append(issue(path, f"value must be between {minimum} and {maximum}", "range"))


def _json_time(value: Any) -> str | None:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value) if value is not None else None


def _optional_text(value: Any) -> str | None:
    return str(value) if value is not None else None


def _redact_text(value: str | None, secrets: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    redacted = value
    for secret in secrets:
        redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def _redact_payload(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        return _redact_text(value, secrets)
    if isinstance(value, list):
        return [_redact_payload(item, secrets) for item in value]
    if isinstance(value, tuple):
        return [_redact_payload(item, secrets) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _redact_payload(item, secrets) for key, item in value.items()}
    return value


__all__ = ["S3ActionConnector"]
