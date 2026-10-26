"""SMTP action connector.

Official docs verified:
- SMTP core protocol: https://www.rfc-editor.org/rfc/rfc5321.html
- SMTP AUTH extension: https://www.rfc-editor.org/rfc/rfc4954
- Python smtplib adapter: https://docs.python.org/3/library/smtplib.html
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from email.utils import parseaddr
from typing import Any

from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.provider_utils import (
    credential_config,
    credential_payload,
    issue,
    unknown_operation,
)
from stackos.repositories.base import ValidationError
from stackos.repositories.resources import ResourceRepository
from stackos.secret_refs import SECRET_REF_SENTINEL

_MAX_RECIPIENTS = 100
_MAX_HEADER_COUNT = 50
_DISALLOWED_HEADERS = {
    "bcc",
    "cc",
    "cookie",
    "from",
    "subject",
    "to",
    "authorization",
    "set-cookie",
}
_TLS_MODES = {"starttls", "ssl", "none"}


class SmtpActionConnector:
    """Decision-free adapter for explicit SMTP send calls."""

    key = "smtp"

    def __init__(self, *, client=None, options=None) -> None:
        self._client = client
        self._options = options

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        if request.operation != "email.send":
            return unknown_operation(request)
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        _recipient_list(payload, "recipients", issues, required=True)
        _recipient_list(payload, "cc", issues)
        _recipient_list(payload, "bcc", issues)
        _text(payload, "subject", issues, required=True, max_chars=998)
        _text(payload, "text", issues, max_chars=2_000_000)
        _text(payload, "html", issues, max_chars=2_000_000)
        _text(payload, "from_ref", issues, max_chars=320)
        _text(payload, "reply_to", issues, max_chars=320)
        _optional_int(payload, "source_agent_request_id", issues, minimum=1)
        _headers(payload.get("headers"), issues)
        if not _has_nonempty_text(payload.get("text")) and not _has_nonempty_text(
            payload.get("html")
        ):
            issues.append(issue("$", "text or html is required", "required"))
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.operation != "email.send":
            raise ValidationError(f"unsupported SMTP operation {request.operation!r}")
        from stackos_connectors import ConnectorClient
        from stackos_connectors.catalog import load_registry

        from stackos.actions.package_bridge import PackageActionConnector

        settings = _smtp_settings(request)
        prepared = {
            key: value
            for key, value in request.input_json.items()
            if key not in {"from_ref", "source_agent_request_id"}
        }
        prepared["from_email"] = _from_email(request.input_json.get("from_ref"), settings)
        prepared["from_name"] = settings["from_name"]
        reply_to = _reply_to(request.input_json.get("reply_to"), settings)
        if reply_to:
            prepared["reply_to"] = reply_to
        recipients = sum(
            len(_email_list(prepared.get(key), key)) for key in ("recipients", "cc", "bcc")
        )
        if recipients > _MAX_RECIPIENTS:
            raise ValidationError(f"SMTP email can target at most {_MAX_RECIPIENTS} recipients")
        if self._client is None:
            self._client = ConnectorClient(registry=load_registry("connectors/smtp/catalog.json"))
        result = await PackageActionConnector(
            "smtp", client=self._client, options=self._options
        ).execute(replace(request, input_json=prepared))
        output = result.output_json
        output["message_ref"] = f"smtp-message:{output['message_id']}"
        output["from_ref"] = f"smtp-from:{output.pop('from_email')}"
        try:
            _store_outbound_email(
                request,
                message_id=str(output["message_id"]),
                from_ref=str(output["from_ref"]),
                accepted=output.get("accepted_recipients") or [],
                rejected=output.get("rejected_recipients") or {},
            )
        except Exception as exc:
            raise ActionConnectorError(
                "SMTP submission completed but outbound history could not be stored",
                output_json=output,
                metadata_json={
                    **(result.metadata_json or {}),
                    "provider_executed": True,
                    "retry_safe": False,
                },
            ) from exc
        return result


def _smtp_settings(request: ActionConnectorRequest) -> dict[str, Any]:
    config = credential_config(request)
    payload = credential_payload(request)
    host = _config_text(config, payload, "host", required=True)
    username = _config_text(config, payload, "username", "user", required=True)
    from_email = _config_text(config, payload, "from_email", required=True)
    port = _config_int(config, payload, "port", default=465)
    tls_mode = _config_text(config, payload, "tls_mode", default="ssl").lower()
    if tls_mode not in _TLS_MODES:
        raise ValidationError("smtp credential tls_mode must be starttls, ssl, or none")
    if not _is_email(from_email):
        raise ValidationError("smtp credential from_email must be a valid email address")
    return {
        "host": host,
        "port": port,
        "tls_mode": tls_mode,
        "username": username,
        "from_email": from_email,
        "from_name": _config_text(config, payload, "from_name", default=""),
        "reply_to": _config_text(config, payload, "reply_to", default=""),
        "timeout_s": float(_config_int(config, payload, "timeout_s", default=30)),
        "from_refs": _mapping(config.get("from_refs")),
        "allowed_reply_to": _string_set(config.get("allowed_reply_to")),
    }


def _store_outbound_email(
    request: ActionConnectorRequest,
    *,
    message_id: str,
    from_ref: str,
    accepted: Sequence[str],
    rejected: Mapping[str, Any],
) -> None:
    if request.session is None:
        return
    payload = request.input_json
    ResourceRepository(request.session).upsert_record(
        project_id=request.project_id,
        plugin_slug="communications",
        resource_key="communication-message",
        external_id=f"smtp-message:{message_id}",
        title=str(payload.get("subject") or "SMTP outbound email"),
        data_json={
            "provider_key": "smtp",
            "direction": "outbound",
            "channel_ref": from_ref,
            "message_ref": f"smtp-message:{message_id}",
            "message_id": message_id,
            "content_type": "email",
            "subject": payload.get("subject"),
            "to": list(payload.get("recipients") or []),
            "cc": list(payload.get("cc") or []),
            "accepted_recipients": list(accepted),
            "rejected_recipients": dict(rejected),
            "attention_status": "sent" if accepted else "rejected",
            "source_agent_request_id": payload.get("source_agent_request_id"),
            "action_ref": request.action_ref,
        },
        provenance_json={"source": "smtp-action"},
    )


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
        raise ValidationError(f"smtp credential missing {keys[0]}")
    return default or ""


def _config_int(
    config: Mapping[str, Any],
    payload: Mapping[str, Any],
    key: str,
    *,
    default: int,
) -> int:
    raw = config.get(key, payload.get(key, default))
    if isinstance(raw, bool):
        raise ValidationError(f"smtp credential {key} must be an integer")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"smtp credential {key} must be an integer") from exc
    if value < 1 or value > 65_535:
        raise ValidationError(f"smtp credential {key} must be between 1 and 65535")
    return value


def _recipient_list(
    payload: Mapping[str, Any],
    key: str,
    issues: list[ActionValidationIssue],
    *,
    required: bool = False,
) -> None:
    value = payload.get(key)
    if value is None:
        if required:
            issues.append(issue(f"$.{key}", f"{key} is required", "required"))
        return
    if not isinstance(value, list) or not value:
        issues.append(issue(f"$.{key}", f"{key} must be a non-empty array", "type_error"))
        return
    if len(value) > _MAX_RECIPIENTS:
        issues.append(issue(f"$.{key}", f"{key} must contain at most {_MAX_RECIPIENTS} items"))
    for index, item in enumerate(value):
        # Preflight projects refs without decryption; _email_list validates resolved values.
        if item == SECRET_REF_SENTINEL:
            continue
        if not isinstance(item, str) or not _is_email(item):
            issues.append(issue(f"$.{key}[{index}]", "must be a valid email address", "format"))


def _email_list(value: Any, path: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError(f"{path} must be an array")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _is_email(item):
            raise ValidationError(f"{path} contains an invalid email address")
        out.append(parseaddr(item)[1])
    return out


def _text(
    payload: Mapping[str, Any],
    key: str,
    issues: list[ActionValidationIssue],
    *,
    required: bool = False,
    max_chars: int | None = None,
) -> None:
    value = payload.get(key)
    if value is None:
        if required:
            issues.append(issue(f"$.{key}", f"{key} is required", "required"))
        return
    if not isinstance(value, str) or not value.strip():
        issues.append(issue(f"$.{key}", f"{key} must be a non-empty string", "type_error"))
        return
    if _has_crlf(value) and key in {"from_ref", "reply_to"}:
        issues.append(issue(f"$.{key}", f"{key} cannot contain CR/LF", "format"))
    if max_chars is not None and len(value) > max_chars:
        issues.append(issue(f"$.{key}", f"{key} must be at most {max_chars} characters", "length"))


def _headers(value: Any, issues: list[ActionValidationIssue]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        issues.append(issue("$.headers", "headers must be an object", "type_error"))
        return
    if len(value) > _MAX_HEADER_COUNT:
        issues.append(issue("$.headers", f"headers may contain at most {_MAX_HEADER_COUNT} items"))
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            issues.append(issue("$.headers", "header names and values must be strings"))
            continue
        if _has_crlf(key) or _has_crlf(item):
            issues.append(issue("$.headers", "headers cannot contain CR/LF", "format"))
        if key.lower() in _DISALLOWED_HEADERS:
            issues.append(issue(f"$.headers.{key}", f"{key} is managed by StackOS", "forbidden"))


def _clean_headers(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError("headers must be an object")
    out: dict[str, str] = {}
    for key, item in value.items():
        if (
            not isinstance(key, str)
            or not isinstance(item, str)
            or _has_crlf(key)
            or _has_crlf(item)
            or key.lower() in _DISALLOWED_HEADERS
        ):
            raise ValidationError("headers contain an invalid or managed header")
        out[key] = item
    return out


def _optional_int(
    payload: Mapping[str, Any],
    key: str,
    issues: list[ActionValidationIssue],
    *,
    minimum: int,
) -> None:
    value = payload.get(key)
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        issues.append(issue(f"$.{key}", f"{key} must be an integer >= {minimum}", "range"))


def _from_email(value: Any, settings: Mapping[str, Any]) -> str:
    configured = str(settings["from_email"])
    if value is None:
        return configured
    raw = str(value).strip()
    refs = settings.get("from_refs") if isinstance(settings.get("from_refs"), Mapping) else {}
    mapped = refs.get(raw) if isinstance(refs, Mapping) else None
    candidate = str(mapped or raw.removeprefix("smtp-from:"))
    if candidate != configured:
        raise ValidationError("from_ref must resolve to the configured SMTP from_email")
    return configured


def _reply_to(value: Any, settings: Mapping[str, Any]) -> str:
    configured = str(settings.get("reply_to") or "")
    if value is None:
        return configured
    candidate = str(value).strip()
    allowed = set(settings.get("allowed_reply_to") or set())
    if configured:
        allowed.add(configured)
    if str(settings.get("from_email") or ""):
        allowed.add(str(settings["from_email"]))
    if candidate not in allowed:
        raise ValidationError("reply_to must be configured as an allowed SMTP reply address")
    if not _is_email(candidate):
        raise ValidationError("reply_to must be a valid email address")
    return candidate


def _is_email(value: str) -> bool:
    if _has_crlf(value):
        return False
    _name, address = parseaddr(value)
    return bool(address and "@" in address and parseaddr(address)[1] == address)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string_set(value: Any) -> set[str]:
    if isinstance(value, str):
        return {item.strip() for item in value.split(",") if item.strip()}
    if isinstance(value, list):
        return {str(item).strip() for item in value if str(item).strip()}
    return set()


def _has_nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_crlf(value: str) -> bool:
    return "\r" in value or "\n" in value


__all__ = ["SmtpActionConnector"]
