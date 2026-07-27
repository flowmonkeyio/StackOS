"""Amazon S3 connector tests through the generic action request contract."""

from __future__ import annotations

import asyncio
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from sqlmodel import Session

from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRequest,
)
from stackos.actions.s3 import S3ActionConnector, _multipart_part_size
from stackos.auth_providers import AuthRepository
from stackos.repositories.base import ValidationError


class _Body(io.BytesIO):
    closed_by_connector = False

    def close(self) -> None:
        self.closed_by_connector = True
        super().close()


class _FailingBody:
    def __init__(self) -> None:
        self.calls = 0
        self.closed_by_connector = False

    def read(self, _size: int) -> bytes:
        self.calls += 1
        if self.calls == 1:
            return b"partial"
        raise OSError("stream interrupted with s3-secret")

    def close(self) -> None:
        self.closed_by_connector = True


def test_s3_multipart_part_sizes_expand_before_the_provider_part_limit() -> None:
    mebibyte = 1024 * 1024
    upload_size = 8 * mebibyte * 10_000 + 1
    copy_size = 64 * mebibyte * 10_000 + 1

    upload_part_size = _multipart_part_size(upload_size, minimum=8 * mebibyte)
    copy_part_size = _multipart_part_size(copy_size, minimum=64 * mebibyte)

    assert upload_part_size > 8 * mebibyte
    assert copy_part_size > 64 * mebibyte
    assert (upload_size + upload_part_size - 1) // upload_part_size <= 10_000
    assert (copy_size + copy_part_size - 1) // copy_part_size <= 10_000
    provider_maximum = 10_000 * 5 * 1024**3
    assert (
        _multipart_part_size(
            provider_maximum,
            minimum=8 * mebibyte,
        )
        == 5 * 1024**3
    )
    with pytest.raises(ValidationError, match=r"48\.8 TiB"):
        _multipart_part_size(provider_maximum + 1, minimum=8 * mebibyte)


class _FakeS3:
    def __init__(self) -> None:
        self.list_responses: list[dict[str, Any] | Exception] = []
        self.objects: dict[str, bytes | Exception | _FailingBody] = {}
        self.operation_outcomes: dict[str, list[dict[str, Any] | Exception]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.bodies: list[_Body] = []

    def _outcome(self, operation: str, default: dict[str, Any]) -> dict[str, Any]:
        queue = self.operation_outcomes.get(operation)
        outcome = queue.pop(0) if queue else default
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("list_objects_v2", kwargs))
        response = self.list_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_object", kwargs))
        value = self.objects[kwargs["Key"]]
        if isinstance(value, Exception):
            raise value
        body = _Body(value) if isinstance(value, bytes) else value
        if isinstance(body, _Body):
            self.bodies.append(body)
        return {
            "Body": body,
            "ContentLength": len(value) if isinstance(value, bytes) else 0,
            "ETag": f'"etag-{kwargs["Key"]}"',
            "VersionId": f"version-{kwargs['Key']}",
            "ResponseMetadata": {
                "HTTPStatusCode": 200,
                "RequestId": f"request-{kwargs['Key']}",
                "HostId": f"extended-{kwargs['Key']}",
            },
        }

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("put_object", kwargs))
        return self._outcome(
            "put_object",
            {
                "ETag": '"put-etag"',
                "VersionId": "put-version",
                "ResponseMetadata": {
                    "HTTPStatusCode": 200,
                    "RequestId": "request-put",
                    "HostId": "extended-put",
                },
            },
        )

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("head_object", kwargs))
        return self._outcome(
            "head_object",
            {
                "ETag": '"head-etag"',
                "VersionId": "head-version",
                "ContentLength": 7,
                "ResponseMetadata": {
                    "HTTPStatusCode": 200,
                    "RequestId": "request-head",
                    "HostId": "extended-head",
                },
            },
        )

    def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("delete_object", kwargs))
        return self._outcome(
            "delete_object",
            {
                "DeleteMarker": True,
                "VersionId": "delete-marker-version",
                "ResponseMetadata": {
                    "HTTPStatusCode": 204,
                    "RequestId": "request-delete",
                    "HostId": "extended-delete",
                },
            },
        )

    def delete_objects(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("delete_objects", kwargs))
        return self._outcome(
            "delete_objects",
            {
                "Deleted": [
                    {
                        "Key": item["Key"],
                        "DeleteMarker": True,
                        "DeleteMarkerVersionId": f"marker-{item['Key']}",
                    }
                    for item in kwargs["Delete"]["Objects"]
                ],
                "Errors": [],
                "ResponseMetadata": {
                    "HTTPStatusCode": 200,
                    "RequestId": "request-delete-batch",
                    "HostId": "extended-delete-batch",
                },
            },
        )

    def copy_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("copy_object", kwargs))
        return self._outcome(
            "copy_object",
            {
                "CopyObjectResult": {
                    "ETag": '"destination-etag"',
                    "ChecksumSHA256": "destination-sha256",
                },
                "VersionId": "destination-version",
                "CopySourceVersionId": "head-version",
                "ResponseMetadata": {
                    "HTTPStatusCode": 200,
                    "RequestId": "request-copy",
                    "HostId": "extended-copy",
                },
            },
        )

    def upload_part_copy(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("upload_part_copy", kwargs))
        return self._outcome(
            "upload_part_copy",
            {
                "CopyPartResult": {
                    "ETag": f'"copy-part-{kwargs["PartNumber"]}"',
                },
                "ResponseMetadata": {
                    "HTTPStatusCode": 200,
                    "RequestId": f"request-copy-part-{kwargs['PartNumber']}",
                },
            },
        )

    def create_multipart_upload(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("create_multipart_upload", kwargs))
        return self._outcome(
            "create_multipart_upload",
            {
                "UploadId": "upload-123",
                "ResponseMetadata": {
                    "HTTPStatusCode": 200,
                    "RequestId": "request-create",
                },
            },
        )

    def upload_part(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("upload_part", kwargs))
        return self._outcome(
            "upload_part",
            {
                "ETag": f'"part-{kwargs["PartNumber"]}"',
                "ResponseMetadata": {
                    "HTTPStatusCode": 200,
                    "RequestId": f"request-part-{kwargs['PartNumber']}",
                },
            },
        )

    def complete_multipart_upload(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("complete_multipart_upload", kwargs))
        return self._outcome(
            "complete_multipart_upload",
            {
                "ETag": '"complete-etag"',
                "VersionId": "complete-version",
                "ResponseMetadata": {
                    "HTTPStatusCode": 200,
                    "RequestId": "request-complete",
                    "HostId": "extended-complete",
                },
            },
        )

    def abort_multipart_upload(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("abort_multipart_upload", kwargs))
        return self._outcome(
            "abort_multipart_upload",
            {
                "ResponseMetadata": {
                    "HTTPStatusCode": 204,
                    "RequestId": "request-abort",
                    "HostId": "extended-abort",
                }
            },
        )


def _list_response(
    *,
    contents: list[dict[str, Any]],
    common_prefixes: list[str] | None = None,
    truncated: bool = False,
    next_cursor: str | None = None,
    encoding_type: str | None = "url",
) -> dict[str, Any]:
    response = {
        "Contents": contents,
        "CommonPrefixes": [{"Prefix": prefix} for prefix in common_prefixes or []],
        "KeyCount": len(contents) + len(common_prefixes or []),
        "IsTruncated": truncated,
        "NextContinuationToken": next_cursor,
        "ResponseMetadata": {
            "HTTPStatusCode": 200,
            "RequestId": "request-list",
            "HostId": "extended-list",
        },
    }
    if encoding_type is not None:
        response["EncodingType"] = encoding_type
    return response


def _request(
    session: Session,
    project_id: int,
    *,
    operation: str,
    input_json: dict[str, Any],
    progress_callback: Any = None,
) -> ActionConnectorRequest:
    stored = (
        AuthRepository(session)
        .store_credential(
            attach_project_id=project_id,
            provider_key="aws-s3",
            auth_method_key="aws-access-key",
            display_name=f"S3 {operation} {id(input_json)}",
            fields={
                "access_key_id": "AKIAEXPLICIT12345678",
                "secret_access_key": "s3-secret",
                "session_token": "s3-session-token",
                "bucket": "stackos-fixture",
                "region": "us-west-2",
            },
        )
        .data
    )
    credential = asyncio.run(
        AuthRepository(session).resolve_for_execution(
            project_id=project_id,
            provider_key="aws-s3",
            credential_ref=stored.credential_ref,
            operation=operation,
        )
    )
    return ActionConnectorRequest(
        project_id=project_id,
        plugin_slug="utils",
        action_key=f"s3.{operation}",
        action_ref=f"utils.s3.{operation}",
        provider_key="aws-s3",
        operation=operation,
        input_json=input_json,
        config_json={},
        credential=credential,
        progress_callback=progress_callback,
    )


def _patch_client(
    monkeypatch: pytest.MonkeyPatch,
    client: _FakeS3,
) -> list[dict[str, Any]]:
    import stackos.actions.s3 as s3_module

    factory_calls: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> _FakeS3:
        factory_calls.append(kwargs)
        return client

    monkeypatch.setattr(s3_module, "create_s3_client", factory)
    return factory_calls


def test_s3_connector_validation_is_provider_specific() -> None:
    connector = S3ActionConnector()
    request = ActionConnectorRequest(
        project_id=1,
        plugin_slug="utils",
        action_key="s3.directory.list",
        action_ref="utils.s3.directory.list",
        provider_key="aws-s3",
        operation="directory.list",
        input_json={"page_size": 1001, "prefix": "safe\nunsafe"},
        config_json={},
    )

    issues = connector.validate(request)

    assert {(item.path, item.code) for item in issues} == {
        ("$.page_size", "range"),
        ("$.prefix", "unsafe"),
    }

    download = ActionConnectorRequest(
        project_id=1,
        plugin_slug="utils",
        action_key="s3.file.download",
        action_ref="utils.s3.file.download",
        provider_key="aws-s3",
        operation="file.download",
        input_json={
            "items": [
                {
                    "remote_key": "reports/",
                    "remote_kind": "guess",
                    "local_path": "/tmp/reports",
                }
            ],
            "conflict_policy": "overwrite",
            "error_policy": "continue",
        },
        config_json={},
    )
    assert any(item.path == "$.items[0].remote_kind" for item in connector.validate(download))

    marker = ActionConnectorRequest(
        project_id=1,
        plugin_slug="utils",
        action_key="s3.directory.create",
        action_ref="utils.s3.directory.create",
        provider_key="aws-s3",
        operation="directory.create",
        input_json={"prefix": "a" * 1024},
        config_json={},
    )
    assert {(item.path, item.code) for item in connector.validate(marker)} == {
        ("$.prefix", "max_length")
    }


def test_s3_exact_soap_object_key_is_rejected_but_list_prefix_is_allowed() -> None:
    connector = S3ActionConnector()
    delete = ActionConnectorRequest(
        project_id=1,
        plugin_slug="utils",
        action_key="s3.file.delete",
        action_ref="utils.s3.file.delete",
        provider_key="aws-s3",
        operation="file.delete",
        input_json={"key": "soap"},
        config_json={},
    )
    listing = ActionConnectorRequest(
        project_id=1,
        plugin_slug="utils",
        action_key="s3.directory.list",
        action_ref="utils.s3.directory.list",
        provider_key="aws-s3",
        operation="directory.list",
        input_json={"prefix": "soap", "delimiter": "/", "page_size": 1000},
        config_json={},
    )

    assert {(item.path, item.code) for item in connector.validate(delete)} == {
        ("$.key", "unsupported")
    }
    assert connector.validate(listing) == []


def test_s3_list_marks_exact_soap_object_key_unsafe(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeS3()
    client.list_responses = [_list_response(contents=[{"Key": "soap", "Size": 4}])]
    _patch_client(monkeypatch, client)
    request = _request(
        session,
        project_id,
        operation="directory.list",
        input_json={"prefix": "", "delimiter": "/", "page_size": 1000},
    )

    result = asyncio.run(S3ActionConnector().execute(request))

    assert result.output_json["objects"][0]["safe_to_use"] is False
    assert result.output_json["objects"][0]["unsafe_reason"] == "unsupported"


def test_s3_upload_rejects_exact_soap_key_before_provider_call(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _FakeS3()
    _patch_client(monkeypatch, client)
    source = tmp_path / "soap.txt"
    source.write_bytes(b"soap")
    request = _request(
        session,
        project_id,
        operation="file.upload",
        input_json={
            "items": [{"local_path": str(source), "destination_key": "soap"}],
            "conflict_policy": "fail",
            "error_policy": "stop",
            "follow_symlinks": False,
        },
    )

    with pytest.raises(ActionConnectorError) as excinfo:
        asyncio.run(S3ActionConnector().execute(request))

    assert client.calls == []
    assert excinfo.value.output_json["failed"][0]["reason_code"] == "validation_error"


def test_s3_list_returns_one_bounded_page_and_opaque_cursor(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeS3()
    client.list_responses = [
        _list_response(
            contents=[
                {
                    "Key": "reports/a%20report.csv",
                    "Size": 42,
                    "ETag": '"etag-a"',
                    "StorageClass": "STANDARD",
                },
                {
                    "Key": "reports/s3-secret.txt",
                    "Size": 0,
                },
            ],
            common_prefixes=["reports/archive%20old/"],
            truncated=True,
            next_cursor="opaque-next-token",
        )
    ]
    _patch_client(monkeypatch, client)
    request = _request(
        session,
        project_id,
        operation="directory.list",
        input_json={
            "prefix": "reports/",
            "delimiter": "/",
            "page_size": 25,
            "cursor": "opaque-input-token",
        },
    )

    result = asyncio.run(S3ActionConnector().execute(request))

    assert client.calls == [
        (
            "list_objects_v2",
            {
                "Bucket": "stackos-fixture",
                "Prefix": "reports/",
                "MaxKeys": 25,
                "EncodingType": "url",
                "Delimiter": "/",
                "ContinuationToken": "opaque-input-token",
            },
        )
    ]
    assert result.output_json["objects"] == [
        {
            "key": "reports/a report.csv",
            "key_redacted": False,
            "safe_to_use": True,
            "size": 42,
            "etag": '"etag-a"',
            "last_modified": None,
            "storage_class": "STANDARD",
            "checksum_algorithms": [],
        },
        {
            "key": "reports/[REDACTED].txt",
            "key_redacted": True,
            "safe_to_use": False,
            "size": 0,
            "etag": None,
            "last_modified": None,
            "storage_class": None,
            "checksum_algorithms": [],
        },
    ]
    assert result.output_json["common_prefixes"] == [
        {
            "prefix": "reports/archive old/",
            "prefix_redacted": False,
            "safe_to_use": True,
        }
    ]
    assert result.output_json["next_cursor"] == "opaque-next-token"
    assert result.output_json["request_id"] == "request-list"


def test_s3_list_preserves_empty_page_without_inventing_directory_state(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeS3()
    client.list_responses = [_list_response(contents=[])]
    _patch_client(monkeypatch, client)
    request = _request(
        session,
        project_id,
        operation="directory.list",
        input_json={
            "prefix": "empty/",
            "delimiter": "/",
            "page_size": 1000,
        },
    )

    result = asyncio.run(S3ActionConnector().execute(request))

    assert result.output_json["objects"] == []
    assert result.output_json["common_prefixes"] == []
    assert result.output_json["key_count"] == 0
    assert result.output_json["is_truncated"] is False
    assert result.output_json["next_cursor"] is None
    assert result.output_json["status"] == "success"


def test_s3_small_and_directory_uploads_use_conditional_puts_and_markers(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _FakeS3()
    factory_calls = _patch_client(monkeypatch, client)
    source = tmp_path / "source"
    source.mkdir()
    (source / "empty").mkdir()
    (source / "file.txt").write_bytes(b"data")
    (source / "ignored-link").symlink_to(source / "file.txt")
    request = _request(
        session,
        project_id,
        operation="file.upload",
        input_json={
            "items": [
                {
                    "local_path": str(source),
                    "destination_key": "uploads",
                }
            ],
            "conflict_policy": "fail",
            "error_policy": "stop",
            "follow_symlinks": False,
        },
    )

    result = asyncio.run(S3ActionConnector().execute(request))

    assert factory_calls[0]["total_max_attempts"] == 1
    put_calls = [kwargs for name, kwargs in client.calls if name == "put_object"]
    assert [(item["Key"], item["Body"]) for item in put_calls] == [
        ("uploads/empty/", b""),
        ("uploads/file.txt", b"data"),
    ]
    assert all(item["IfNoneMatch"] == "*" for item in put_calls)
    assert all("ExpectedBucketOwner" not in item for item in put_calls)
    assert result.output_json["status"] == "success"
    assert result.output_json["completed_count"] == 2
    assert result.output_json["skipped_count"] == 1
    assert result.output_json["skipped"][0]["reason_code"] == "symlink_skipped"
    assert result.output_json["bytes_transferred"] == 4


def test_s3_upload_continue_records_missing_mapping_and_uploads_later_item(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _FakeS3()
    _patch_client(monkeypatch, client)
    source = tmp_path / "valid.txt"
    source.write_bytes(b"valid")
    request = _request(
        session,
        project_id,
        operation="file.upload",
        input_json={
            "items": [
                {
                    "local_path": str(tmp_path / "missing.txt"),
                    "destination_key": "uploads/missing.txt",
                },
                {
                    "local_path": str(source),
                    "destination_key": "uploads/valid.txt",
                },
            ],
            "conflict_policy": "fail",
            "error_policy": "continue",
            "follow_symlinks": False,
        },
    )

    result = asyncio.run(S3ActionConnector().execute(request))

    assert [name for name, _kwargs in client.calls] == ["put_object"]
    assert client.calls[0][1]["Key"] == "uploads/valid.txt"
    assert result.output_json["status"] == "partial"
    assert result.output_json["completed_count"] == 1
    assert result.output_json["failed_count"] == 1
    assert result.output_json["failed"][0]["source_path"].endswith("missing.txt")
    assert result.output_json["failed"][0]["reason_code"] == "filesystem_error"


def test_s3_upload_global_object_bound_fails_before_any_provider_mutation(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import stackos.actions.s3 as s3_module

    client = _FakeS3()
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(s3_module, "_UPLOAD_OBJECT_LIMIT", 1)
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    request = _request(
        session,
        project_id,
        operation="file.upload",
        input_json={
            "items": [
                {"local_path": str(first), "destination_key": "uploads/first.txt"},
                {"local_path": str(second), "destination_key": "uploads/second.txt"},
            ],
            "conflict_policy": "fail",
            "error_policy": "continue",
            "follow_symlinks": False,
        },
    )

    with pytest.raises(ActionConnectorError) as excinfo:
        asyncio.run(S3ActionConnector().execute(request))

    assert client.calls == []
    assert excinfo.value.output_json["failed_count"] == 1
    assert excinfo.value.output_json["failed"][0]["reason_code"] == "validation_error"


def test_s3_upload_followed_symlink_cycle_fails_before_provider_mutation(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _FakeS3()
    _patch_client(monkeypatch, client)
    source = tmp_path / "tree"
    source.mkdir()
    (source / "loop").symlink_to(source, target_is_directory=True)
    request = _request(
        session,
        project_id,
        operation="file.upload",
        input_json={
            "items": [{"local_path": str(source), "destination_key": "uploads"}],
            "conflict_policy": "fail",
            "error_policy": "stop",
            "follow_symlinks": True,
        },
    )

    with pytest.raises(ActionConnectorError) as excinfo:
        asyncio.run(S3ActionConnector().execute(request))

    assert client.calls == []
    assert excinfo.value.output_json["failed"][0]["reason_code"] == "validation_error"
    assert "cycle" not in json.dumps(excinfo.value.output_json).lower()


def test_s3_upload_rejects_an_oversized_derived_key_before_mutation(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _FakeS3()
    _patch_client(monkeypatch, client)
    source = tmp_path / "tree"
    source.mkdir()
    (source / ("b" * 30)).write_text("bounded", encoding="utf-8")
    request = _request(
        session,
        project_id,
        operation="file.upload",
        input_json={
            "items": [
                {
                    "local_path": str(source),
                    "destination_key": "a" * 1000,
                }
            ],
            "conflict_policy": "fail",
            "error_policy": "stop",
            "follow_symlinks": False,
        },
    )

    with pytest.raises(ActionConnectorError) as excinfo:
        asyncio.run(S3ActionConnector().execute(request))

    assert client.calls == []
    assert excinfo.value.output_json["failed"][0]["reason_code"] == "validation_error"


def test_s3_multipart_upload_conditions_completion_and_reports_parts(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import stackos.actions.s3 as s3_module

    client = _FakeS3()
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(s3_module, "_MULTIPART_THRESHOLD", 5)
    monkeypatch.setattr(s3_module, "_MULTIPART_PART_SIZE", 3)
    source = tmp_path / "large.bin"
    source.write_bytes(b"abcdefgh")
    request = _request(
        session,
        project_id,
        operation="file.upload",
        input_json={
            "items": [
                {
                    "local_path": str(source),
                    "destination_key": "large.bin",
                }
            ],
            "conflict_policy": "fail",
            "error_policy": "stop",
            "follow_symlinks": False,
        },
    )

    result = asyncio.run(S3ActionConnector().execute(request))

    names = [name for name, _kwargs in client.calls]
    assert names == [
        "create_multipart_upload",
        "upload_part",
        "upload_part",
        "upload_part",
        "complete_multipart_upload",
    ]
    create = client.calls[0][1]
    assert "IfNoneMatch" not in create
    parts = [kwargs for name, kwargs in client.calls if name == "upload_part"]
    assert [item["Body"] for item in parts] == [b"abc", b"def", b"gh"]
    complete = client.calls[-1][1]
    assert complete["IfNoneMatch"] == "*"
    assert complete["MultipartUpload"] == {
        "Parts": [
            {"ETag": '"part-1"', "PartNumber": 1},
            {"ETag": '"part-2"', "PartNumber": 2},
            {"ETag": '"part-3"', "PartNumber": 3},
        ]
    }
    assert all("ExpectedBucketOwner" not in kwargs for _name, kwargs in client.calls)
    assert result.output_json["completed"][0]["multipart"] is True
    assert result.output_json["completed"][0]["part_count"] == 3
    assert result.output_json["bytes_transferred"] == 8
    assert result.output_json["request_count"] == 5


def test_s3_multipart_upload_skip_aborts_after_destination_race(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import stackos.actions.s3 as s3_module

    client = _FakeS3()
    client.operation_outcomes["complete_multipart_upload"] = [
        ClientError(
            {
                "Error": {
                    "Code": "PreconditionFailed",
                    "Message": "destination appeared with s3-secret",
                },
                "ResponseMetadata": {
                    "HTTPStatusCode": 412,
                    "RequestId": "request-complete-conflict",
                },
            },
            "CompleteMultipartUpload",
        )
    ]
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(s3_module, "_MULTIPART_THRESHOLD", 1)
    monkeypatch.setattr(s3_module, "_MULTIPART_PART_SIZE", 5)
    source = tmp_path / "large.bin"
    source.write_bytes(b"abcdefghij")
    request = _request(
        session,
        project_id,
        operation="file.upload",
        input_json={
            "items": [
                {
                    "local_path": str(source),
                    "destination_key": "large.bin",
                }
            ],
            "conflict_policy": "skip",
            "error_policy": "stop",
            "follow_symlinks": False,
        },
    )

    result = asyncio.run(S3ActionConnector().execute(request))

    assert [name for name, _kwargs in client.calls] == [
        "create_multipart_upload",
        "upload_part",
        "upload_part",
        "complete_multipart_upload",
        "abort_multipart_upload",
    ]
    complete = client.calls[3][1]
    assert complete["IfNoneMatch"] == "*"
    assert result.output_json["status"] == "success"
    assert result.output_json["completed"] == []
    assert result.output_json["skipped"][0]["reason_code"] == "destination_exists"
    assert result.output_json["skipped"][0]["abort"]["status"] == "success"
    assert result.output_json["cleanup_unverified"] is True
    assert "s3-secret" not in json.dumps(result.output_json)


def test_s3_single_put_skip_is_race_safe_without_preflight(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _FakeS3()
    client.operation_outcomes["put_object"] = [
        ClientError(
            {
                "Error": {
                    "Code": "PreconditionFailed",
                    "Message": "destination exists s3-secret",
                },
                "ResponseMetadata": {
                    "HTTPStatusCode": 412,
                    "RequestId": "request-precondition",
                },
            },
            "PutObject",
        )
    ]
    _patch_client(monkeypatch, client)
    source = tmp_path / "small.txt"
    source.write_bytes(b"new")
    request = _request(
        session,
        project_id,
        operation="file.upload",
        input_json={
            "items": [
                {
                    "local_path": str(source),
                    "destination_key": "small.txt",
                }
            ],
            "conflict_policy": "skip",
            "error_policy": "stop",
            "follow_symlinks": False,
        },
    )

    result = asyncio.run(S3ActionConnector().execute(request))

    assert [name for name, _kwargs in client.calls] == ["put_object"]
    assert client.calls[0][1]["IfNoneMatch"] == "*"
    assert result.output_json["completed_count"] == 0
    assert result.output_json["skipped_count"] == 1
    assert result.output_json["skipped"][0]["reason_code"] == "destination_exists"
    assert "s3-secret" not in json.dumps(result.model_dump(mode="json"))


def test_s3_known_multipart_failure_aborts_and_reports_cleanup(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import stackos.actions.s3 as s3_module

    client = _FakeS3()
    client.operation_outcomes["upload_part"] = [
        ClientError(
            {
                "Error": {
                    "Code": "AccessDenied",
                    "Message": "rejected s3-secret",
                },
                "ResponseMetadata": {
                    "HTTPStatusCode": 403,
                    "RequestId": "request-part-failed",
                },
            },
            "UploadPart",
        )
    ]
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(s3_module, "_MULTIPART_THRESHOLD", 1)
    monkeypatch.setattr(s3_module, "_MULTIPART_PART_SIZE", 5)
    source = tmp_path / "large.bin"
    source.write_bytes(b"abcdefghij")
    request = _request(
        session,
        project_id,
        operation="file.upload",
        input_json={
            "items": [
                {
                    "local_path": str(source),
                    "destination_key": "large.bin",
                }
            ],
            "conflict_policy": "overwrite",
            "error_policy": "stop",
            "follow_symlinks": False,
        },
    )

    with pytest.raises(ActionConnectorError) as excinfo:
        asyncio.run(S3ActionConnector().execute(request))

    assert [name for name, _kwargs in client.calls] == [
        "create_multipart_upload",
        "upload_part",
        "abort_multipart_upload",
    ]
    failure = excinfo.value.output_json["failed"][0]
    assert failure["aws_error_code"] == "AccessDenied"
    assert excinfo.value.output_json["provider_status_code"] == 403
    assert excinfo.value.output_json["provider_error"] == {"code": "AccessDenied"}
    assert failure["abort"]["status"] == "success"
    assert failure["completion_started"] is False
    assert failure["outcome_unknown"] is False
    assert failure["retry_safe"] is False
    assert excinfo.value.output_json["cleanup_unverified"] is True
    assert "s3-secret" not in json.dumps(excinfo.value.output_json)


def test_s3_multipart_upload_part_transport_failure_keeps_cleanup_unverified(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import stackos.actions.s3 as s3_module

    client = _FakeS3()
    client.operation_outcomes["upload_part"] = [
        EndpointConnectionError(endpoint_url="https://s3.example/s3-secret")
    ]
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(s3_module, "_MULTIPART_THRESHOLD", 1)
    monkeypatch.setattr(s3_module, "_MULTIPART_PART_SIZE", 5)
    source = tmp_path / "large.bin"
    source.write_bytes(b"abcdefghij")
    request = _request(
        session,
        project_id,
        operation="file.upload",
        input_json={
            "items": [{"local_path": str(source), "destination_key": "large.bin"}],
            "conflict_policy": "overwrite",
            "error_policy": "stop",
            "follow_symlinks": False,
        },
    )

    with pytest.raises(ActionConnectorError) as excinfo:
        asyncio.run(S3ActionConnector().execute(request))

    assert [name for name, _kwargs in client.calls] == [
        "create_multipart_upload",
        "upload_part",
        "abort_multipart_upload",
    ]
    failure = excinfo.value.output_json["failed"][0]
    assert failure["completion_started"] is False
    assert failure["outcome_unknown"] is True
    assert failure["cleanup_unverified"] is True
    assert failure["abort"]["status"] == "success"
    assert failure["abort"]["cleanup_unverified"] is True
    assert "ListParts" in failure["abort"]["reconciliation"]
    assert excinfo.value.output_json["outcome_unknown"] is True
    assert excinfo.value.output_json["cleanup_unverified"] is True
    assert "s3-secret" not in json.dumps(excinfo.value.output_json)


def test_s3_ambiguous_multipart_completion_is_not_aborted_or_retried(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import stackos.actions.s3 as s3_module

    client = _FakeS3()
    client.operation_outcomes["complete_multipart_upload"] = [
        EndpointConnectionError(endpoint_url="https://s3.example/s3-secret")
    ]
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(s3_module, "_MULTIPART_THRESHOLD", 1)
    monkeypatch.setattr(s3_module, "_MULTIPART_PART_SIZE", 5)
    source = tmp_path / "large.bin"
    source.write_bytes(b"abcdefghij")
    request = _request(
        session,
        project_id,
        operation="file.upload",
        input_json={
            "items": [
                {
                    "local_path": str(source),
                    "destination_key": "large.bin",
                }
            ],
            "conflict_policy": "overwrite",
            "error_policy": "stop",
            "follow_symlinks": False,
        },
    )

    with pytest.raises(ActionConnectorError) as excinfo:
        asyncio.run(S3ActionConnector().execute(request))

    names = [name for name, _kwargs in client.calls]
    assert names == [
        "create_multipart_upload",
        "upload_part",
        "upload_part",
        "complete_multipart_upload",
    ]
    assert "abort_multipart_upload" not in names
    failure = excinfo.value.output_json["failed"][0]
    assert failure["completion_started"] is True
    assert failure["outcome_unknown"] is True
    assert failure["retry_safe"] is False
    assert "Inspect the destination object" in failure["reconciliation"]
    assert "s3-secret" not in json.dumps(excinfo.value.output_json)


def test_s3_file_delete_heads_then_conditionally_deletes_current_object(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeS3()
    factory_calls = _patch_client(monkeypatch, client)
    request = _request(
        session,
        project_id,
        operation="file.delete",
        input_json={"key": "reports/current.csv"},
    )

    result = asyncio.run(S3ActionConnector().execute(request))

    assert [item["total_max_attempts"] for item in factory_calls] == [3, 1]
    assert client.calls == [
        (
            "head_object",
            {
                "Bucket": "stackos-fixture",
                "Key": "reports/current.csv",
            },
        ),
        (
            "delete_object",
            {
                "Bucket": "stackos-fixture",
                "Key": "reports/current.csv",
                "IfMatch": '"head-etag"',
            },
        ),
    ]
    assert "VersionId" not in client.calls[1][1]
    assert result.output_json["delete_marker"] is True
    assert result.output_json["version_id"] == "delete-marker-version"
    assert result.output_json["observed_version_id"] == "head-version"
    assert result.output_json["historical_versions_purged"] is False


def test_s3_file_delete_rejects_missing_or_changed_identity_before_mutation(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeS3()
    _patch_client(monkeypatch, client)
    request = _request(
        session,
        project_id,
        operation="file.delete",
        input_json={"key": "reports/current.csv", "if_match": '"older-etag"'},
    )

    with pytest.raises(ActionConnectorError) as excinfo:
        asyncio.run(S3ActionConnector().execute(request))

    assert [name for name, _kwargs in client.calls] == ["head_object"]
    assert excinfo.value.output_json["reason_code"] == "etag_mismatch"
    assert excinfo.value.output_json["outcome_unknown"] is False


def test_s3_file_delete_reports_missing_current_object_without_mutation(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeS3()
    client.operation_outcomes["head_object"] = [
        ClientError(
            {
                "Error": {"Code": "NoSuchKey", "Message": "missing s3-secret"},
                "ResponseMetadata": {
                    "HTTPStatusCode": 404,
                    "RequestId": "request-head-missing",
                },
            },
            "HeadObject",
        )
    ]
    _patch_client(monkeypatch, client)
    request = _request(
        session,
        project_id,
        operation="file.delete",
        input_json={"key": "reports/missing.csv"},
    )

    with pytest.raises(ActionConnectorError) as excinfo:
        asyncio.run(S3ActionConnector().execute(request))

    assert [name for name, _kwargs in client.calls] == ["head_object"]
    output = excinfo.value.output_json
    assert output["status"] == "failed"
    assert output["aws_error_code"] == "NoSuchKey"
    assert output["provider_status_code"] == 404
    assert output["provider_error"] == {"code": "NoSuchKey"}
    assert output["retry_safe"] is True
    assert "s3-secret" not in json.dumps(output)


def test_s3_directory_create_writes_only_a_conditional_empty_marker(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeS3()
    factory_calls = _patch_client(monkeypatch, client)
    request = _request(
        session,
        project_id,
        operation="directory.create",
        input_json={"prefix": "reports"},
    )

    result = asyncio.run(S3ActionConnector().execute(request))

    assert factory_calls[0]["total_max_attempts"] == 1
    assert client.calls == [
        (
            "put_object",
            {
                "Bucket": "stackos-fixture",
                "Key": "reports/",
                "Body": b"",
                "IfNoneMatch": "*",
            },
        )
    ]
    assert result.output_json["marker_created"] is True
    assert result.output_json["marker_key"] == "reports/"
    assert result.output_json["historical_versions_purged"] is False


def test_s3_nonrecursive_directory_delete_rejects_children_before_mutation(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeS3()
    client.list_responses = [
        _list_response(
            contents=[
                {"Key": "reports/", "Size": 0, "ETag": '"marker"'},
                {"Key": "reports/a.txt", "Size": 1, "ETag": '"a"'},
            ]
        )
    ]
    _patch_client(monkeypatch, client)
    request = _request(
        session,
        project_id,
        operation="directory.delete",
        input_json={"prefix": "reports", "recursive": False, "max_objects": 10},
    )

    with pytest.raises(ActionConnectorError) as excinfo:
        asyncio.run(S3ActionConnector().execute(request))

    assert [name for name, _kwargs in client.calls] == ["list_objects_v2"]
    assert excinfo.value.output_json["reason_code"] == "prefix_not_empty"
    assert excinfo.value.output_json["mutation_started"] is False


def test_s3_nonrecursive_directory_delete_conditionally_removes_only_marker(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeS3()
    client.list_responses = [
        _list_response(contents=[{"Key": "reports/", "Size": 0, "ETag": '"marker"'}])
    ]
    _patch_client(monkeypatch, client)
    request = _request(
        session,
        project_id,
        operation="directory.delete",
        input_json={"prefix": "reports", "recursive": False, "max_objects": 10},
    )

    result = asyncio.run(S3ActionConnector().execute(request))

    assert [name for name, _kwargs in client.calls] == [
        "list_objects_v2",
        "delete_object",
    ]
    assert client.calls[1][1]["IfMatch"] == '"marker"'
    assert result.output_json["marker_deleted"] is True
    assert result.output_json["historical_versions_purged"] is False


def test_s3_recursive_directory_delete_uses_conditional_batches_and_reconciles(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 26, tzinfo=UTC)
    client = _FakeS3()
    client.list_responses = [
        _list_response(
            contents=[
                {
                    "Key": "reports/",
                    "Size": 0,
                    "ETag": '"marker"',
                    "LastModified": now,
                },
                {
                    "Key": "reports/a.txt",
                    "Size": 1,
                    "ETag": '"a"',
                    "LastModified": now,
                },
            ]
        ),
        _list_response(contents=[]),
    ]
    factory_calls = _patch_client(monkeypatch, client)
    snapshots: list[dict[str, Any]] = []
    request = _request(
        session,
        project_id,
        operation="directory.delete",
        input_json={"prefix": "reports", "recursive": True, "max_objects": 10},
        progress_callback=snapshots.append,
    )

    result = asyncio.run(S3ActionConnector().execute(request))

    assert [item["total_max_attempts"] for item in factory_calls] == [3, 1]
    assert [name for name, _kwargs in client.calls] == [
        "list_objects_v2",
        "delete_objects",
        "list_objects_v2",
    ]
    delete_request = client.calls[1][1]
    assert "ExpectedBucketOwner" not in delete_request
    assert delete_request["Delete"]["Quiet"] is False
    assert delete_request["Delete"]["Objects"] == [
        {
            "Key": "reports/",
            "ETag": '"marker"',
        },
        {
            "Key": "reports/a.txt",
            "ETag": '"a"',
        },
    ]
    assert all("VersionId" not in item for item in delete_request["Delete"]["Objects"])
    assert result.output_json["status"] == "success"
    assert result.output_json["planned_count"] == 2
    assert result.output_json["deleted_count"] == 2
    assert result.output_json["remaining_count"] == 0
    assert result.output_json["historical_versions_purged"] is False
    assert snapshots[-1]["phase"] == "complete"


def test_s3_recursive_directory_delete_enforces_bound_before_mutation(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeS3()
    client.list_responses = [
        _list_response(
            contents=[
                {"Key": "reports/a.txt", "Size": 1, "ETag": '"a"'},
                {"Key": "reports/b.txt", "Size": 1, "ETag": '"b"'},
            ]
        )
    ]
    _patch_client(monkeypatch, client)
    request = _request(
        session,
        project_id,
        operation="directory.delete",
        input_json={"prefix": "reports", "recursive": True, "max_objects": 1},
    )

    with pytest.raises(ActionConnectorError) as excinfo:
        asyncio.run(S3ActionConnector().execute(request))

    assert [name for name, _kwargs in client.calls] == ["list_objects_v2"]
    assert excinfo.value.output_json["reason_code"] == "object_bound_exceeded"
    assert excinfo.value.output_json["mutation_started"] is False


def test_s3_recursive_directory_delete_preserves_partial_errors_and_remaining_keys(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeS3()
    client.list_responses = [
        _list_response(
            contents=[
                {"Key": "reports/a.txt", "Size": 1, "ETag": '"a"'},
                {"Key": "reports/b.txt", "Size": 1, "ETag": '"b"'},
            ]
        ),
        _list_response(contents=[{"Key": "reports/b.txt", "Size": 1, "ETag": '"b"'}]),
    ]
    client.operation_outcomes["delete_objects"] = [
        {
            "Deleted": [
                {
                    "Key": "reports/a.txt",
                    "DeleteMarker": True,
                    "DeleteMarkerVersionId": "marker-a",
                }
            ],
            "Errors": [
                {
                    "Key": "reports/b.txt",
                    "Code": "AccessDenied",
                    "Message": "s3-secret",
                }
            ],
            "ResponseMetadata": {
                "HTTPStatusCode": 200,
                "RequestId": "request-partial-delete",
            },
        }
    ]
    _patch_client(monkeypatch, client)
    request = _request(
        session,
        project_id,
        operation="directory.delete",
        input_json={"prefix": "reports", "recursive": True, "max_objects": 10},
    )

    with pytest.raises(ActionConnectorError) as excinfo:
        asyncio.run(S3ActionConnector().execute(request))

    output = excinfo.value.output_json
    assert output["status"] == "partial"
    assert output["deleted_count"] == 1
    assert output["failed_count"] == 1
    assert output["remaining_keys"] == ["reports/b.txt"]
    assert output["errors"][0]["aws_error_code"] == "AccessDenied"
    assert output["provider_status_code"] == 200
    assert output["provider_error"] == {"code": "AccessDenied"}
    assert output["retry_safe"] is False
    assert output["historical_versions_purged"] is False
    assert "s3-secret" not in json.dumps(output)


def test_s3_move_rejects_prefixes_and_same_key_during_validation() -> None:
    request = ActionConnectorRequest(
        project_id=1,
        plugin_slug="utils",
        action_key="s3.path.rename",
        action_ref="utils.s3.path.rename",
        provider_key="aws-s3",
        operation="path.rename",
        input_json={
            "source_key": "reports/",
            "destination_key": "reports/",
            "conflict_policy": "overwrite",
        },
        config_json={},
    )

    issues = S3ActionConnector().validate(request)

    assert {item.code for item in issues} == {"unsupported", "conflict"}
    assert {item.path for item in issues} == {
        "$.source_key",
        "$.destination_key",
    }


def test_s3_single_object_move_conditions_copy_and_source_delete(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeS3()
    factory_calls = _patch_client(monkeypatch, client)
    request = _request(
        session,
        project_id,
        operation="path.rename",
        input_json={
            "source_key": "reports/source.csv",
            "destination_key": "archive/source.csv",
            "conflict_policy": "fail",
        },
    )

    result = asyncio.run(S3ActionConnector().execute(request))

    assert [item["total_max_attempts"] for item in factory_calls] == [3, 1]
    assert [name for name, _kwargs in client.calls] == [
        "head_object",
        "copy_object",
        "delete_object",
    ]
    copy_request = client.calls[1][1]
    assert copy_request == {
        "Bucket": "stackos-fixture",
        "Key": "archive/source.csv",
        "CopySource": {
            "Bucket": "stackos-fixture",
            "Key": "reports/source.csv",
            "VersionId": "head-version",
        },
        "CopySourceIfMatch": '"head-etag"',
        "IfNoneMatch": "*",
    }
    delete_request = client.calls[2][1]
    assert delete_request["IfMatch"] == '"head-etag"'
    assert "VersionId" not in delete_request
    assert result.output_json["status"] == "success"
    assert result.output_json["non_atomic"] is True
    assert result.output_json["copy_completed"] is True
    assert result.output_json["source_delete_completed"] is True
    assert result.output_json["destination_version_id"] == "destination-version"
    assert result.output_json["historical_versions_purged"] is False


def test_s3_move_skip_keeps_source_when_destination_condition_fails(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeS3()
    client.operation_outcomes["copy_object"] = [
        ClientError(
            {
                "Error": {
                    "Code": "PreconditionFailed",
                    "Message": "destination exists s3-secret",
                },
                "ResponseMetadata": {
                    "HTTPStatusCode": 412,
                    "RequestId": "request-copy-condition",
                },
            },
            "CopyObject",
        )
    ]
    _patch_client(monkeypatch, client)
    request = _request(
        session,
        project_id,
        operation="path.rename",
        input_json={
            "source_key": "reports/source.csv",
            "destination_key": "archive/source.csv",
            "conflict_policy": "skip",
        },
    )

    result = asyncio.run(S3ActionConnector().execute(request))

    assert [name for name, _kwargs in client.calls] == ["head_object", "copy_object"]
    assert result.output_json["status"] == "skipped"
    assert result.output_json["copy_completed"] is False
    assert result.output_json["source_delete_completed"] is False
    assert result.output_json["retry_safe"] is True
    assert "s3-secret" not in json.dumps(result.model_dump(mode="json"))


def test_s3_move_copy_success_delete_failure_is_explicit_partial_without_rollback(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeS3()
    client.operation_outcomes["delete_object"] = [
        ClientError(
            {
                "Error": {
                    "Code": "PreconditionFailed",
                    "Message": "source changed s3-secret",
                },
                "ResponseMetadata": {
                    "HTTPStatusCode": 412,
                    "RequestId": "request-source-condition",
                },
            },
            "DeleteObject",
        )
    ]
    _patch_client(monkeypatch, client)
    request = _request(
        session,
        project_id,
        operation="path.rename",
        input_json={
            "source_key": "reports/source.csv",
            "destination_key": "archive/source.csv",
            "conflict_policy": "overwrite",
        },
    )

    with pytest.raises(ActionConnectorError) as excinfo:
        asyncio.run(S3ActionConnector().execute(request))

    assert [name for name, _kwargs in client.calls] == [
        "head_object",
        "copy_object",
        "delete_object",
    ]
    output = excinfo.value.output_json
    assert output["status"] == "partial"
    assert output["copy_completed"] is True
    assert output["source_delete_completed"] is False
    assert output["destination_etag"] == '"destination-etag"'
    assert output["retry_safe"] is False
    assert "Inspect both exact keys" in output["reconciliation"]
    assert "s3-secret" not in json.dumps(output)


def test_s3_multipart_move_uses_conditional_part_copy_and_completion(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.s3 as s3_module

    client = _FakeS3()
    client.operation_outcomes["head_object"] = [
        {
            "ETag": '"head-etag"',
            "VersionId": "head-version",
            "ContentLength": 8,
            "ResponseMetadata": {
                "HTTPStatusCode": 200,
                "RequestId": "request-head",
            },
        }
    ]
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(s3_module, "_SINGLE_COPY_MAX_BYTES", 5)
    monkeypatch.setattr(s3_module, "_COPY_PART_SIZE", 3)
    snapshots: list[dict[str, Any]] = []
    request = _request(
        session,
        project_id,
        operation="path.rename",
        input_json={
            "source_key": "reports/large.bin",
            "destination_key": "archive/large.bin",
            "conflict_policy": "fail",
        },
        progress_callback=snapshots.append,
    )

    result = asyncio.run(S3ActionConnector().execute(request))

    assert [name for name, _kwargs in client.calls] == [
        "head_object",
        "create_multipart_upload",
        "upload_part_copy",
        "upload_part_copy",
        "upload_part_copy",
        "complete_multipart_upload",
        "delete_object",
    ]
    part_calls = [kwargs for name, kwargs in client.calls if name == "upload_part_copy"]
    assert [item["CopySourceRange"] for item in part_calls] == [
        "bytes=0-2",
        "bytes=3-5",
        "bytes=6-7",
    ]
    assert all(item["CopySourceIfMatch"] == '"head-etag"' for item in part_calls)
    assert all("ExpectedSourceBucketOwner" not in item for item in part_calls)
    complete = client.calls[-2][1]
    assert complete["IfNoneMatch"] == "*"
    assert complete["MultipartUpload"] == {
        "Parts": [
            {"ETag": '"copy-part-1"', "PartNumber": 1},
            {"ETag": '"copy-part-2"', "PartNumber": 2},
            {"ETag": '"copy-part-3"', "PartNumber": 3},
        ]
    }
    assert result.output_json["copy"]["multipart"] is True
    assert result.output_json["copy"]["part_count"] == 3
    assert result.output_json["request_count"] == 7
    assert snapshots[-1]["phase"] == "complete"


def test_s3_move_uses_multipart_copy_immediately_above_five_decimal_gigabytes(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.s3 as s3_module

    client = _FakeS3()
    client.operation_outcomes["head_object"] = [
        {
            "ETag": '"head-etag"',
            "ContentLength": 5_000_000_001,
            "ResponseMetadata": {
                "HTTPStatusCode": 200,
                "RequestId": "request-head",
            },
        }
    ]
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(s3_module, "_COPY_PART_SIZE", 5_000_000_001)
    request = _request(
        session,
        project_id,
        operation="path.rename",
        input_json={
            "source_key": "reports/large.bin",
            "destination_key": "archive/large.bin",
            "conflict_policy": "overwrite",
        },
    )

    result = asyncio.run(S3ActionConnector().execute(request))

    assert "copy_object" not in [name for name, _kwargs in client.calls]
    assert [name for name, _kwargs in client.calls] == [
        "head_object",
        "create_multipart_upload",
        "upload_part_copy",
        "complete_multipart_upload",
        "delete_object",
    ]
    assert client.calls[2][1]["CopySourceRange"] == "bytes=0-5000000000"
    assert result.output_json["copy"]["multipart"] is True


def test_s3_move_uses_single_copy_at_five_decimal_gigabytes(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeS3()
    client.operation_outcomes["head_object"] = [
        {
            "ETag": '"head-etag"',
            "ContentLength": 5_000_000_000,
            "ResponseMetadata": {
                "HTTPStatusCode": 200,
                "RequestId": "request-head",
            },
        }
    ]
    _patch_client(monkeypatch, client)
    request = _request(
        session,
        project_id,
        operation="path.rename",
        input_json={
            "source_key": "reports/large.bin",
            "destination_key": "archive/large.bin",
            "conflict_policy": "overwrite",
        },
    )

    result = asyncio.run(S3ActionConnector().execute(request))

    assert [name for name, _kwargs in client.calls] == [
        "head_object",
        "copy_object",
        "delete_object",
    ]
    assert result.output_json["copy"]["multipart"] is False


def test_s3_multipart_copy_part_transport_failure_preserves_abort_provider_receipt(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.s3 as s3_module

    client = _FakeS3()
    client.operation_outcomes["head_object"] = [
        {
            "ETag": '"head-etag"',
            "ContentLength": 8,
            "ResponseMetadata": {
                "HTTPStatusCode": 200,
                "RequestId": "request-head",
            },
        }
    ]
    client.operation_outcomes["upload_part_copy"] = [
        EndpointConnectionError(endpoint_url="https://s3.example/s3-secret")
    ]
    client.operation_outcomes["abort_multipart_upload"] = [
        ClientError(
            {
                "Error": {
                    "Code": "AccessDenied",
                    "Message": "abort denied s3-secret",
                },
                "ResponseMetadata": {
                    "HTTPStatusCode": 403,
                    "RequestId": "request-abort-denied",
                    "HostId": "extended-abort-denied",
                },
            },
            "AbortMultipartUpload",
        )
    ]
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(s3_module, "_SINGLE_COPY_MAX_BYTES", 5)
    monkeypatch.setattr(s3_module, "_COPY_PART_SIZE", 4)
    request = _request(
        session,
        project_id,
        operation="path.rename",
        input_json={
            "source_key": "reports/large.bin",
            "destination_key": "archive/large.bin",
            "conflict_policy": "overwrite",
        },
    )

    with pytest.raises(ActionConnectorError) as excinfo:
        asyncio.run(S3ActionConnector().execute(request))

    assert [name for name, _kwargs in client.calls] == [
        "head_object",
        "create_multipart_upload",
        "upload_part_copy",
        "abort_multipart_upload",
    ]
    output = excinfo.value.output_json
    assert output["outcome_unknown"] is True
    assert output["cleanup_unverified"] is True
    assert output["abort"]["status"] == "failed"
    assert output["abort"]["aws_error_code"] == "AccessDenied"
    assert output["abort"]["provider_status_code"] == 403
    assert output["abort"]["request_id"] == "request-abort-denied"
    assert "s3-secret" not in json.dumps(output)


def test_s3_ambiguous_multipart_copy_completion_does_not_abort_or_delete_source(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.actions.s3 as s3_module

    client = _FakeS3()
    client.operation_outcomes["head_object"] = [
        {
            "ETag": '"head-etag"',
            "VersionId": "head-version",
            "ContentLength": 8,
            "ResponseMetadata": {
                "HTTPStatusCode": 200,
                "RequestId": "request-head",
            },
        }
    ]
    client.operation_outcomes["complete_multipart_upload"] = [
        EndpointConnectionError(endpoint_url="https://s3.example/s3-secret")
    ]
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(s3_module, "_SINGLE_COPY_MAX_BYTES", 5)
    monkeypatch.setattr(s3_module, "_COPY_PART_SIZE", 4)
    request = _request(
        session,
        project_id,
        operation="path.rename",
        input_json={
            "source_key": "reports/large.bin",
            "destination_key": "archive/large.bin",
            "conflict_policy": "overwrite",
        },
    )

    with pytest.raises(ActionConnectorError) as excinfo:
        asyncio.run(S3ActionConnector().execute(request))

    names = [name for name, _kwargs in client.calls]
    assert names == [
        "head_object",
        "create_multipart_upload",
        "upload_part_copy",
        "upload_part_copy",
        "complete_multipart_upload",
    ]
    assert "abort_multipart_upload" not in names
    assert "delete_object" not in names
    output = excinfo.value.output_json
    assert output["copy_completed"] is False
    assert output["source_delete_completed"] is False
    assert output["outcome_unknown"] is True
    assert output["retry_safe"] is False
    assert "s3-secret" not in json.dumps(output)


def test_s3_prefix_download_fully_pages_markers_and_places_files_atomically(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _FakeS3()
    client.list_responses = [
        _list_response(
            contents=[
                {"Key": "reports/", "Size": 0},
                {"Key": "reports/empty/", "Size": 0},
                {"Key": "reports/a.txt", "Size": 1},
            ],
            truncated=True,
            next_cursor="page-two",
        ),
        _list_response(
            contents=[{"Key": "reports/sub/b.txt", "Size": 2}],
        ),
    ]
    client.objects = {
        "reports/a.txt": b"a",
        "reports/sub/b.txt": b"bb",
    }
    _patch_client(monkeypatch, client)
    snapshots: list[dict[str, Any]] = []
    destination = tmp_path / "download"
    request = _request(
        session,
        project_id,
        operation="file.download",
        input_json={
            "items": [
                {
                    "remote_key": "reports",
                    "remote_kind": "prefix",
                    "local_path": str(destination),
                }
            ],
            "conflict_policy": "fail",
            "error_policy": "stop",
        },
        progress_callback=snapshots.append,
    )

    result = asyncio.run(S3ActionConnector().execute(request))

    assert (destination / "a.txt").read_bytes() == b"a"
    assert (destination / "empty").is_dir()
    assert (destination / "sub" / "b.txt").read_bytes() == b"bb"
    assert not list(tmp_path.rglob("*.part"))
    assert all(body.closed_by_connector for body in client.bodies)
    assert result.output_json["status"] == "success"
    assert result.output_json["completed_count"] == 4
    assert result.output_json["bytes_transferred"] == 3
    assert result.output_json["request_count"] == 4
    assert snapshots[-1]["phase"] == "complete"
    assert snapshots[-1]["bytes_transferred"] == 3

    list_calls = [kwargs for name, kwargs in client.calls if name == "list_objects_v2"]
    assert list_calls[0]["Prefix"] == "reports/"
    assert list_calls[1]["ContinuationToken"] == "page-two"
    assert all("ExpectedBucketOwner" not in kwargs for _name, kwargs in client.calls)


def test_s3_prefix_download_rejects_server_key_traversal_before_local_write(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _FakeS3()
    client.list_responses = [_list_response(contents=[{"Key": "safe/../escape.txt", "Size": 6}])]
    client.objects = {"safe/../escape.txt": b"escape"}
    _patch_client(monkeypatch, client)
    destination = tmp_path / "destination"
    request = _request(
        session,
        project_id,
        operation="file.download",
        input_json={
            "items": [
                {
                    "remote_key": "safe/",
                    "remote_kind": "prefix",
                    "local_path": str(destination),
                }
            ],
            "conflict_policy": "overwrite",
            "error_policy": "stop",
        },
    )

    with pytest.raises(ActionConnectorError) as excinfo:
        asyncio.run(S3ActionConnector().execute(request))

    assert not (tmp_path / "escape.txt").exists()
    assert excinfo.value.output_json["status"] == "failed"
    assert excinfo.value.output_json["failed"][0]["reason_code"] == "validation_error"
    assert [name for name, _kwargs in client.calls] == ["list_objects_v2"]


def test_s3_download_continue_preserves_skip_and_safe_provider_failure(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _FakeS3()
    client.objects = {
        "skip.txt": b"new",
        "denied.txt": ClientError(
            {
                "Error": {
                    "Code": "AccessDenied",
                    "Message": "s3-secret s3-session-token",
                },
                "ResponseMetadata": {
                    "HTTPStatusCode": 403,
                    "RequestId": "request-denied",
                    "HostId": "extended-denied",
                },
            },
            "GetObject",
        ),
    }
    _patch_client(monkeypatch, client)
    existing = tmp_path / "existing.txt"
    existing.write_bytes(b"old")
    request = _request(
        session,
        project_id,
        operation="file.download",
        input_json={
            "items": [
                {
                    "remote_key": "skip.txt",
                    "remote_kind": "object",
                    "local_path": str(existing),
                },
                {
                    "remote_key": "denied.txt",
                    "remote_kind": "object",
                    "local_path": str(tmp_path / "denied.txt"),
                },
            ],
            "conflict_policy": "skip",
            "error_policy": "continue",
        },
    )

    result = asyncio.run(S3ActionConnector().execute(request))

    assert existing.read_bytes() == b"old"
    assert not (tmp_path / "denied.txt").exists()
    assert result.output_json["status"] == "partial"
    assert result.output_json["skipped_count"] == 1
    assert result.output_json["failed"][0]["aws_error_code"] == "AccessDenied"
    assert result.output_json["provider_status_code"] == 403
    assert result.output_json["provider_error"] == {"code": "AccessDenied"}
    assert "s3-secret" not in json.dumps(result.model_dump(mode="json"))
    assert "s3-session-token" not in json.dumps(result.model_dump(mode="json"))


def test_s3_failed_download_keeps_existing_file_and_removes_temporary_file(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client = _FakeS3()
    failing_body = _FailingBody()
    client.objects = {"replace.txt": failing_body}
    _patch_client(monkeypatch, client)
    target = tmp_path / "replace.txt"
    target.write_bytes(b"original")
    request = _request(
        session,
        project_id,
        operation="file.download",
        input_json={
            "items": [
                {
                    "remote_key": "replace.txt",
                    "remote_kind": "object",
                    "local_path": str(target),
                }
            ],
            "conflict_policy": "overwrite",
            "error_policy": "stop",
        },
    )

    with pytest.raises(ActionConnectorError) as excinfo:
        asyncio.run(S3ActionConnector().execute(request))

    assert target.read_bytes() == b"original"
    assert not list(tmp_path.glob("*.part"))
    assert failing_body.closed_by_connector is True
    assert excinfo.value.output_json["bytes_transferred"] == len(b"partial")
    assert "s3-secret" not in json.dumps(excinfo.value.output_json)
