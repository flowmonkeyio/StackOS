"""Cloudflare v4 integration limited to zone discovery and DNS record CRUD."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal
from urllib.parse import quote

import httpx

from stackos.integrations._base import BaseIntegration, IntegrationCallResult
from stackos.mcp.errors import IntegrationDownError, RateLimitedError
from stackos.repositories.base import ValidationError

_ExpectedResult = Literal["list", "object", "delete"]
_MUTATION_METHODS = {"POST", "PATCH", "PUT", "DELETE"}


class CloudflareIntegration(BaseIntegration):
    """Decision-free wrapper for Cloudflare's public DNS record endpoints."""

    kind = "cloudflare"
    vendor = "cloudflare"
    default_qps = 4.0
    BASE_URL = "https://api.cloudflare.com/client/v4"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._api_token = self.payload.decode("utf-8").strip()
        if not self._api_token:
            raise ValidationError("cloudflare credential is empty")

    def _headers(self, *, json_body: bool = False) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._api_token}",
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _response_headers(response: httpx.Response) -> dict[str, Any]:
        metadata: dict[str, Any] = {"status_code": response.status_code}
        for header, key in (
            ("cf-ray", "cf_ray"),
            ("ratelimit", "ratelimit"),
            ("ratelimit-policy", "ratelimit_policy"),
            ("retry-after", "retry_after"),
        ):
            value = response.headers.get(header)
            if value:
                metadata[key] = value
        return metadata

    def _extract_response_metadata(
        self,
        op: str,
        *,
        request: Any,
        response: Any,
        http_response: httpx.Response,
    ) -> dict[str, Any]:
        del op, request, response
        return self._response_headers(http_response)

    @classmethod
    def _provider_error(cls, response: httpx.Response) -> Any:
        try:
            raw: Any = response.json()
        except ValueError:
            raw = {"message": response.text[:500] or f"HTTP {response.status_code}"}
        body = dict(raw) if isinstance(raw, dict) else {"response": raw}
        for key, value in cls._response_headers(response).items():
            if key != "status_code":
                body.setdefault(key, value)
        return cls._truncate(body)

    @staticmethod
    def _path_identifier(value: str, *, label: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValidationError(f"{label} is required")
        if len(normalized) > 32:
            raise ValidationError(f"{label} must contain at most 32 characters")
        return quote(normalized, safe="")

    @staticmethod
    def _query(params: Mapping[str, Any] | None) -> dict[str, str | int]:
        query: dict[str, str | int] = {}
        for key, value in (params or {}).items():
            if value is None:
                continue
            if isinstance(value, bool):
                query[key] = "true" if value else "false"
            elif isinstance(value, list):
                query[key] = ",".join(str(item) for item in value)
            elif isinstance(value, (str, int)) and not isinstance(value, bool):
                query[key] = value
            else:
                raise ValidationError(f"unsupported Cloudflare query value for {key!r}")
        return query

    @staticmethod
    def _envelope_failure(
        *,
        op: str,
        data: Any,
        metadata: Mapping[str, Any] | None,
        detail: str,
    ) -> IntegrationDownError:
        status = (metadata or {}).get("status_code")
        provider_error: dict[str, Any] = (
            dict(data) if isinstance(data, dict) else {"response": data}
        )
        for key in ("cf_ray", "ratelimit", "ratelimit_policy", "retry_after"):
            value = (metadata or {}).get(key)
            if value is not None:
                provider_error.setdefault(key, value)
        return IntegrationDownError(
            detail,
            data={
                "vendor": "cloudflare",
                "op": op,
                "status": status,
                "provider_error": BaseIntegration._truncate(provider_error),
            },
        )

    @staticmethod
    def _annotate_mutation_failure(
        exc: IntegrationDownError | RateLimitedError,
        *,
        op: str,
        force_unknown: bool = False,
    ) -> None:
        status = exc.data.get("status")
        try:
            status_code = int(status) if status is not None else None
        except (TypeError, ValueError):
            status_code = None
        ambiguous = force_unknown or status_code is None or status_code >= 500
        exc.data.update(
            {
                "mutation": True,
                "automatic_retry_count": 0,
                "retry_safe": status_code == 429 and not force_unknown,
                "outcome_unknown": ambiguous,
            }
        )
        if ambiguous:
            exc.data.update(
                {
                    "reconciliation_action": "utils.cloudflare.dns.records.list",
                    "reconciliation_guidance": (
                        "List the selected zone's DNS records using the intended name, "
                        "type, and content before deciding whether to retry the mutation."
                    ),
                }
            )
        elif status_code != 429:
            exc.data["repair_guidance"] = (
                "Repair the provider-reported request or permission error before retrying."
            )

    async def _call_cloudflare(
        self,
        *,
        op: str,
        method: str,
        url: str,
        expected: _ExpectedResult,
        params: Mapping[str, Any] | None = None,
        record: Mapping[str, Any] | None = None,
    ) -> IntegrationCallResult:
        mutation = method in _MUTATION_METHODS
        try:
            call_result = await self.call(
                op=op,
                method=method,
                url=url,
                params=self._query(params),
                json_body=dict(record) if record is not None else None,
                headers=self._headers(json_body=record is not None),
                max_retries=0 if mutation else 3,
            )
        except (IntegrationDownError, RateLimitedError) as exc:
            if mutation:
                self._annotate_mutation_failure(exc, op=op)
            raise

        data = call_result.data
        metadata = call_result.metadata or {}
        if not isinstance(data, dict):
            failure = self._envelope_failure(
                op=op,
                data=data,
                metadata=metadata,
                detail=f"cloudflare.{op} returned a non-object response",
            )
            if mutation:
                self._annotate_mutation_failure(failure, op=op, force_unknown=True)
            raise failure
        if data.get("success") is False:
            failure = self._envelope_failure(
                op=op,
                data=data,
                metadata=metadata,
                detail=f"cloudflare.{op} reported success=false",
            )
            if mutation:
                self._annotate_mutation_failure(failure, op=op)
                failure.data["outcome_unknown"] = False
            raise failure
        if expected != "delete" and data.get("success") is not True:
            failure = self._envelope_failure(
                op=op,
                data=data,
                metadata=metadata,
                detail=f"cloudflare.{op} returned an envelope without success=true",
            )
            if mutation:
                self._annotate_mutation_failure(failure, op=op, force_unknown=True)
            raise failure

        provider_result = data.get("result")
        malformed = False
        if expected == "list":
            malformed = not isinstance(provider_result, list) or not isinstance(
                data.get("result_info"), dict
            )
        elif expected == "object":
            malformed = not isinstance(provider_result, dict)
        else:
            malformed = not isinstance(provider_result, dict) or not isinstance(
                provider_result.get("id"), str
            )
        if malformed:
            failure = self._envelope_failure(
                op=op,
                data=data,
                metadata=metadata,
                detail=f"cloudflare.{op} returned a malformed {expected} envelope",
            )
            if mutation:
                self._annotate_mutation_failure(failure, op=op, force_unknown=True)
            raise failure
        return call_result

    async def list_zones(
        self,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> IntegrationCallResult:
        return await self._call_cloudflare(
            op="zones.list",
            method="GET",
            url=f"{self.BASE_URL}/zones",
            expected="list",
            params=params,
        )

    async def verify_token(self) -> IntegrationCallResult:
        return await self._call_cloudflare(
            op="tokens.verify",
            method="GET",
            url=f"{self.BASE_URL}/user/tokens/verify",
            expected="object",
        )

    async def list_dns_records(
        self,
        *,
        zone_id: str,
        params: Mapping[str, Any] | None = None,
    ) -> IntegrationCallResult:
        zone = self._path_identifier(zone_id, label="zone_id")
        return await self._call_cloudflare(
            op="dns.records.list",
            method="GET",
            url=f"{self.BASE_URL}/zones/{zone}/dns_records",
            expected="list",
            params=params,
        )

    async def get_dns_record(
        self,
        *,
        zone_id: str,
        dns_record_id: str,
        params: Mapping[str, Any] | None = None,
    ) -> IntegrationCallResult:
        zone = self._path_identifier(zone_id, label="zone_id")
        record_id = self._path_identifier(dns_record_id, label="dns_record_id")
        return await self._call_cloudflare(
            op="dns.records.get",
            method="GET",
            url=f"{self.BASE_URL}/zones/{zone}/dns_records/{record_id}",
            expected="object",
            params=params,
        )

    async def create_dns_record(
        self,
        *,
        zone_id: str,
        record: Mapping[str, Any],
        params: Mapping[str, Any] | None = None,
    ) -> IntegrationCallResult:
        zone = self._path_identifier(zone_id, label="zone_id")
        return await self._call_cloudflare(
            op="dns.records.create",
            method="POST",
            url=f"{self.BASE_URL}/zones/{zone}/dns_records",
            expected="object",
            params=params,
            record=record,
        )

    async def edit_dns_record(
        self,
        *,
        zone_id: str,
        dns_record_id: str,
        record: Mapping[str, Any],
        params: Mapping[str, Any] | None = None,
    ) -> IntegrationCallResult:
        zone = self._path_identifier(zone_id, label="zone_id")
        record_id = self._path_identifier(dns_record_id, label="dns_record_id")
        return await self._call_cloudflare(
            op="dns.records.edit",
            method="PATCH",
            url=f"{self.BASE_URL}/zones/{zone}/dns_records/{record_id}",
            expected="object",
            params=params,
            record=record,
        )

    async def replace_dns_record(
        self,
        *,
        zone_id: str,
        dns_record_id: str,
        record: Mapping[str, Any],
        params: Mapping[str, Any] | None = None,
    ) -> IntegrationCallResult:
        zone = self._path_identifier(zone_id, label="zone_id")
        record_id = self._path_identifier(dns_record_id, label="dns_record_id")
        return await self._call_cloudflare(
            op="dns.records.replace",
            method="PUT",
            url=f"{self.BASE_URL}/zones/{zone}/dns_records/{record_id}",
            expected="object",
            params=params,
            record=record,
        )

    async def delete_dns_record(
        self,
        *,
        zone_id: str,
        dns_record_id: str,
    ) -> IntegrationCallResult:
        zone = self._path_identifier(zone_id, label="zone_id")
        record_id = self._path_identifier(dns_record_id, label="dns_record_id")
        return await self._call_cloudflare(
            op="dns.records.delete",
            method="DELETE",
            url=f"{self.BASE_URL}/zones/{zone}/dns_records/{record_id}",
            expected="delete",
        )

    async def test_credentials(self) -> dict[str, Any]:
        result = await self.verify_token()
        data = result.data if isinstance(result.data, dict) else {}
        raw_token = data.get("result")
        token = raw_token if isinstance(raw_token, dict) else {}
        token_status = str(token.get("status") or "")
        if token_status != "active":
            raise IntegrationDownError(
                "Cloudflare API token is not active",
                data={
                    "vendor": "cloudflare",
                    "stage": "authenticate",
                    "reason_code": "token_not_active",
                    "token_status": token_status or "unknown",
                },
            )
        return {
            "ok": True,
            "vendor": "cloudflare",
            "zone_read_verified": False,
            "dns_read_verified": False,
            "dns_write_verified": False,
        }


__all__ = ["CloudflareIntegration"]
