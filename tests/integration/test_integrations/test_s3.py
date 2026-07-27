"""Amazon S3 credential wrapper tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any, ClassVar

import httpx
import pytest
from botocore.exceptions import ClientError
from botocore.session import get_session

from stackos.integrations.s3 import (
    AWS_S3_REGIONS,
    S3Integration,
    normalize_s3_prefix,
    validate_s3_credential_config,
)
from stackos.mcp.errors import IntegrationDownError


class _S3Client:
    response: ClassVar[dict[str, Any]] = {
        "KeyCount": 0,
        "IsTruncated": False,
        "ResponseMetadata": {
            "HTTPStatusCode": 200,
            "RequestId": "request-123",
            "HostId": "extended-456",
            "HTTPHeaders": {"x-amz-bucket-region": "us-west-2"},
        },
    }
    error: ClassVar[ClientError | None] = None
    calls: ClassVar[list[dict[str, Any]]] = []

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        self.__class__.calls.append(kwargs)
        if self.__class__.error is not None:
            raise self.__class__.error
        return self.__class__.response


class _Session:
    init_calls: ClassVar[list[dict[str, Any]]] = []
    client_calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.__class__.init_calls.append(kwargs)

    def client(self, service_name: str, **kwargs: Any) -> _S3Client:
        self.__class__.client_calls.append({"service_name": service_name, **kwargs})
        return _S3Client()


def _reset_fakes() -> None:
    _S3Client.calls.clear()
    _S3Client.error = None
    _Session.init_calls.clear()
    _Session.client_calls.clear()


def _payload() -> bytes:
    return json.dumps(
        {
            "access_key_id": "AKIAEXPLICIT12345678",
            "secret_access_key": "explicit-secret",
            "session_token": "temporary-session-token",
        }
    ).encode()


def test_locked_botocore_s3_model_has_required_conditional_members() -> None:
    session = get_session()
    service = session.get_service_model("s3")
    endpoint_regions = tuple(
        region
        for partition in session.get_available_partitions()
        for region in session.get_available_regions("s3", partition_name=partition)
    )
    assert endpoint_regions == AWS_S3_REGIONS
    expected_members = {
        "ListObjectsV2": {
            "ContinuationToken",
            "EncodingType",
            "MaxKeys",
        },
        "PutObject": {"IfNoneMatch"},
        "CompleteMultipartUpload": {
            "IfMatch",
            "IfNoneMatch",
        },
        "CopyObject": {
            "CopySourceIfMatch",
            "IfNoneMatch",
        },
        "UploadPartCopy": {
            "CopySourceIfMatch",
        },
        "DeleteObject": {"IfMatch"},
    }

    for operation, members in expected_members.items():
        input_shape = service.operation_model(operation).input_shape
        assert input_shape is not None
        assert members <= set(input_shape.members), operation

    delete_objects_input = service.operation_model("DeleteObjects").input_shape
    assert delete_objects_input is not None
    delete_shape = delete_objects_input.members["Delete"]
    object_identifiers = delete_shape.members["Objects"]
    object_identifier = object_identifiers.member
    assert {"Key", "ETag"} <= set(object_identifier.members)


def test_s3_config_rejects_invalid_bucket_region_and_prefix() -> None:
    with pytest.raises(ValueError, match="general-purpose"):
        validate_s3_credential_config(
            {
                "bucket": "directory--usw2-az1--x-s3",
                "region": "us-west-2",
            }
        )
    with pytest.raises(ValueError, match="supported AWS"):
        validate_s3_credential_config(
            {
                "bucket": "valid-bucket",
                "region": "moon-west-1",
            }
        )
    for prefix in (
        "/data",
        "s3://valid-bucket/data/",
        r"data\private",
        "data//private",
        "data/./private",
        "data/../private",
        "data/\nprivate",
    ):
        with pytest.raises(ValueError, match="prefix"):
            validate_s3_credential_config(
                {
                    "bucket": "valid-bucket",
                    "region": "us-west-2",
                    "prefix": prefix,
                }
            )
    validate_s3_credential_config(
        {
            "bucket": "valid-bucket",
            "region": "us-west-2",
            "prefix": "data",
        }
    )
    assert normalize_s3_prefix(" data ") == "data/"
    assert normalize_s3_prefix("") == ""
    assert "us-east-1" in AWS_S3_REGIONS
    assert "cn-north-1" in AWS_S3_REGIONS
    assert "us-gov-west-1" in AWS_S3_REGIONS
    assert "eusc-de-east-1" in AWS_S3_REGIONS


def test_s3_auth_probe_lists_configured_prefix_with_only_explicit_credentials(
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.integrations.s3 as s3_module

    _reset_fakes()
    monkeypatch.setattr(s3_module.boto3.session, "Session", _Session)

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            integration = S3Integration(
                payload=_payload(),
                project_id=project_id,
                http=client,
                bucket="stackos-fixture",
                region="us-west-2",
                prefix="data",
            )
            return await integration.test_credentials()

    result = asyncio.run(go())

    assert _Session.init_calls == [
        {
            "aws_access_key_id": "AKIAEXPLICIT12345678",
            "aws_secret_access_key": "explicit-secret",
            "aws_session_token": "temporary-session-token",
            "region_name": "us-west-2",
        }
    ]
    assert _Session.client_calls[0]["service_name"] == "s3"
    assert _Session.client_calls[0]["region_name"] == "us-west-2"
    assert _Session.client_calls[0]["config"].retries == {
        "mode": "standard",
        "total_max_attempts": 3,
    }
    assert _S3Client.calls == [
        {
            "Bucket": "stackos-fixture",
            "Prefix": "data/",
            "MaxKeys": 1,
        }
    ]
    assert result == {
        "ok": True,
        "vendor": "aws-s3",
        "status": "ok",
        "bucket": "stackos-fixture",
        "prefix": "data/",
        "region": "us-west-2",
        "actual_region": "us-west-2",
        "request_id": "request-123",
        "extended_request_id": "extended-456",
    }
    serialized = json.dumps(result)
    assert "AKIAEXPLICIT12345678" not in serialized
    assert "explicit-secret" not in serialized
    assert "temporary-session-token" not in serialized


def test_s3_auth_probe_keeps_legacy_bucket_root_as_empty_prefix(
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.integrations.s3 as s3_module

    _reset_fakes()
    monkeypatch.setattr(s3_module.boto3.session, "Session", _Session)

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            integration = S3Integration(
                payload=_payload(),
                project_id=project_id,
                http=client,
                bucket="stackos-fixture",
                region="us-west-2",
            )
            return await integration.test_credentials()

    result = asyncio.run(go())

    assert _S3Client.calls == [
        {
            "Bucket": "stackos-fixture",
            "Prefix": "",
            "MaxKeys": 1,
        }
    ]
    assert result["prefix"] == ""


@pytest.mark.parametrize(
    ("code", "status", "reason_code"),
    [
        ("AccessDenied", 403, "access_denied"),
        ("NoSuchBucket", 404, "bucket_not_found"),
        ("ExpiredToken", 403, "expired_credentials"),
        ("InvalidAccessKeyId", 403, "invalid_credentials"),
        ("PermanentRedirect", 301, "region_mismatch"),
    ],
)
def test_s3_auth_probe_returns_sanitized_provider_guidance(
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    status: int,
    reason_code: str,
) -> None:
    import stackos.integrations.s3 as s3_module

    _reset_fakes()
    monkeypatch.setattr(s3_module.boto3.session, "Session", _Session)
    _S3Client.error = ClientError(
        {
            "Error": {
                "Code": code,
                "Message": (
                    "provider rejected AKIAEXPLICIT12345678 explicit-secret temporary-session-token"
                ),
            },
            "ResponseMetadata": {
                "HTTPStatusCode": status,
                "RequestId": "request-error",
                "HostId": "extended-error",
                "HTTPHeaders": {"x-amz-bucket-region": "eu-west-1"},
            },
        },
        "ListObjectsV2",
    )

    async def go() -> None:
        async with httpx.AsyncClient() as client:
            integration = S3Integration(
                payload=_payload(),
                project_id=project_id,
                http=client,
                bucket="stackos-fixture",
                region="us-west-2",
                prefix="data/",
            )
            with pytest.raises(IntegrationDownError) as excinfo:
                await integration.test_credentials()
            assert excinfo.value.data["stage"] == "list_objects_v2"
            assert excinfo.value.data["reason_code"] == reason_code
            assert excinfo.value.data["aws_error_code"] == code
            assert excinfo.value.data["request_id"] == "request-error"
            assert excinfo.value.data["actual_region"] == "eu-west-1"
            serialized = json.dumps(excinfo.value.to_dict())
            assert "AKIAEXPLICIT12345678" not in serialized
            assert "explicit-secret" not in serialized
            assert "temporary-session-token" not in serialized

    asyncio.run(go())


def test_s3_auth_probe_rejects_invalid_payload_without_creating_session(
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import stackos.integrations.s3 as s3_module

    _reset_fakes()
    monkeypatch.setattr(s3_module.boto3.session, "Session", _Session)

    async def go() -> None:
        async with httpx.AsyncClient() as client:
            with pytest.raises(IntegrationDownError) as excinfo:
                S3Integration(
                    payload=json.dumps(
                        {
                            "access_key_id": "",
                            "secret_access_key": "not-returned",
                        }
                    ).encode(),
                    project_id=project_id,
                    http=client,
                    bucket="stackos-fixture",
                    region="us-west-2",
                )
            assert excinfo.value.data["reason_code"] == "invalid_access_key_id"
            assert "not-returned" not in json.dumps(excinfo.value.to_dict())

    asyncio.run(go())
    assert _Session.init_calls == []
