"""Linear OAuth and fixed-endpoint GraphQL integration wrapper."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

import httpx

from stackos.artifacts import redact_secret_text
from stackos.integrations._base import (
    DEFAULT_MAX_RETRIES,
    BaseIntegration,
    IntegrationCallResult,
)
from stackos.mcp.errors import IntegrationDownError, RateLimitedError
from stackos.repositories.base import ValidationError

LINEAR_GRAPHQL_ENDPOINT = "https://api.linear.app/graphql"
LINEAR_MAX_REQUEST_BYTES = 256 * 1024
LINEAR_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
LINEAR_OAUTH_AUTH_METHOD_KEY = "oauth2_authorization_code"
LINEAR_PERSONAL_API_KEY_AUTH_METHOD_KEY = "personal_api_key"


def parse_linear_oauth_payload(payload: bytes) -> dict[str, Any]:
    """Decode daemon-resolved Linear OAuth material without exposing it."""
    text = payload.decode("utf-8").strip()
    if not text:
        raise ValidationError("linear credential is empty")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError("linear credential payload must be a JSON object") from exc
    if not isinstance(parsed, dict):
        raise ValidationError("linear credential payload must be a JSON object")
    return parsed


def linear_access_token(payload: dict[str, Any]) -> str:
    """Require an access token issued or refreshed by ``AuthRepository``."""
    access_token = payload.get("access_token")
    if isinstance(access_token, str) and access_token.strip():
        return access_token.strip()
    raise ValidationError(
        "linear credential missing access_token; reconnect or renew the credential"
    )


def linear_bearer_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def parse_linear_personal_api_key(payload: bytes) -> str:
    """Read the raw daemon-held Linear personal API key."""
    try:
        api_key = payload.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise ValidationError("linear personal API key must be UTF-8 text") from exc
    if not api_key:
        raise ValidationError("linear personal API key is empty")
    return api_key


def linear_personal_api_key_headers(api_key: str) -> dict[str, str]:
    """Linear requires the stored personal key as the complete Authorization value."""
    return {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }


def read_linear_graphql_asset(relative_path: str) -> str:
    """Read one checked-in Linear document from checkout or packaged assets."""
    asset_path = Path(relative_path)
    if (
        asset_path.is_absolute()
        or ".." in asset_path.parts
        or not asset_path.parts
        or asset_path.parts[0] != "graphql"
        or asset_path.suffix != ".graphql"
    ):
        raise ValidationError("Linear asset path must be a .graphql file under graphql/")
    repo_path = Path(__file__).resolve().parents[2] / "plugins" / "linear" / asset_path
    if repo_path.is_file():
        return repo_path.read_text(encoding="utf-8")
    node = (
        resources.files("stackos")
        .joinpath("_assets")
        .joinpath("plugins")
        .joinpath("linear")
        .joinpath(relative_path)
    )
    if node.is_file():
        return node.read_text(encoding="utf-8")
    raise ValidationError(f"Linear asset not found: {relative_path}")


class LinearIntegration(BaseIntegration):
    """Credential probe and fixed-endpoint Linear GraphQL transport."""

    kind = "linear"
    vendor = "linear"
    default_qps = 1.0

    def __init__(
        self,
        *,
        auth_method_key: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        try:
            probe_auth_method_key = (
                self.probe_context.auth_method_key if self.probe_context is not None else None
            )
            if auth_method_key is not None and not auth_method_key.strip():
                raise ValidationError("linear credential has an unsupported auth method")
            if probe_auth_method_key is not None and not probe_auth_method_key.strip():
                raise ValidationError("linear credential auth method does not match probe context")
            if (
                auth_method_key is not None
                and probe_auth_method_key is not None
                and auth_method_key != probe_auth_method_key
            ):
                raise ValidationError("linear credential auth method does not match probe context")
            resolved_auth_method_key = (
                auth_method_key if auth_method_key is not None else probe_auth_method_key
            )
            if resolved_auth_method_key == LINEAR_OAUTH_AUTH_METHOD_KEY:
                parsed = parse_linear_oauth_payload(self.payload)
                self._headers = linear_bearer_headers(linear_access_token(parsed))
            elif resolved_auth_method_key == LINEAR_PERSONAL_API_KEY_AUTH_METHOD_KEY:
                self._headers = linear_personal_api_key_headers(
                    parse_linear_personal_api_key(self.payload)
                )
            else:
                raise ValidationError("linear credential has an unsupported auth method")
        except ValidationError as exc:
            raise IntegrationDownError(
                redact_secret_text(str(exc)),
                data={"vendor": "linear"},
            ) from exc

    async def execute_document(
        self,
        *,
        document_path: str,
        variables: dict[str, Any] | None = None,
        op: str,
        write: bool = False,
    ) -> IntegrationCallResult:
        query = read_linear_graphql_asset(document_path)
        body: dict[str, Any] = {"query": query}
        if variables:
            body["variables"] = variables
        try:
            request_bytes = json.dumps(body, separators=(",", ":")).encode()
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "Linear GraphQL variables must be JSON-compatible",
                data={"reason_code": "invalid_variables"},
            ) from exc
        if len(request_bytes) > LINEAR_MAX_REQUEST_BYTES:
            raise ValidationError(
                "Linear GraphQL request exceeds the fixed size limit",
                data={
                    "reason_code": "request_too_large",
                    "max_bytes": LINEAR_MAX_REQUEST_BYTES,
                },
            )
        try:
            result = await self.call(
                op=op,
                method="POST",
                url=LINEAR_GRAPHQL_ENDPOINT,
                json_body=body,
                headers=self._headers,
                request_log_body=body,
                max_retries=0 if write else DEFAULT_MAX_RETRIES,
            )
        except RateLimitedError:
            raise
        except IntegrationDownError as exc:
            data = dict(exc.data)
            if write and (
                data.get("status") is None
                or data.get("reason_code") == "response_too_large"
                or (isinstance(data.get("status"), int) and int(data["status"]) >= 500)
            ):
                data["outcome_unknown"] = True
            else:
                data.setdefault("outcome_unknown", False)
            raise IntegrationDownError(
                "Linear GraphQL transport failed",
                data=data,
            ) from exc

        body_data = result.data
        if not isinstance(body_data, dict):
            raise IntegrationDownError(
                "Linear returned a malformed GraphQL response",
                data={
                    "vendor": "linear",
                    "op": op,
                    "reason_code": "malformed_response",
                    "outcome_unknown": write,
                },
            )
        errors = body_data.get("errors")
        if errors:
            rate_limit = _rate_limit_context(errors)
            if rate_limit is not None:
                raise RateLimitedError(
                    "Linear GraphQL rate limit rejected the operation",
                    data={
                        "vendor": "linear",
                        "op": op,
                        "status": 200,
                        "reason_code": "graphql_ratelimited",
                        "rate_limit": rate_limit,
                        "outcome_unknown": False,
                    },
                )
            partial_data = isinstance(body_data.get("data"), dict)
            raise IntegrationDownError(
                "Linear GraphQL returned operation errors",
                data={
                    "vendor": "linear",
                    "op": op,
                    "reason_code": "graphql_error",
                    "partial_data": partial_data,
                    "outcome_unknown": bool(write and partial_data),
                    "provider_error": {"errors": _safe_graphql_errors(errors)},
                    "metadata": result.metadata or {},
                },
            )
        response_data = body_data.get("data")
        if not isinstance(response_data, dict):
            raise IntegrationDownError(
                "Linear GraphQL response is missing its data object",
                data={
                    "vendor": "linear",
                    "op": op,
                    "reason_code": "malformed_response",
                    "outcome_unknown": write,
                },
            )
        unsuccessful_root = next(
            (
                root
                for root, payload in response_data.items()
                if isinstance(payload, dict) and payload.get("success") is False
            ),
            None,
        )
        if unsuccessful_root is not None:
            raise IntegrationDownError(
                "Linear rejected the mutation",
                data={
                    "vendor": "linear",
                    "op": op,
                    "reason_code": "provider_unsuccessful",
                    "root": unsuccessful_root,
                    "outcome_unknown": False,
                    "metadata": result.metadata or {},
                },
            )
        return result

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Classify Linear-specific HTTP failures around the shared retry loop."""
        try:
            response = await super()._request_with_retry(method, url, **kwargs)
        except RateLimitedError:
            raise
        except IntegrationDownError as exc:
            status = exc.data.get("status")
            provider_error = exc.data.get("provider_error")
            if status == 400:
                rate_limit = _rate_limit_context(provider_error)
                if rate_limit is not None:
                    raise RateLimitedError(
                        "Linear GraphQL rate limit rejected the operation",
                        data={
                            "vendor": "linear",
                            "op": str(kwargs.get("op") or "graphql"),
                            "status": 400,
                            "reason_code": "graphql_ratelimited",
                            "rate_limit": rate_limit,
                        },
                    ) from exc
            if status == 401:
                reason_code = "authentication_failed"
                retryable = False
            elif status == 403:
                reason_code = "authorization_failed"
                retryable = False
            elif isinstance(status, int) and status >= 500:
                reason_code = "provider_unavailable"
                retryable = True
            elif status is None:
                reason_code = "transport_failure"
                retryable = True
            else:
                reason_code = "provider_http_error"
                retryable = False
            data: dict[str, Any] = {
                "vendor": "linear",
                "op": str(kwargs.get("op") or "graphql"),
                "reason_code": reason_code,
                "retryable": retryable,
            }
            if isinstance(status, int):
                data["status"] = status
            safe_errors = _safe_graphql_errors(provider_error)
            if safe_errors:
                data["provider_error"] = {"errors": safe_errors}
            raise IntegrationDownError(
                "Linear GraphQL HTTP request failed",
                data=data,
            ) from exc
        if len(response.content) > LINEAR_MAX_RESPONSE_BYTES:
            raise IntegrationDownError(
                "Linear GraphQL response exceeds the fixed size limit",
                data={
                    "vendor": "linear",
                    "op": str(kwargs.get("op") or "graphql"),
                    "reason_code": "response_too_large",
                    "max_bytes": LINEAR_MAX_RESPONSE_BYTES,
                    "status": response.status_code,
                },
            )
        return response

    def _extract_response_metadata(
        self,
        op: str,
        *,
        request: Any,
        response: Any,
        http_response: Any,
    ) -> dict[str, Any] | None:
        del op, request, response
        metadata: dict[str, Any] = {"endpoint": LINEAR_GRAPHQL_ENDPOINT}
        request_id = http_response.headers.get("x-request-id")
        if request_id:
            metadata["request_id"] = request_id
        for header, key in (
            ("x-ratelimit-requests-limit", "rate_limit"),
            ("x-ratelimit-requests-remaining", "rate_remaining"),
            ("x-ratelimit-requests-reset", "rate_reset"),
            ("x-ratelimit-complexity-limit", "complexity_limit"),
            ("x-ratelimit-complexity-remaining", "complexity_remaining"),
            ("x-ratelimit-complexity-reset", "complexity_reset"),
        ):
            value = http_response.headers.get(header)
            if value is not None:
                metadata[key] = value
        return metadata

    async def test_credentials(self) -> dict[str, Any]:
        result = await self.execute_document(
            document_path="graphql/auth/viewer-organization.graphql",
            op="auth.test",
        )
        body = result.data
        if not isinstance(body, dict):
            return {
                "ok": False,
                "vendor": "linear",
                "status": "invalid_response",
                "summary": "Linear auth probe returned a non-JSON response",
            }
        errors = body.get("errors")
        if errors:
            return {
                "ok": False,
                "vendor": "linear",
                "status": "graphql_error",
                "summary": redact_secret_text(_graphql_error_summary(errors)),
            }
        data = body.get("data")
        viewer = data.get("viewer") if isinstance(data, dict) else None
        organization = viewer.get("organization") if isinstance(viewer, dict) else None
        if not isinstance(viewer, dict) or not isinstance(organization, dict):
            return {
                "ok": False,
                "vendor": "linear",
                "status": "invalid_response",
                "summary": "Linear auth probe did not return viewer organization identity",
            }
        organization_id = organization.get("id")
        organization_name = organization.get("name")
        viewer_id = viewer.get("id")
        viewer_name = viewer.get("name")
        return {
            "ok": True,
            "vendor": "linear",
            "status": "ok",
            "organization_id": organization_id,
            "organization_name": organization_name,
            "viewer_id": viewer_id,
            "viewer_name": viewer_name,
            "metadata": {
                "evidence": {
                    "account": {
                        "provider_account_id": (
                            str(organization_id) if organization_id is not None else None
                        ),
                        "display_name": (
                            str(organization_name) if organization_name is not None else None
                        ),
                        "metadata": {
                            "organization_id": organization_id,
                            "organization_name": organization_name,
                            "viewer_id": viewer_id,
                            "viewer_name": viewer_name,
                        },
                    }
                }
            },
        }


def _graphql_error_summary(errors: Any) -> str:
    if isinstance(errors, list):
        messages = [
            str(item.get("message") or item)
            for item in errors[:5]
            if isinstance(item, dict) or item is not None
        ]
        return "; ".join(messages) or "Linear GraphQL error"
    if isinstance(errors, dict):
        return str(errors.get("message") or errors)
    return str(errors)


def _graphql_error_items(errors: Any) -> list[dict[str, Any]]:
    if isinstance(errors, dict) and isinstance(errors.get("errors"), list):
        errors = errors["errors"]
    if not isinstance(errors, list):
        return []
    return [item for item in errors[:10] if isinstance(item, dict)]


def _safe_graphql_errors(errors: Any) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    allowed_extensions = {
        "code",
        "type",
        "statusCode",
        "limit",
        "remaining",
        "reset",
        "requested",
        "retryAfter",
    }
    for item in _graphql_error_items(errors):
        normalized: dict[str, Any] = {}
        path = item.get("path")
        if isinstance(path, list):
            normalized["path"] = [
                value
                for value in path[:20]
                if isinstance(value, (str, int)) and not isinstance(value, bool)
            ]
        extensions = item.get("extensions")
        if isinstance(extensions, dict):
            normalized_extensions = {
                key: value
                for key, value in extensions.items()
                if key in allowed_extensions and isinstance(value, (str, int, float, bool))
            }
            if normalized_extensions:
                normalized["extensions"] = normalized_extensions
        safe.append(normalized)
    return safe


def _rate_limit_context(errors: Any) -> dict[str, Any] | None:
    allowed = {
        "code",
        "type",
        "limit",
        "remaining",
        "reset",
        "requested",
        "retryAfter",
    }
    for item in _graphql_error_items(errors):
        extensions = item.get("extensions")
        if not isinstance(extensions, dict):
            continue
        code = str(extensions.get("code") or "").upper()
        if code != "RATELIMITED":
            continue
        return {
            key: value
            for key, value in extensions.items()
            if key in allowed and isinstance(value, (str, int, float, bool))
        }
    return None


__all__ = [
    "LINEAR_GRAPHQL_ENDPOINT",
    "LINEAR_MAX_REQUEST_BYTES",
    "LINEAR_MAX_RESPONSE_BYTES",
    "LINEAR_OAUTH_AUTH_METHOD_KEY",
    "LINEAR_PERSONAL_API_KEY_AUTH_METHOD_KEY",
    "LinearIntegration",
    "linear_access_token",
    "linear_bearer_headers",
    "linear_personal_api_key_headers",
    "parse_linear_oauth_payload",
    "parse_linear_personal_api_key",
    "read_linear_graphql_asset",
]
