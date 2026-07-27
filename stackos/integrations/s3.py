"""Amazon S3 client construction and credential health probe.

The wrapper accepts only daemon-resolved explicit credentials and one saved
general-purpose bucket binding. It never opts into boto3's ambient credential
or region discovery chain.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from stackos.integrations._base import BaseIntegration
from stackos.mcp.errors import IntegrationDownError

_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_REGION_RE = re.compile(r"^[a-z]{2,8}(?:-[a-z0-9]+)+-\d+$")
_RESERVED_BUCKET_PREFIXES = ("xn--", "sthree-", "amzn-s3-demo-")
_RESERVED_BUCKET_SUFFIXES = (
    "-s3alias",
    "--ol-s3",
    ".mrap",
    "--x-s3",
    "--table-s3",
)


@dataclass(frozen=True)
class S3CredentialMaterial:
    """Explicit AWS credential material held only inside the daemon process."""

    access_key_id: str
    secret_access_key: str
    session_token: str | None


def validate_s3_credential_config(config: Mapping[str, Any]) -> None:
    """Validate the safe one-bucket binding without calling AWS."""

    bucket_value = config.get("bucket")
    if not isinstance(bucket_value, str) or not bucket_value.strip():
        raise ValueError("Amazon S3 bucket is required")
    bucket = bucket_value.strip()
    if (
        not _BUCKET_RE.fullmatch(bucket)
        or ".." in bucket
        or bucket.startswith(_RESERVED_BUCKET_PREFIXES)
        or bucket.endswith(_RESERVED_BUCKET_SUFFIXES)
    ):
        raise ValueError("Amazon S3 bucket must be a valid general-purpose bucket name")
    try:
        ipaddress.ip_address(bucket)
    except ValueError:
        pass
    else:
        raise ValueError("Amazon S3 bucket must not be formatted as an IP address")

    region_value = config.get("region")
    if not isinstance(region_value, str) or not region_value.strip():
        raise ValueError("Amazon S3 region is required")
    region = region_value.strip()
    if not _REGION_RE.fullmatch(region):
        raise ValueError("Amazon S3 region must be an AWS region identifier")


def parse_s3_credentials(payload: bytes) -> S3CredentialMaterial:
    """Decode and validate daemon-held explicit AWS credentials."""

    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrationDownError(
            "Amazon S3 credential payload must be a JSON object",
            data={
                "vendor": "aws-s3",
                "stage": "credential",
                "reason_code": "invalid_credential_payload",
            },
        ) from exc
    if not isinstance(decoded, dict):
        raise IntegrationDownError(
            "Amazon S3 credential payload must be a JSON object",
            data={
                "vendor": "aws-s3",
                "stage": "credential",
                "reason_code": "invalid_credential_payload",
            },
        )

    access_key_id = decoded.get("access_key_id")
    secret_access_key = decoded.get("secret_access_key")
    session_token = decoded.get("session_token")
    if (
        not isinstance(access_key_id, str)
        or not access_key_id
        or access_key_id != access_key_id.strip()
        or any(char.isspace() for char in access_key_id)
        or len(access_key_id) > 256
    ):
        raise IntegrationDownError(
            "Amazon S3 access key id is missing or invalid",
            data={
                "vendor": "aws-s3",
                "stage": "credential",
                "reason_code": "invalid_access_key_id",
            },
        )
    if (
        not isinstance(secret_access_key, str)
        or not secret_access_key
        or len(secret_access_key) > 512
    ):
        raise IntegrationDownError(
            "Amazon S3 secret access key is missing or invalid",
            data={
                "vendor": "aws-s3",
                "stage": "credential",
                "reason_code": "invalid_secret_access_key",
            },
        )
    if session_token is not None and (
        not isinstance(session_token, str) or not session_token or len(session_token) > 16_384
    ):
        raise IntegrationDownError(
            "Amazon S3 session token is invalid",
            data={
                "vendor": "aws-s3",
                "stage": "credential",
                "reason_code": "invalid_session_token",
            },
        )
    return S3CredentialMaterial(
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        session_token=session_token,
    )


def create_s3_client(
    *,
    payload: bytes,
    region: str,
    total_max_attempts: int,
) -> Any:
    """Build one S3 client without ambient credential or region fallback."""

    if not 1 <= total_max_attempts <= 4:
        raise ValueError("Amazon S3 total_max_attempts must be between 1 and 4")
    credentials = parse_s3_credentials(payload)
    session = boto3.session.Session(
        aws_access_key_id=credentials.access_key_id,
        aws_secret_access_key=credentials.secret_access_key,
        aws_session_token=credentials.session_token,
        region_name=region,
    )
    return session.client(
        "s3",
        region_name=region,
        config=Config(
            connect_timeout=10,
            read_timeout=30,
            retries={
                "mode": "standard",
                "total_max_attempts": total_max_attempts,
            },
            user_agent_extra="stackos-amazon-s3/1",
        ),
    )


def _response_metadata(response: Mapping[str, Any]) -> dict[str, Any]:
    metadata = response.get("ResponseMetadata")
    if not isinstance(metadata, Mapping):
        metadata = {}
    headers = metadata.get("HTTPHeaders")
    if not isinstance(headers, Mapping):
        headers = {}
    request_id = metadata.get("RequestId") or headers.get("x-amz-request-id")
    extended_request_id = metadata.get("HostId") or headers.get("x-amz-id-2")
    http_status = metadata.get("HTTPStatusCode")
    return {
        "request_id": str(request_id) if request_id else None,
        "extended_request_id": (str(extended_request_id) if extended_request_id else None),
        "http_status": int(http_status) if isinstance(http_status, int) else None,
        "bucket_region": (
            str(headers["x-amz-bucket-region"]) if headers.get("x-amz-bucket-region") else None
        ),
    }


def _client_error_data(
    exc: ClientError,
    *,
    bucket: str,
    region: str,
) -> tuple[str, dict[str, Any]]:
    response = exc.response if isinstance(exc.response, Mapping) else {}
    error = response.get("Error")
    if not isinstance(error, Mapping):
        error = {}
    code = str(error.get("Code") or "Unknown")
    metadata = _response_metadata(response)
    status = metadata["http_status"]
    actual_region = metadata["bucket_region"]

    if code in {"PermanentRedirect", "AuthorizationHeaderMalformed"} or status == 301:
        reason_code = "region_mismatch"
        detail = "Amazon S3 bucket is in a different region"
        next_action = (
            f"Update the Account region to {actual_region} and test again."
            if actual_region
            else "Confirm the bucket region in AWS and update the Account."
        )
    elif code in {"NoSuchBucket", "NotFound", "404"} or status == 404:
        reason_code = "bucket_not_found"
        detail = "Amazon S3 bucket was not found"
        next_action = "Confirm the bucket name, region, and current bucket existence."
    elif code in {"ExpiredToken", "ExpiredTokenException", "RequestExpired"}:
        reason_code = "expired_credentials"
        detail = "Amazon S3 temporary credentials have expired"
        next_action = "Replace the Account credentials, including a current session token."
    elif code in {
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "InvalidToken",
        "UnrecognizedClientException",
    }:
        reason_code = "invalid_credentials"
        detail = "Amazon S3 rejected the supplied credentials"
        next_action = "Replace the Account credentials and test again."
    elif code in {"AccessDenied", "Forbidden", "403"} or status == 403:
        reason_code = "access_denied"
        detail = "Amazon S3 denied bucket access"
        next_action = "Grant the credential access in AWS IAM or the bucket policy."
    else:
        reason_code = "provider_error"
        detail = "Amazon S3 credential test failed"
        next_action = "Review the safe AWS error code and bucket configuration."

    return detail, {
        "vendor": "aws-s3",
        "stage": "head_bucket",
        "reason_code": reason_code,
        "aws_error_code": code[:160],
        "http_status": status,
        "request_id": metadata["request_id"],
        "extended_request_id": metadata["extended_request_id"],
        "configured_region": region,
        "actual_region": actual_region,
        "bucket": bucket,
        "next_action": next_action,
    }


class S3Integration(BaseIntegration):
    """Read-only Amazon S3 Account health check."""

    kind = "aws-s3"
    vendor = "aws-s3"
    default_qps = 2.0

    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        config = {
            "bucket": bucket,
            "region": region,
        }
        try:
            validate_s3_credential_config(config)
        except ValueError as exc:
            raise IntegrationDownError(
                str(exc),
                data={
                    "vendor": "aws-s3",
                    "stage": "config",
                    "reason_code": "invalid_config",
                },
            ) from exc
        self._bucket_name = bucket.strip()
        self._region = region.strip()
        self._client = create_s3_client(
            payload=self.payload,
            region=self._region,
            total_max_attempts=3,
        )

    async def test_credentials(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._test_sync)

    def _test_sync(self) -> dict[str, Any]:
        try:
            response = self._client.head_bucket(Bucket=self._bucket_name)
        except ClientError as exc:
            detail, data = _client_error_data(
                exc,
                bucket=self._bucket_name,
                region=self._region,
            )
            raise IntegrationDownError(detail, data=data) from exc
        except BotoCoreError as exc:
            raise IntegrationDownError(
                "Amazon S3 credential test could not reach AWS",
                data={
                    "vendor": "aws-s3",
                    "stage": "head_bucket",
                    "reason_code": "transport_error",
                    "error_type": type(exc).__name__,
                    "bucket": self._bucket_name,
                    "configured_region": self._region,
                    "next_action": "Check network access and retry the read-only Account test.",
                },
            ) from exc

        metadata = _response_metadata(response)
        actual_region = (
            str(response.get("BucketRegion"))
            if response.get("BucketRegion")
            else metadata["bucket_region"] or self._region
        )
        return {
            "ok": True,
            "vendor": "aws-s3",
            "status": "ok",
            "bucket": self._bucket_name,
            "region": self._region,
            "actual_region": actual_region,
            "request_id": metadata["request_id"],
            "extended_request_id": metadata["extended_request_id"],
        }


__all__ = [
    "S3CredentialMaterial",
    "S3Integration",
    "create_s3_client",
    "parse_s3_credentials",
    "validate_s3_credential_config",
]
