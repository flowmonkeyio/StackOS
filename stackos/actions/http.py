"""Generic static HTTP/Webhook action connector."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

import httpx

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.repositories.base import ValidationError

_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_ALLOWED_AUTH_TYPES = {"none", "bearer", "header", "basic"}
_ALLOWED_REQUEST_MODES = {"json", "query"}
_ALLOWED_RESPONSE_MODES = {"auto", "json", "text"}
_SECRET_HEADER_PARTS = {
    "authorization",
    "api-key",
    "api_key",
    "apikey",
    "cookie",
    "password",
    "secret",
    "token",
}

# Static plugin config follows RFC 9110 HTTP semantics. Auth credentials are
# injected at execution using RFC 6750 bearer, RFC 7617 basic, or a configured
# header; static headers reject secret-looking names so manifests stay secret-free.
# TODO: return redacted RFC 9457-style error bodies once retry/audit semantics exist.


def _issue(path: str, message: str, code: str = "validation_error") -> ActionValidationIssue:
    return ActionValidationIssue(path=path, message=message, code=code)


def _http_config(request: ActionConnectorRequest) -> dict[str, Any]:
    config = request.config_json.get("http")
    if not isinstance(config, dict):
        raise ValidationError("http action config must include an http object")
    return config


def _static_headers(config: dict[str, Any]) -> dict[str, str]:
    headers = config.get("headers")
    if headers is None:
        return {}
    if not isinstance(headers, dict):
        raise ValidationError("http.headers must be an object")
    safe: dict[str, str] = {}
    for raw_key, raw_value in headers.items():
        key = str(raw_key)
        if _looks_secret_header(key):
            raise ValidationError("http.headers must not contain credential-like header names")
        if not isinstance(raw_value, str):
            raise ValidationError("http.headers values must be strings")
        safe[key] = raw_value
    return safe


def _looks_secret_header(key: str) -> bool:
    normalized = key.lower()
    return any(part in normalized for part in _SECRET_HEADER_PARTS)


def _method(config: dict[str, Any]) -> str:
    value = config.get("method", "POST")
    if not isinstance(value, str):
        raise ValidationError("http.method must be a string")
    method = value.upper()
    if method not in _ALLOWED_METHODS:
        raise ValidationError(f"http.method must be one of {sorted(_ALLOWED_METHODS)}")
    return method


def _request_mode(config: dict[str, Any], method: str) -> str:
    default_mode = "query" if method in {"GET", "DELETE"} else "json"
    value = config.get("request_mode", default_mode)
    if not isinstance(value, str) or value not in _ALLOWED_REQUEST_MODES:
        raise ValidationError("http.request_mode must be json or query")
    return value


def _response_mode(config: dict[str, Any]) -> str:
    value = config.get("response_mode", "auto")
    if not isinstance(value, str) or value not in _ALLOWED_RESPONSE_MODES:
        raise ValidationError("http.response_mode must be auto, json, or text")
    return value


def _timeout(config: dict[str, Any]) -> float:
    value = config.get("timeout_s", 30.0)
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0 or value > 120:
        raise ValidationError("http.timeout_s must be a number between 0 and 120")
    return float(value)


def _url(config: dict[str, Any]) -> str:
    value = config.get("url")
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("http.url is required")
    url = value.strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValidationError("http.url must be an absolute http or https URL")
    if parsed.username or parsed.password:
        raise ValidationError("http.url must not include embedded credentials")
    return url


def _auth_config(config: dict[str, Any]) -> dict[str, Any]:
    auth = config.get("auth", {"type": "none"})
    if not isinstance(auth, dict):
        raise ValidationError("http.auth must be an object")
    auth_type = auth.get("type", "none")
    if not isinstance(auth_type, str) or auth_type not in _ALLOWED_AUTH_TYPES:
        raise ValidationError("http.auth.type must be none, bearer, header, or basic")
    if auth_type == "header":
        header = auth.get("header_name")
        if not isinstance(header, str) or not header.strip():
            raise ValidationError("http.auth.header_name is required for header auth")
    return auth


def _credential_text(request: ActionConnectorRequest) -> str:
    if request.credential is None:
        raise ValidationError(f"{request.action_ref} requires a resolved credential")
    text = request.credential.secret_payload.decode("utf-8").strip()
    if not text:
        raise ValidationError(f"{request.action_ref} credential payload is empty")
    return text


def _basic_auth(text: str) -> httpx.BasicAuth:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        if ":" not in text:
            raise ValidationError(
                "basic auth credential must be JSON or username:password"
            ) from None
        username, password = text.split(":", 1)
    else:
        if not isinstance(parsed, dict):
            raise ValidationError("basic auth credential JSON must be an object")
        username = str(parsed.get("username") or parsed.get("user") or "")
        password = str(parsed.get("password") or parsed.get("secret") or "")
    if not username or not password:
        raise ValidationError("basic auth credential missing username/password")
    return httpx.BasicAuth(username, password)


def _query_params(payload: dict[str, Any]) -> dict[str, str | list[str]]:
    params: dict[str, str | list[str]] = {}
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, str | int | float | bool):
            params[key] = str(value)
        elif isinstance(value, list) and all(
            isinstance(item, str | int | float | bool) for item in value
        ):
            params[key] = [str(item) for item in value]
        else:
            raise ValidationError("query-mode HTTP action inputs must be primitive values")
    return params


class HttpActionConnector:
    """Decision-free adapter for static user-owned HTTP/Webhook actions."""

    key = "http"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        if request.operation != "request":
            return [
                _issue(
                    "$.operation",
                    f"unsupported operation {request.operation!r}",
                    "enum_mismatch",
                )
            ]
        issues: list[ActionValidationIssue] = []
        try:
            config = _http_config(request)
            method = _method(config)
            _url(config)
            _request_mode(config, method)
            _response_mode(config)
            _timeout(config)
            _static_headers(config)
            _auth_config(config)
        except ValidationError as exc:
            issues.append(_issue("$.config.http", str(exc), "config_invalid"))
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.operation != "request":
            raise ValidationError(f"unsupported HTTP operation {request.operation!r}")
        config = _http_config(request)
        method = _method(config)
        request_mode = _request_mode(config, method)
        response_mode = _response_mode(config)
        headers = _static_headers(config)
        auth: httpx.BasicAuth | None = None
        auth_config = _auth_config(config)
        auth_type = str(auth_config.get("type", "none"))
        if auth_type == "bearer":
            headers["Authorization"] = f"Bearer {_credential_text(request)}"
        elif auth_type == "header":
            headers[str(auth_config["header_name"])] = _credential_text(request)
        elif auth_type == "basic":
            auth = _basic_auth(_credential_text(request))

        kwargs: dict[str, Any] = {
            "method": method,
            "url": _url(config),
            "headers": headers or None,
            "auth": auth,
        }
        if request_mode == "query":
            kwargs["params"] = _query_params(request.input_json)
        else:
            kwargs["json"] = request.input_json

        async with httpx.AsyncClient(timeout=_timeout(config)) as http:
            response = await http.request(**kwargs)
        if response.status_code >= 400:
            raise ValidationError(f"http action returned status {response.status_code}")
        body: Any
        if response_mode == "text":
            body = response.text
        else:
            try:
                body = response.json()
            except ValueError:
                if response_mode == "json":
                    raise ValidationError("http action expected a JSON response") from None
                body = response.text
        return ActionConnectorResult(
            output_json={"status_code": response.status_code, "body": body},
            metadata_json={"vendor": "http", "operation": request.operation, "method": method},
        )


__all__ = ["HttpActionConnector"]
