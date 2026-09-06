"""IMAP action connector.

Official docs verified:
- IMAP4rev2 protocol: https://www.rfc-editor.org/rfc/rfc9051.html
- Python imaplib adapter: https://docs.python.org/3/library/imaplib.html
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import imaplib
import os
import re
import shutil
from collections.abc import Mapping, Sequence
from contextlib import suppress
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import getaddresses
from pathlib import Path
from typing import Any
from uuid import uuid4

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
from stackos.config import Settings
from stackos.integrations.imap import imap_ssl_context
from stackos.repositories.base import ValidationError
from stackos.repositories.resources import ResourceRepository

_TLS_MODES = {"ssl", "starttls", "none"}
_MAX_LIMIT = 500
_DEFAULT_LIMIT = 50
_DEFAULT_BODY_BYTES = 64_000
_MAX_BODY_BYTES = 1_048_576
_MAX_EXPORT_MESSAGE_BYTES = 10 * 1024 * 1024
_MAX_EXPORT_ATTACHMENTS = 20
_MAX_EXPORT_ATTACHMENT_BYTES = 8 * 1024 * 1024
_MAX_EXPORT_ATTACHMENT_TOTAL_BYTES = 10 * 1024 * 1024
_MAX_EXPORT_MIME_PARTS = 200
_MAX_EXPORT_MIME_DEPTH = 20
_MAX_UIDVALIDITY = 4_294_967_295
_MAX_MAILBOX_NAME_CHARS = 240
_TRANSFER_ID_RE = re.compile(r"^[a-f0-9]{32}$")
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
            case "message.export":
                _text(payload, "mailbox_ref", issues, required=True)
                _optional_int(payload, "uid", issues, minimum=1, required=True)
            case "message.export.cleanup":
                _transfer_id(payload, issues)
            case "message.mark_seen" | "message.mark_unseen":
                _text(payload, "mailbox_ref", issues, required=True)
                _optional_int(payload, "uid", issues, minimum=1, required=True)
                _optional_uidvalidity(payload, issues)
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
            case "message.export":
                return await asyncio.to_thread(_export_message, request)
            case "message.export.cleanup":
                return await asyncio.to_thread(_cleanup_export, request)
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
        raw, flags, size = _fetch_payload(data, uid=uid)
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


def _export_message(request: ActionConnectorRequest) -> ActionConnectorResult:
    """Stage one exact RFC822 message without creating StackOS evidence state."""
    settings = _imap_settings(request)
    client: Any | None = None
    transfer_dir: Path | None = None
    transfer_id: str | None = None
    asset_root: Path | None = None
    try:
        _require_export_tls(settings)
        mailbox = _mailbox_name(request, settings)
        uid = int(request.input_json["uid"])
        client = _login(settings)
        selected = _select(client, mailbox, readonly=True)
        uidvalidity = _required_uidvalidity(selected.get("uidvalidity"))
        # RFC822.SIZE is deliberately fetched before the full BODY.PEEK literal.
        typ, preflight = client.uid("FETCH", str(uid), "(UID RFC822.SIZE)")
        _ensure_export_ok(typ)
        preflight_size, _unused_literal = _export_fetch_tuple(
            preflight,
            uid=uid,
            require_literal=False,
        )
        if preflight_size > _MAX_EXPORT_MESSAGE_BYTES:
            raise _export_error(
                "oversize",
                "IMAP message exceeds the 10 MiB evidence export limit",
                details={"size_bytes": preflight_size, "max_bytes": _MAX_EXPORT_MESSAGE_BYTES},
            )

        # BODY.PEEK and readonly SELECT ensure exporting never changes \Seen.
        typ, fetched = client.uid("FETCH", str(uid), "(UID RFC822.SIZE BODY.PEEK[])")
        _ensure_export_ok(typ)
        fetched_size, raw = _export_fetch_tuple(fetched, uid=uid, require_literal=True)
        assert raw is not None
        if fetched_size != preflight_size or len(raw) != preflight_size:
            raise _export_error(
                "size_mismatch",
                "IMAP evidence export did not return the preflighted message size",
                details={
                    "expected_bytes": preflight_size,
                    "received_bytes": len(raw),
                },
            )

        attachments = _export_attachments(raw)
        transfer_id, transfer_dir, asset_root = _create_transfer_dir(request)
        raw_manifest = _stage_export_file(transfer_dir, "original.eml", raw)
        attachment_manifest = [
            {
                "ordinal": ordinal,
                "path": f"attachment-{ordinal:03d}",
                "media_type": media_type,
                **_stage_export_file(transfer_dir, f"attachment-{ordinal:03d}", payload),
            }
            for ordinal, (media_type, payload) in enumerate(attachments, start=1)
        ]
        staging_uri = _generated_assets_uri(asset_root, transfer_dir)
        account_ref = _safe_account_ref(request)
        return _export_result(
            operation="message.export",
            tls_mode=str(settings["tls_mode"]),
            body={
                "transfer_kind": "imap-staged-evidence.v1",
                "transfer_id": transfer_id,
                "staging_uri": staging_uri,
                "source_identity": {
                    "provider_key": "imap",
                    "account_ref": account_ref,
                    "mailbox_ref": _mailbox_ref(mailbox),
                    "uidvalidity": uidvalidity,
                    "uid": uid,
                    "content_sha256": raw_manifest["sha256"],
                },
                "raw_mime": {"path": "original.eml", **raw_manifest},
                "attachments": attachment_manifest,
                "attachment_count": len(attachment_manifest),
                "attachment_total_bytes": sum(item["bytes"] for item in attachment_manifest),
            },
        )
    except ActionConnectorError as exc:
        _cleanup_partial_export_or_raise(
            transfer_id=transfer_id,
            transfer_dir=transfer_dir,
            asset_root=asset_root,
        )
        exc.metadata_json.setdefault("operation", "message.export")
        exc.metadata_json.setdefault("tls_mode", str(settings.get("tls_mode") or "unknown"))
        raise
    except Exception as exc:
        failure = _export_error(
            "export_failed",
            "IMAP evidence export could not complete safely",
            details={"error_category": type(exc).__name__},
        )
        _cleanup_partial_export_or_raise(
            transfer_id=transfer_id,
            transfer_dir=transfer_dir,
            asset_root=asset_root,
        )
        raise failure from exc
    finally:
        if client is not None:
            _logout(client)


def _cleanup_export(request: ActionConnectorRequest) -> ActionConnectorResult:
    raw_transfer_id = request.input_json.get("transfer_id")
    if not isinstance(raw_transfer_id, str) or _TRANSFER_ID_RE.fullmatch(raw_transfer_id) is None:
        raise _export_error(
            "invalid_transfer", "IMAP evidence cleanup requires an opaque transfer id"
        )
    transfer_id = raw_transfer_id
    asset_root = _generated_assets_root(request)
    project_root = _project_transfer_root(request, asset_root)
    transfer_dir = project_root / transfer_id
    _assert_contained(project_root, transfer_dir)
    if not _path_exists(transfer_dir):
        raise _export_error("not_found", "IMAP staged evidence transfer was not found")
    if transfer_dir.is_symlink():
        raise _export_error(
            "unsafe_transfer", "IMAP staged evidence transfer is unsafe to clean up"
        )
    staging_uri = _generated_assets_uri(asset_root, transfer_dir)
    try:
        _assert_no_symlinks(transfer_dir)
        _remove_transfer_dir(transfer_dir)
    except ActionConnectorError:
        raise
    except OSError as exc:
        raise _cleanup_error(transfer_id=transfer_id, staging_uri=staging_uri) from exc
    if _path_exists(transfer_dir):
        raise _cleanup_error(transfer_id=transfer_id, staging_uri=staging_uri)
    return _export_result(
        operation="message.export.cleanup",
        tls_mode=None,
        body={
            "transfer_id": transfer_id,
            "staging_uri": staging_uri,
            "cleanup_status": "deleted",
        },
    )


def _mark_message(
    request: ActionConnectorRequest,
    *,
    seen: bool,
) -> ActionConnectorResult:
    settings = _imap_settings(request)
    mailbox = _mailbox_name(request, settings)
    uid = int(request.input_json["uid"])
    expected_uidvalidity = request.input_json.get("expected_uidvalidity")
    client = _login(settings)
    try:
        selected = _select(client, mailbox, readonly=False)
        selected_uidvalidity = selected.get("uidvalidity")
        if expected_uidvalidity is not None:
            actual_uidvalidity = _required_uidvalidity(selected_uidvalidity)
            if actual_uidvalidity != str(expected_uidvalidity):
                raise ValidationError("IMAP UIDVALIDITY no longer matches the selected mailbox")
        op = "+FLAGS" if seen else "-FLAGS"
        # IMAP STORE command for \\Seen lifecycle:
        # https://www.rfc-editor.org/rfc/rfc9051.html#name-store-command
        # A tagged OK also permits a nonexistent UID (RFC 9051 section 6.4.9).
        # Observe the exact UID's flags before claiming acknowledgement.
        try:
            typ, _data = client.uid("STORE", str(uid), op, "(\\Seen)")
            _ensure_ok(typ, f"UID STORE {op}")
            typ, data = client.uid("FETCH", str(uid), "(UID FLAGS)")
            _ensure_ok(typ, "UID FETCH flags")
            matches = []
            for item in data or []:
                if not isinstance(item, bytes):
                    continue
                metadata = _safe_decode(item)
                if _single_fetch_number(metadata, r"\bUID\s+(\d+)") == uid and re.search(
                    r"\bFLAGS\s+\([^)]*\)", metadata, re.IGNORECASE
                ):
                    matches.append(_parse_flags(metadata))
            if len(matches) != 1 or (("\\Seen" in matches[0]) != seen):
                raise ValidationError("IMAP flag readback did not confirm the requested UID state")
        except Exception as exc:
            raise ActionConnectorError(
                "IMAP flag write outcome requires reconciliation",
                provider_error={
                    "outcome_unknown": True,
                    "retry_safe": False,
                    "recovery": "Re-read the exact mailbox epoch, UID and flags before "
                    "acknowledging or retrying; retain staged evidence until confirmed.",
                },
            ) from exc
        result = {
            "mailbox_ref": _mailbox_ref(mailbox),
            "uid": uid,
            "uidvalidity": selected_uidvalidity,
            "attention_status": "read" if seen else "unread",
            "store_applied": True,
        }
        return _mark_result(request, result, settings)
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
        "tls_ca_pem": config.get("tls_ca_pem"),
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
    tls_context = imap_ssl_context(settings.get("tls_ca_pem"))
    if settings["tls_mode"] == "ssl":
        client: Any = imaplib.IMAP4_SSL(
            host,
            port,
            ssl_context=tls_context,
            timeout=timeout,
        )
    else:
        client = imaplib.IMAP4(host, port, timeout=timeout)
    if settings["tls_mode"] == "starttls":
        client.starttls(ssl_context=tls_context)
    client.login(str(settings["username"]), str(settings["password"]))
    return client


def _logout(client: Any) -> None:
    # LOGOUT releases selection without expunging pre-existing \\Deleted mail.
    # CLOSE would delete unrelated messages after a writable flag action.
    # https://www.rfc-editor.org/rfc/rfc9051.html#section-6.4.1
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
        mailbox = str(settings["default_mailbox"])
    elif isinstance(refs, Mapping) and raw in refs:
        mailbox = str(refs[raw])
    elif raw.startswith("imap-mailbox:"):
        mailbox = raw.removeprefix("imap-mailbox:")
    elif not refs:
        mailbox = raw
    else:
        raise ValidationError(f"mailbox_ref {raw!r} is not configured for this IMAP credential")
    return _validated_mailbox_name(mailbox)


def _validated_mailbox_name(value: str) -> str:
    mailbox = str(value).strip()
    if not mailbox or len(mailbox) > _MAX_MAILBOX_NAME_CHARS or _has_crlf(mailbox):
        raise ValidationError("IMAP mailbox reference resolved to an invalid mailbox name")
    return mailbox


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


def _fetch_payload(
    data: Sequence[Any] | None, *, uid: int
) -> tuple[bytes | None, list[str], int | None]:
    if not data:
        return None, [], None
    matches: list[tuple[bytes, list[str], int | None]] = []
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2:
            meta = _safe_decode(item[0])
            if _single_fetch_number(meta, r"\bUID\s+(\d+)") != uid:
                raise ValidationError("IMAP fetch returned a mismatched or missing message UID")
            if not isinstance(item[1], bytes):
                raise ValidationError("IMAP fetch returned an invalid message literal")
            matches.append((item[1], sorted(set(_parse_flags(meta))), _parse_size(meta)))
    if len(matches) > 1:
        raise ValidationError("IMAP fetch returned ambiguous message literals")
    return matches[0] if matches else (None, [], None)


def _export_fetch_tuple(
    data: Sequence[Any] | None,
    *,
    uid: int,
    require_literal: bool,
) -> tuple[int, bytes | None]:
    """Bind UID, RFC822.SIZE, and an optional literal from one FETCH response."""
    matches: list[tuple[int, bytes | None]] = []
    for item in data or []:
        literal: bytes | None
        if isinstance(item, tuple) and len(item) >= 2:
            metadata = _safe_decode(item[0])
            literal = item[1] if isinstance(item[1], bytes) else None
        elif isinstance(item, bytes):
            # imaplib returns metadata-only FETCH responses (for example,
            # RFC822.SIZE preflight) as a bare bytes item. Literal-bearing
            # FETCH responses use a (metadata, literal) tuple.
            metadata = _safe_decode(item)
            literal = None
        else:
            continue
        response_uid = _single_fetch_number(metadata, r"\bUID\s+(\d+)")
        if response_uid is None:
            continue
        if response_uid != uid:
            raise _export_error(
                "fetch_mismatch", "IMAP evidence export returned a different message UID"
            )
        size = _single_fetch_number(metadata, r"\bRFC822\.SIZE\s+(\d+)")
        if size is None:
            raise _export_error(
                "fetch_mismatch", "IMAP evidence export did not return a usable message size"
            )
        if require_literal:
            literal_size = _single_fetch_number(metadata, r"\bBODY(?:\.PEEK)?\[\]\s+\{(\d+)\}")
            if literal is None or literal_size is None or literal_size != len(literal):
                raise _export_error(
                    "fetch_truncated",
                    "IMAP evidence export did not return a complete message literal",
                )
        elif literal not in {None, b""}:
            raise _export_error(
                "fetch_mismatch", "IMAP evidence preflight unexpectedly returned message content"
            )
        matches.append((size, literal))
    if len(matches) != 1:
        raise _export_error(
            "fetch_ambiguous", "IMAP evidence export did not return exactly one matching message"
        )
    return matches[0]


def _single_fetch_number(metadata: str, expression: str) -> int | None:
    values = re.findall(expression, metadata, flags=re.IGNORECASE)
    if len(values) != 1:
        return None
    try:
        return int(values[0])
    except ValueError:
        return None


def _required_uidvalidity(value: Any) -> str:
    if isinstance(value, bool):
        raise _export_error(
            "uidvalidity_invalid", "IMAP mailbox UIDVALIDITY is unavailable or invalid"
        )
    text = str(value or "").strip()
    if not text.isascii() or not text.isdigit():
        raise _export_error(
            "uidvalidity_invalid", "IMAP mailbox UIDVALIDITY is unavailable or invalid"
        )
    numeric = int(text)
    if numeric < 1 or numeric > _MAX_UIDVALIDITY:
        raise _export_error(
            "uidvalidity_invalid", "IMAP mailbox UIDVALIDITY is unavailable or invalid"
        )
    return str(numeric)


def _export_attachments(raw: bytes) -> list[tuple[str, bytes]]:
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
    except Exception as exc:
        raise _export_error("malformed", "IMAP evidence MIME is malformed") from exc
    if message.defects:
        raise _export_error("malformed", "IMAP evidence MIME is malformed")

    parts = _bounded_mime_parts(message)
    attachments: list[tuple[str, bytes]] = []
    attachment_paths: list[tuple[int, ...]] = []
    attachment_total = 0
    for part, path in parts:
        if any(path[: len(parent)] == parent for parent in attachment_paths):
            continue
        if not _is_attachment(part):
            continue
        if len(attachments) >= _MAX_EXPORT_ATTACHMENTS:
            raise _export_error(
                "attachment_count_exceeded",
                "IMAP evidence export exceeds the attachment count limit",
                details={"max_attachments": _MAX_EXPORT_ATTACHMENTS},
            )
        payload = _attachment_bytes(part)
        if len(payload) > _MAX_EXPORT_ATTACHMENT_BYTES:
            raise _export_error(
                "attachment_oversize",
                "IMAP evidence export contains an attachment above the 8 MiB limit",
                details={"max_attachment_bytes": _MAX_EXPORT_ATTACHMENT_BYTES},
            )
        attachment_total += len(payload)
        if attachment_total > _MAX_EXPORT_ATTACHMENT_TOTAL_BYTES:
            raise _export_error(
                "attachment_total_oversize",
                "IMAP evidence export exceeds the attachment aggregate limit",
                details={"max_attachment_total_bytes": _MAX_EXPORT_ATTACHMENT_TOTAL_BYTES},
            )
        attachments.append((_safe_media_type(part.get_content_type()), payload))
        attachment_paths.append(path)
    return attachments


def _bounded_mime_parts(message: Message) -> list[tuple[Message, tuple[int, ...]]]:
    stack: list[tuple[Message, int, tuple[int, ...]]] = [(message, 1, ())]
    collected: list[tuple[Message, tuple[int, ...]]] = []
    part_count = 0
    while stack:
        part, depth, path = stack.pop()
        part_count += 1
        if part_count > _MAX_EXPORT_MIME_PARTS or depth > _MAX_EXPORT_MIME_DEPTH:
            raise _export_error(
                "mime_structure_exceeded", "IMAP evidence MIME structure exceeds limits"
            )
        if part.defects:
            raise _export_error("malformed", "IMAP evidence MIME is malformed")
        collected.append((part, path))
        if not part.is_multipart():
            continue
        children = part.get_payload()
        if not isinstance(children, list):
            raise _export_error("malformed", "IMAP evidence MIME is malformed")
        stack.extend(
            (child, depth + 1, (*path, index))
            for index, child in reversed(list(enumerate(children)))
            if isinstance(child, Message)
        )
        if len(children) != sum(isinstance(child, Message) for child in children):
            raise _export_error("malformed", "IMAP evidence MIME is malformed")
    return collected


def _is_attachment(part: Message) -> bool:
    return part.get_content_disposition() == "attachment" or part.get_filename() is not None


def _attachment_bytes(part: Message) -> bytes:
    if part.is_multipart() or part.get_content_type().lower() == "message/rfc822":
        return part.as_bytes(policy=policy.default)
    encoding = str(part.get("Content-Transfer-Encoding") or "").strip().lower()
    if encoding not in {"", "7bit", "8bit", "binary", "base64", "quoted-printable"}:
        raise _export_error(
            "unsupported_encoding", "IMAP evidence attachment encoding is unsupported"
        )
    if encoding == "base64":
        encoded = part.get_payload()
        if not isinstance(encoded, str):
            raise _export_error("malformed", "IMAP evidence attachment is malformed")
        try:
            return base64.b64decode("".join(encoded.split()), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise _export_error("malformed", "IMAP evidence attachment is malformed") from exc
    payload = part.get_payload(decode=True)
    if payload is None:
        raw_payload = part.get_payload()
        if raw_payload is None or raw_payload == "":
            return b""
        raise _export_error("malformed", "IMAP evidence attachment is malformed")
    if not isinstance(payload, bytes):
        raise _export_error("malformed", "IMAP evidence attachment is malformed")
    return payload


def _safe_media_type(value: str) -> str:
    candidate = str(value or "").strip().lower()
    if len(candidate) <= 127 and re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", candidate):
        return candidate
    return "application/octet-stream"


def _generated_assets_root(request: ActionConnectorRequest) -> Path:
    configured = request.asset_dir or Settings().generated_assets_dir
    if configured.is_symlink():
        raise _export_error("unsafe_staging", "IMAP evidence staging root is unavailable")
    root = configured.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise _export_error("unsafe_staging", "IMAP evidence staging root is unavailable")
    return root


def _project_transfer_root(request: ActionConnectorRequest, asset_root: Path) -> Path:
    root = asset_root / "imap-transfers" / f"project-{request.project_id}"
    _assert_contained(asset_root, root)
    for directory in (asset_root / "imap-transfers", root):
        _assert_contained(asset_root, directory)
        if directory.exists() and directory.is_symlink():
            raise _export_error("unsafe_staging", "IMAP evidence staging root is unsafe")
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise _export_error("unsafe_staging", "IMAP evidence staging root is unsafe")
    return root


def _create_transfer_dir(request: ActionConnectorRequest) -> tuple[str, Path, Path]:
    asset_root = _generated_assets_root(request)
    project_root = _project_transfer_root(request, asset_root)
    for _attempt in range(8):
        transfer_id = uuid4().hex
        transfer_dir = project_root / transfer_id
        _assert_contained(project_root, transfer_dir)
        try:
            transfer_dir.mkdir(mode=0o700)
        except FileExistsError:
            continue
        if transfer_dir.is_symlink() or not transfer_dir.is_dir():
            _remove_transfer_dir(transfer_dir)
            continue
        return transfer_id, transfer_dir, asset_root
    raise _export_error("staging_unavailable", "IMAP evidence staging could not be created safely")


def _stage_export_file(directory: Path, name: str, payload: bytes) -> dict[str, Any]:
    target = directory / name
    _assert_contained(directory, target)
    temporary = directory / f".{name}.{uuid4().hex}.tmp"
    _assert_contained(directory, temporary)
    try:
        with temporary.open("xb") as file_obj:
            file_obj.write(payload)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(temporary, target)
        staged = target.read_bytes()
    except OSError as exc:
        with suppress(FileNotFoundError):
            temporary.unlink()
        raise _export_error(
            "staging_unavailable", "IMAP evidence staging could not be written safely"
        ) from exc
    if staged != payload:
        raise _export_error("staging_mismatch", "IMAP evidence staging verification failed")
    return {"bytes": len(staged), "sha256": hashlib.sha256(staged).hexdigest()}


def _generated_assets_uri(asset_root: Path, transfer_dir: Path) -> str:
    try:
        relative = transfer_dir.relative_to(asset_root)
    except ValueError as exc:
        raise _export_error(
            "unsafe_staging", "IMAP evidence staging escaped generated assets"
        ) from exc
    return f"/generated-assets/{relative.as_posix()}/"


def _assert_contained(root: Path, candidate: Path) -> None:
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise _export_error("unsafe_transfer", "IMAP evidence transfer path is unsafe") from exc


def _assert_no_symlinks(root: Path) -> None:
    if root.is_symlink():
        raise _export_error(
            "unsafe_transfer", "IMAP staged evidence transfer is unsafe to clean up"
        )
    for path in root.rglob("*"):
        if path.is_symlink():
            raise _export_error(
                "unsafe_transfer", "IMAP staged evidence transfer is unsafe to clean up"
            )


def _remove_transfer_dir(transfer_dir: Path) -> None:
    if not _path_exists(transfer_dir):
        return
    if transfer_dir.is_symlink():
        raise OSError("refusing to delete a symbolic-link transfer directory")
    _assert_no_symlinks(transfer_dir)
    shutil.rmtree(transfer_dir)
    if _path_exists(transfer_dir):
        raise OSError("transfer directory remains after deletion")


def _path_exists(path: Path) -> bool:
    return os.path.lexists(path)


def _cleanup_partial_export_or_raise(
    *,
    transfer_id: str | None,
    transfer_dir: Path | None,
    asset_root: Path | None,
) -> None:
    if transfer_dir is None or transfer_id is None or asset_root is None:
        return
    staging_uri = _generated_assets_uri(asset_root, transfer_dir)
    try:
        _remove_transfer_dir(transfer_dir)
    except (ActionConnectorError, OSError) as exc:
        raise _export_error(
            "partial_export_cleanup_failed",
            "IMAP evidence export failed and staged data needs explicit cleanup",
            details={
                "transfer_id": transfer_id,
                "staging_uri": staging_uri,
                "recovery_required": True,
            },
        ) from exc
    if _path_exists(transfer_dir):
        raise _export_error(
            "partial_export_cleanup_failed",
            "IMAP evidence export failed and staged data needs explicit cleanup",
            details={
                "transfer_id": transfer_id,
                "staging_uri": staging_uri,
                "recovery_required": True,
            },
        )


def _cleanup_error(*, transfer_id: str, staging_uri: str) -> ActionConnectorError:
    return _export_error(
        "cleanup_failed",
        "IMAP staged evidence transfer could not be removed safely",
        details={
            "transfer_id": transfer_id,
            "staging_uri": staging_uri,
            "recovery_required": True,
        },
    )


def _safe_account_ref(request: ActionConnectorRequest) -> str:
    if request.credential is None:
        raise _export_error(
            "credential_missing", "IMAP evidence export requires a selected account"
        )
    return request.credential.credential_ref


def _require_export_tls(settings: Mapping[str, Any]) -> None:
    if settings.get("tls_mode") not in {"ssl", "starttls"}:
        raise _export_error("tls_required", "IMAP evidence export requires SSL or STARTTLS")


def _ensure_export_ok(status: Any) -> None:
    if str(status).upper() != "OK":
        raise _export_error("provider_rejected", "IMAP evidence export request was not accepted")


def _export_result(
    *,
    operation: str,
    tls_mode: str | None,
    body: dict[str, Any],
) -> ActionConnectorResult:
    metadata: dict[str, Any] = {
        "vendor": "imap",
        "operation": operation,
        "evidence_transfer": True,
    }
    if tls_mode is not None:
        metadata["tls_mode"] = tls_mode
    return ActionConnectorResult(
        output_json={"provider": "imap", "operation": operation, "status": "success", **body},
        metadata_json=metadata,
    )


def _export_error(
    category: str,
    detail: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> ActionConnectorError:
    output: dict[str, Any] = {"status": "rejected", "category": category}
    if details:
        output.update(dict(details))
    return ActionConnectorError(
        detail,
        output_json=output,
        metadata_json={"vendor": "imap", "evidence_transfer": True, "category": category},
    )


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
            "last_observed_uid": max(result.get("uids") or [0]),
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
        external_id=(
            f"imap-event:{result['mailbox_ref']}:{result['uid']}:{result['attention_status']}"
        ),
        title=f"IMAP message {result['uid']} {result['attention_status']}",
        data_json={
            "provider_key": "imap",
            "event_type": "message_flag_changed",
            "mailbox_ref": result["mailbox_ref"],
            "message_ref": f"imap-message:{result['mailbox_ref']}:{result['uid']}",
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
        },
    )


def _mark_result(
    request: ActionConnectorRequest,
    body: dict[str, Any],
    settings: Mapping[str, Any],
) -> ActionConnectorResult:
    """Return only the allowed acknowledgement projection, never IMAP STORE bytes."""
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
            "acknowledgement": True,
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


def _optional_uidvalidity(
    payload: Mapping[str, Any],
    issues: list[ActionValidationIssue],
) -> None:
    value = payload.get("expected_uidvalidity")
    if value is None:
        return
    if not isinstance(value, str):
        issues.append(
            issue(
                "$.expected_uidvalidity",
                "expected_uidvalidity must be a bounded nonzero numeric string",
                "format",
            )
        )
        return
    try:
        _required_uidvalidity(value)
    except ActionConnectorError:
        issues.append(
            issue(
                "$.expected_uidvalidity",
                "expected_uidvalidity must be a bounded nonzero numeric string",
                "format",
            )
        )


def _transfer_id(payload: Mapping[str, Any], issues: list[ActionValidationIssue]) -> None:
    value = payload.get("transfer_id")
    if not isinstance(value, str) or _TRANSFER_ID_RE.fullmatch(value) is None:
        issues.append(issue("$.transfer_id", "transfer_id must be an opaque transfer id", "format"))


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
