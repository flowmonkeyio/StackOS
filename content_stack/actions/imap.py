"""IMAP action connector.

Official docs verified:
- IMAP4rev2 protocol: https://www.rfc-editor.org/rfc/rfc9051.html
- Python imaplib adapter: https://docs.python.org/3/library/imaplib.html
"""

from __future__ import annotations

import asyncio
import imaplib
import re
from collections.abc import Mapping, Sequence
from contextlib import suppress
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import getaddresses
from typing import Any

from content_stack.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from content_stack.actions.provider_utils import (
    credential_config,
    credential_payload,
    credential_value,
    issue,
    unknown_operation,
)
from content_stack.repositories.base import ValidationError
from content_stack.repositories.resources import ResourceRepository

_TLS_MODES = {"ssl", "starttls", "none"}
_MAX_LIMIT = 500
_DEFAULT_LIMIT = 50
_DEFAULT_BODY_BYTES = 64_000
_MAX_BODY_BYTES = 1_048_576
_MESSAGE_FIELDS = {
    "subject",
    "from",
    "to",
    "cc",
    "date",
    "message_id",
    "text_preview",
    "html_preview",
    "body_text",
    "body_html",
    "flags",
    "headers",
}
_TEXT_CRITERIA = {"from", "to", "subject", "text"}
_DATE_RE = re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{4}$")


class ImapActionConnector:
    """Decision-free adapter for explicit IMAP mailbox calls."""

    key = "imap"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case "mailbox.list":
                return []
            case "messages.search":
                _text(payload, "mailbox_ref", issues, required=True)
                _optional_int(payload, "limit", issues, minimum=1, maximum=_MAX_LIMIT)
                _criteria(payload.get("criteria"), issues)
            case "message.fetch":
                _text(payload, "mailbox_ref", issues, required=True)
                _optional_int(payload, "uid", issues, minimum=1, required=True)
                _fields(payload.get("fields"), issues)
                _optional_int(payload, "max_body_bytes", issues, minimum=0, maximum=_MAX_BODY_BYTES)
            case "message.mark_seen" | "message.mark_unseen":
                _text(payload, "mailbox_ref", issues, required=True)
                _optional_int(payload, "uid", issues, minimum=1, required=True)
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        match request.operation:
            case "mailbox.list":
                result = await asyncio.to_thread(_list_mailboxes, request)
                _store_mailboxes(request, result.output_json.get("mailboxes") or [])
                return result
            case "messages.search":
                result = await asyncio.to_thread(_search_messages, request)
                _store_cursor(request, result.output_json)
                return result
            case "message.fetch":
                result = await asyncio.to_thread(_fetch_message, request)
                _store_inbound_message(request, result.output_json)
                return result
            case "message.mark_seen":
                result = await asyncio.to_thread(_mark_message, request, seen=True)
                _store_message_status(request, result.output_json)
                return result
            case "message.mark_unseen":
                result = await asyncio.to_thread(_mark_message, request, seen=False)
                _store_message_status(request, result.output_json)
                return result
            case _:
                raise ValidationError(f"unsupported IMAP operation {request.operation!r}")


def _list_mailboxes(request: ActionConnectorRequest) -> ActionConnectorResult:
    settings = _imap_settings(request)
    client = _login(settings)
    try:
        # IMAP LIST command: https://www.rfc-editor.org/rfc/rfc9051.html#name-list-command
        typ, data = client.list()
        _ensure_ok(typ, "LIST")
        mailboxes = [_parse_list_line(line) for line in data or [] if line]
        return _connector_result(
            request,
            {
                "mailboxes": mailboxes,
                "mailbox_count": len(mailboxes),
            },
            settings,
        )
    finally:
        _logout(client)


def _search_messages(request: ActionConnectorRequest) -> ActionConnectorResult:
    settings = _imap_settings(request)
    mailbox = _mailbox_name(request, settings)
    limit = int(request.input_json.get("limit") or settings.get("search_limit") or _DEFAULT_LIMIT)
    client = _login(settings)
    try:
        readonly_select = _select(client, mailbox, readonly=True)
        criteria = _search_criteria(request.input_json.get("criteria"))
        # IMAP UID SEARCH command:
        # https://www.rfc-editor.org/rfc/rfc9051.html#name-uid-command
        typ, data = client.uid("SEARCH", None, *criteria)
        _ensure_ok(typ, "UID SEARCH")
        uids = _uid_list(data)[:limit]
        result = {
            "mailbox_ref": _mailbox_ref(mailbox),
            "mailbox_name": mailbox,
            "uidvalidity": readonly_select.get("uidvalidity"),
            "criteria": criteria,
            "uids": uids,
            "message_refs": [f"imap-message:{mailbox}:{uid}" for uid in uids],
            "count": len(uids),
            "limit": limit,
        }
        return _connector_result(request, result, settings)
    finally:
        _logout(client)


def _fetch_message(request: ActionConnectorRequest) -> ActionConnectorResult:
    settings = _imap_settings(request)
    mailbox = _mailbox_name(request, settings)
    uid = int(request.input_json["uid"])
    fields = _requested_fields(request.input_json.get("fields"))
    max_body_bytes = int(request.input_json.get("max_body_bytes") or _DEFAULT_BODY_BYTES)
    client = _login(settings)
    try:
        select_data = _select(client, mailbox, readonly=True)
        # Use UID FETCH and BODY.PEEK so reads do not mutate \\Seen.
        # https://www.rfc-editor.org/rfc/rfc9051.html#name-fetch-command
        typ, data = client.uid(
            "FETCH",
            str(uid),
            f"(UID FLAGS RFC822.SIZE BODY.PEEK[]<0.{max_body_bytes}>)",
        )
        _ensure_ok(typ, "UID FETCH")
        raw, flags, size = _fetch_payload(data)
        if raw is None:
            raise ValidationError(f"IMAP message UID {uid} was not found")
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
        message = _message_output(
            parsed,
            fields=fields,
            mailbox=mailbox,
            uid=uid,
            uidvalidity=select_data.get("uidvalidity"),
            flags=flags,
            size=size,
            max_body_bytes=max_body_bytes,
        )
        return _connector_result(request, message, settings)
    finally:
        _logout(client)


def _mark_message(
    request: ActionConnectorRequest,
    *,
    seen: bool,
) -> ActionConnectorResult:
    settings = _imap_settings(request)
    mailbox = _mailbox_name(request, settings)
    uid = int(request.input_json["uid"])
    client = _login(settings)
    try:
        _select(client, mailbox, readonly=False)
        op = "+FLAGS" if seen else "-FLAGS"
        # IMAP STORE command for \\Seen lifecycle:
        # https://www.rfc-editor.org/rfc/rfc9051.html#name-store-command
        typ, data = client.uid("STORE", str(uid), op, "(\\Seen)")
        _ensure_ok(typ, f"UID STORE {op}")
        result = {
            "mailbox_ref": _mailbox_ref(mailbox),
            "mailbox_name": mailbox,
            "uid": uid,
            "message_ref": f"imap-message:{mailbox}:{uid}",
            "attention_status": "read" if seen else "unread",
            "store_response": [_safe_decode(item) for item in data or [] if item],
        }
        return _connector_result(request, result, settings)
    finally:
        _logout(client)


def _imap_settings(request: ActionConnectorRequest) -> dict[str, Any]:
    config = credential_config(request)
    payload = credential_payload(request)
    host = _config_text(config, payload, "host", required=True)
    username = _config_text(config, payload, "username", "user", required=True)
    port = _config_int(config, payload, "port", default=993)
    tls_mode = _config_text(config, payload, "tls_mode", default="ssl").lower()
    if tls_mode not in _TLS_MODES:
        raise ValidationError("imap credential tls_mode must be ssl, starttls, or none")
    default_mailbox = _config_text(config, payload, "default_mailbox", default="INBOX")
    return {
        "host": host,
        "port": port,
        "tls_mode": tls_mode,
        "username": username,
        "password": credential_value(request, "password", "secret"),
        "timeout_s": float(_config_int(config, payload, "timeout_s", default=30)),
        "default_mailbox": default_mailbox,
        "mailbox_refs": _mailbox_ref_map(config.get("mailbox_refs")),
        "search_limit": _config_int(config, payload, "search_limit", default=_DEFAULT_LIMIT),
    }


def _login(settings: Mapping[str, Any]) -> Any:
    host = str(settings["host"])
    port = int(settings["port"])
    timeout = float(settings["timeout_s"])
    client: Any
    try:
        if settings["tls_mode"] == "ssl":
            client = imaplib.IMAP4_SSL(host, port, timeout=timeout)
        else:
            client = imaplib.IMAP4(host, port, timeout=timeout)
    except TypeError:
        if settings["tls_mode"] == "ssl":
            client = imaplib.IMAP4_SSL(host, port)
        else:
            client = imaplib.IMAP4(host, port)
    if settings["tls_mode"] == "starttls":
        client.starttls()
    client.login(str(settings["username"]), str(settings["password"]))
    return client


def _logout(client: Any) -> None:
    with suppress(Exception):
        client.close()
    with suppress(Exception):
        client.logout()


def _select(client: Any, mailbox: str, *, readonly: bool) -> dict[str, Any]:
    typ, _data = client.select(mailbox, readonly=readonly)
    _ensure_ok(typ, "SELECT")
    uidvalidity = None
    try:
        response = client.response("UIDVALIDITY")
    except Exception:
        response = None
    if isinstance(response, tuple) and len(response) == 2:
        values = response[1]
        if isinstance(values, list) and values:
            uidvalidity = _safe_decode(values[0])
    return {"uidvalidity": uidvalidity}


def _mailbox_name(
    request: ActionConnectorRequest,
    settings: Mapping[str, Any],
) -> str:
    raw = str(request.input_json.get("mailbox_ref") or "default").strip()
    refs = settings.get("mailbox_refs") if isinstance(settings.get("mailbox_refs"), Mapping) else {}
    if raw in {"default", "imap-mailbox:default"}:
        return str(settings["default_mailbox"])
    if isinstance(refs, Mapping) and raw in refs:
        return str(refs[raw])
    if raw.startswith("imap-mailbox:"):
        return raw.removeprefix("imap-mailbox:")
    if not refs:
        return raw
    raise ValidationError(f"mailbox_ref {raw!r} is not configured for this IMAP credential")


def _mailbox_ref(mailbox: str) -> str:
    return f"imap-mailbox:{mailbox}"


def _mailbox_ref_map(value: Any) -> dict[str, str]:
    if isinstance(value, Mapping):
        return {str(key): str(item) for key, item in value.items()}
    if isinstance(value, str):
        out: dict[str, str] = {}
        for part in value.split(","):
            item = part.strip()
            if not item:
                continue
            if ":" in item:
                key, mailbox = item.split(":", 1)
                out[key.strip()] = mailbox.strip()
            else:
                out[item] = item
        return out
    return {}


def _search_criteria(raw: Any) -> list[str]:
    if raw is None:
        return ["ALL"]
    if not isinstance(raw, Mapping):
        raise ValidationError("criteria must be an object")
    criteria: list[str] = []
    if raw.get("unseen") is True:
        criteria.append("UNSEEN")
    if raw.get("seen") is True:
        criteria.append("SEEN")
    for key in ("since", "before"):
        value = raw.get(key)
        if value is not None:
            text = str(value).strip()
            if not _DATE_RE.match(text):
                raise ValidationError(f"criteria.{key} must use IMAP date format DD-Mon-YYYY")
            criteria.extend([key.upper(), text])
    for key in _TEXT_CRITERIA:
        value = raw.get(key)
        if value is not None:
            text = str(value).strip()
            if not text or _has_crlf(text):
                raise ValidationError(f"criteria.{key} must be non-empty text without CR/LF")
            criteria.extend([key.upper() if key != "text" else "TEXT", text])
    uid_from = raw.get("uid_from")
    uid_to = raw.get("uid_to")
    if uid_from is not None or uid_to is not None:
        start = _positive_int(uid_from or 1, "criteria.uid_from")
        end = _positive_int(uid_to or "*", "criteria.uid_to", allow_star=True)
        criteria.extend(["UID", f"{start}:{end}"])
    return criteria or ["ALL"]


def _uid_list(data: Sequence[Any] | None) -> list[int]:
    if not data:
        return []
    text = " ".join(_safe_decode(item) for item in data if item)
    out: list[int] = []
    for part in text.split():
        if part.isdigit():
            out.append(int(part))
    return out


def _fetch_payload(data: Sequence[Any] | None) -> tuple[bytes | None, list[str], int | None]:
    if not data:
        return None, [], None
    raw: bytes | None = None
    flags: list[str] = []
    size: int | None = None
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2:
            meta = _safe_decode(item[0])
            if isinstance(item[1], bytes):
                raw = item[1]
            flags = _parse_flags(meta)
            size = _parse_size(meta)
        elif isinstance(item, bytes):
            flags.extend(_parse_flags(_safe_decode(item)))
    return raw, sorted(set(flags)), size


def _message_output(
    message: Message,
    *,
    fields: set[str],
    mailbox: str,
    uid: int,
    uidvalidity: str | None,
    flags: list[str],
    size: int | None,
    max_body_bytes: int,
) -> dict[str, Any]:
    text_body, html_body = _message_bodies(message, max_body_bytes=max_body_bytes)
    headers = {
        key: str(message.get(key) or "")
        for key in ("Subject", "From", "To", "Cc", "Date", "Message-ID")
        if message.get(key) is not None
    }
    base: dict[str, Any] = {
        "mailbox_ref": _mailbox_ref(mailbox),
        "mailbox_name": mailbox,
        "uid": uid,
        "uidvalidity": uidvalidity,
        "message_ref": f"imap-message:{mailbox}:{uid}",
        "size_bytes": size,
    }
    candidates = {
        "subject": str(message.get("Subject") or ""),
        "from": _addresses(message.get_all("From", [])),
        "to": _addresses(message.get_all("To", [])),
        "cc": _addresses(message.get_all("Cc", [])),
        "date": str(message.get("Date") or ""),
        "message_id": str(message.get("Message-ID") or ""),
        "text_preview": text_body[:500],
        "html_preview": html_body[:500],
        "body_text": text_body,
        "body_html": html_body,
        "flags": flags,
        "headers": headers,
    }
    for key in fields:
        base[key] = candidates[key]
    return base


def _requested_fields(raw: Any) -> set[str]:
    if raw is None:
        return {"subject", "from", "to", "date", "message_id", "text_preview", "flags"}
    if not isinstance(raw, list) or not raw:
        raise ValidationError("fields must be a non-empty array")
    fields = {str(item) for item in raw}
    invalid = fields - _MESSAGE_FIELDS
    if invalid:
        raise ValidationError(f"unsupported IMAP message fields: {', '.join(sorted(invalid))}")
    return fields


def _message_bodies(message: Message, *, max_body_bytes: int) -> tuple[str, str]:
    text = ""
    html = ""
    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            if content_type == "text/plain" and not text:
                text = _part_text(part, max_body_bytes=max_body_bytes)
            elif content_type == "text/html" and not html:
                html = _part_text(part, max_body_bytes=max_body_bytes)
    elif isinstance(message, EmailMessage):
        if message.get_content_type() == "text/html":
            html = _part_text(message, max_body_bytes=max_body_bytes)
        else:
            text = _part_text(message, max_body_bytes=max_body_bytes)
    else:
        payload = message.get_payload(decode=True)
        text = _safe_decode(payload)[:max_body_bytes] if payload else ""
    return text[:max_body_bytes], html[:max_body_bytes]


def _part_text(part: Message, *, max_body_bytes: int) -> str:
    if isinstance(part, EmailMessage):
        try:
            content = part.get_content()
        except Exception:
            raw = part.get_payload(decode=True)
            return _safe_decode(raw)[:max_body_bytes] if raw else ""
        if isinstance(content, bytes):
            return _safe_decode(content)[:max_body_bytes]
        return str(content)[:max_body_bytes]
    raw = part.get_payload(decode=True)
    return _safe_decode(raw)[:max_body_bytes] if raw else ""


def _addresses(values: Sequence[str]) -> list[str]:
    return [address for _name, address in getaddresses(values) if address]


def _parse_list_line(value: Any) -> dict[str, Any]:
    text = _safe_decode(value)
    flags = re.findall(r"\\[A-Za-z]+", text)
    mailbox = text.split(' "/" ')[-1].strip().strip('"') if ' "/" ' in text else text.split()[-1]
    return {
        "mailbox_ref": _mailbox_ref(mailbox),
        "name": mailbox,
        "flags": flags,
    }


def _parse_flags(text: str) -> list[str]:
    match = re.search(r"FLAGS \(([^)]*)\)", text, flags=re.IGNORECASE)
    if match is None:
        return []
    return [part for part in match.group(1).split() if part]


def _parse_size(text: str) -> int | None:
    match = re.search(r"RFC822\.SIZE\s+(\d+)", text, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _store_mailboxes(request: ActionConnectorRequest, mailboxes: list[dict[str, Any]]) -> None:
    if request.session is None:
        return
    resources = ResourceRepository(request.session)
    for item in mailboxes:
        name = str(item["name"])
        resources.upsert_record(
            project_id=request.project_id,
            plugin_slug="communications",
            resource_key="communication-channel",
            external_id=f"imap-mailbox:{name}",
            title=name,
            data_json={
                "provider_key": "imap",
                "channel_type": "mailbox",
                "mailbox_ref": item["mailbox_ref"],
                "mailbox_name": name,
                "flags": item.get("flags", []),
            },
            provenance_json={"source": "imap-action"},
        )


def _store_cursor(request: ActionConnectorRequest, result: Mapping[str, Any]) -> None:
    if request.session is None:
        return
    ResourceRepository(request.session).upsert_record(
        project_id=request.project_id,
        plugin_slug="communications",
        resource_key="communication-cursor",
        external_id=f"imap-cursor:{result['mailbox_name']}",
        title=f"IMAP cursor {result['mailbox_name']}",
        data_json={
            "provider_key": "imap",
            "mailbox_ref": result["mailbox_ref"],
            "mailbox_name": result["mailbox_name"],
            "uidvalidity": result.get("uidvalidity"),
            "last_seen_uid": max(result.get("uids") or [0]),
            "last_search_count": result.get("count"),
        },
        provenance_json={"source": "imap-action"},
    )


def _store_inbound_message(request: ActionConnectorRequest, message: Mapping[str, Any]) -> None:
    if request.session is None:
        return
    uid = message["uid"]
    mailbox = message["mailbox_name"]
    ResourceRepository(request.session).upsert_record(
        project_id=request.project_id,
        plugin_slug="communications",
        resource_key="communication-message",
        external_id=f"imap-message:{mailbox}:{uid}",
        title=str(message.get("subject") or f"IMAP message {uid}"),
        data_json={
            "provider_key": "imap",
            "direction": "inbound",
            "channel_ref": message["mailbox_ref"],
            "message_ref": message["message_ref"],
            "uid": uid,
            "uidvalidity": message.get("uidvalidity"),
            "subject": message.get("subject"),
            "from": message.get("from", []),
            "to": message.get("to", []),
            "date": message.get("date"),
            "message_id": message.get("message_id"),
            "text_preview": message.get("text_preview"),
            "flags": message.get("flags", []),
            "attention_status": "read" if "\\Seen" in message.get("flags", []) else "unread",
            "action_ref": request.action_ref,
        },
        provenance_json={"source": "imap-action"},
    )


def _store_message_status(request: ActionConnectorRequest, result: Mapping[str, Any]) -> None:
    if request.session is None:
        return
    ResourceRepository(request.session).upsert_record(
        project_id=request.project_id,
        plugin_slug="communications",
        resource_key="communication-event",
        external_id=f"imap-event:{result['mailbox_name']}:{result['uid']}:{result['attention_status']}",
        title=f"IMAP message {result['uid']} {result['attention_status']}",
        data_json={
            "provider_key": "imap",
            "event_type": "message_flag_changed",
            "mailbox_ref": result["mailbox_ref"],
            "message_ref": result["message_ref"],
            "attention_status": result["attention_status"],
            "action_ref": request.action_ref,
        },
        provenance_json={"source": "imap-action"},
    )


def _connector_result(
    request: ActionConnectorRequest,
    body: dict[str, Any],
    settings: Mapping[str, Any],
) -> ActionConnectorResult:
    return ActionConnectorResult(
        output_json={
            "provider": "imap",
            "operation": request.operation,
            "status": "success",
            **body,
        },
        metadata_json={
            "vendor": "imap",
            "operation": request.operation,
            "tls_mode": settings["tls_mode"],
            "host": settings["host"],
            "port": settings["port"],
        },
    )


def _ensure_ok(status: Any, label: str) -> None:
    if str(status).upper() != "OK":
        raise ValidationError(f"IMAP {label} failed with status {status!r}")


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
        raise ValidationError(f"imap credential missing {keys[0]}")
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
        raise ValidationError(f"imap credential {key} must be an integer")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"imap credential {key} must be an integer") from exc
    if value < 1 or value > 65_535:
        raise ValidationError(f"imap credential {key} must be between 1 and 65535")
    return value


def _text(
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
    if not isinstance(value, str) or not value.strip() or _has_crlf(value):
        issues.append(issue(f"$.{key}", f"{key} must be text without CR/LF", "format"))


def _optional_int(
    payload: Mapping[str, Any],
    key: str,
    issues: list[ActionValidationIssue],
    *,
    minimum: int,
    maximum: int | None = None,
    required: bool = False,
) -> None:
    value = payload.get(key)
    if value is None:
        if required:
            issues.append(issue(f"$.{key}", f"{key} is required", "required"))
        return
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        issues.append(issue(f"$.{key}", f"{key} must be an integer >= {minimum}", "range"))
        return
    if maximum is not None and value > maximum:
        issues.append(issue(f"$.{key}", f"{key} must be <= {maximum}", "range"))


def _criteria(value: Any, issues: list[ActionValidationIssue]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        issues.append(issue("$.criteria", "criteria must be an object", "type_error"))
        return
    allowed = {"unseen", "seen", "since", "before", "uid_from", "uid_to", *_TEXT_CRITERIA}
    for key, item in value.items():
        if key not in allowed:
            issues.append(issue(f"$.criteria.{key}", f"unsupported criteria {key}", "forbidden"))
        elif key in {"unseen", "seen"}:
            if not isinstance(item, bool):
                issues.append(issue(f"$.criteria.{key}", f"{key} must be boolean", "type_error"))
        elif key in {"uid_from", "uid_to"}:
            if item != "*" and (not isinstance(item, int) or isinstance(item, bool) or item < 1):
                issues.append(issue(f"$.criteria.{key}", f"{key} must be a positive integer"))
        elif key in {"since", "before"}:
            if (
                not isinstance(item, str)
                or not item.strip()
                or _has_crlf(item)
                or not _DATE_RE.match(item.strip())
            ):
                issues.append(
                    issue(
                        f"$.criteria.{key}",
                        f"{key} must use IMAP date format DD-Mon-YYYY",
                    )
                )
        elif not isinstance(item, str) or not item.strip() or _has_crlf(item):
            issues.append(issue(f"$.criteria.{key}", f"{key} must be safe text"))


def _fields(value: Any, issues: list[ActionValidationIssue]) -> None:
    if value is None:
        return
    if not isinstance(value, list) or not value:
        issues.append(issue("$.fields", "fields must be a non-empty array", "type_error"))
        return
    invalid = {
        str(item) for item in value if not isinstance(item, str) or item not in _MESSAGE_FIELDS
    }
    if invalid:
        issues.append(issue("$.fields", f"unsupported fields: {', '.join(sorted(invalid))}"))


def _positive_int(value: Any, label: str, *, allow_star: bool = False) -> int | str:
    if allow_star and value == "*":
        return "*"
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValidationError(f"{label} must be a positive integer")
    return value


def _safe_decode(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _has_crlf(value: str) -> bool:
    return "\r" in value or "\n" in value


__all__ = ["ImapActionConnector"]
