"""IMAP action connector.

Official docs verified:
- IMAP4rev2 protocol: https://www.rfc-editor.org/rfc/rfc9051.html
- Python imaplib adapter: https://docs.python.org/3/library/imaplib.html
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import replace
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
from stackos.artifacts import redact_secrets
from stackos.config import Settings
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

    def __init__(self, *, client=None, options=None) -> None:
        self._client = client
        self._options = options

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case "mailbox.list":
                return []
            case "messages.search":
                _text(payload, "mailbox_ref", issues, required=True)
                _optional_int(payload, "limit", issues, minimum=1, maximum=_MAX_LIMIT)
                _optional_int(payload, "after_uid", issues, minimum=1, maximum=_MAX_UIDVALIDITY)
                _optional_uidvalidity(payload, issues)
                if payload.get("after_uid") is not None and not payload.get("expected_uidvalidity"):
                    issues.append(
                        issue(
                            "$.expected_uidvalidity",
                            "after_uid requires expected_uidvalidity from the previous search",
                            "required",
                        )
                    )
                _criteria(payload.get("criteria"), issues)
            case "message.fetch":
                _text(payload, "mailbox_ref", issues, required=True)
                _optional_int(payload, "uid", issues, minimum=1, required=True)
                _fields(payload.get("fields"), issues)
                _optional_int(payload, "max_body_bytes", issues, minimum=1, maximum=_MAX_BODY_BYTES)
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
        if request.operation == "message.export.cleanup":
            return await asyncio.to_thread(_cleanup_export, request)
        if request.operation == "message.export":
            return await _export_message(request, self)
        try:
            result = await self._native(request)
        except ActionConnectorError as exc:
            if (
                request.operation.startswith("message.mark_")
                and exc.output_json.get("store_confirmed") is False
            ):
                output = dict(exc.output_json)
                mailbox = output.pop("mailbox_name", None)
                if isinstance(mailbox, str):
                    output["mailbox_ref"] = _mailbox_ref(mailbox)
                requested_seen = output.pop("requested_seen", None)
                if isinstance(requested_seen, bool):
                    output["requested_attention_status"] = "read" if requested_seen else "unread"
                exc.output_json = redact_secrets(output)
                exc.metadata_json = redact_secrets(exc.metadata_json)
            raise
        output = result.output_json
        mailbox = output.get("mailbox_name")
        if mailbox:
            output["mailbox_ref"] = _mailbox_ref(str(mailbox))
        if request.operation == "mailbox.list":
            for item in output.get("mailboxes", []):
                item["mailbox_ref"] = _mailbox_ref(item["name"])
        if request.operation == "messages.search":
            output["message_refs"] = [f"imap-message:{mailbox}:{uid}" for uid in output["uids"]]
        if request.operation == "message.fetch":
            output["message_ref"] = f"imap-message:{mailbox}:{output['uid']}"
            output["content_completeness"]["full_content_action_ref"] = (
                "communications.imap.message.export"
            )
        if request.operation.startswith("message.mark_"):
            output["attention_status"] = "read" if output.pop("seen") else "unread"
            output.pop("mailbox_name", None)
            result.metadata_json["acknowledgement"] = True
        try:
            match request.operation:
                case "mailbox.list":
                    _store_mailboxes(request, output.get("mailboxes") or [])
                case "messages.search":
                    _store_cursor(request, output)
                case "message.fetch":
                    _store_inbound_message(request, output)
                case "message.mark_seen" | "message.mark_unseen":
                    _store_message_status(request, output)
        except Exception as exc:
            raise ActionConnectorError(
                "IMAP operation completed but communication state could not be stored",
                output_json=output,
                metadata_json={
                    **(result.metadata_json or {}),
                    "provider_executed": True,
                    "retry_safe": not request.operation.startswith("message.mark_"),
                },
            ) from exc
        return ActionConnectorResult(
            output_json=redact_secrets(output), metadata_json=redact_secrets(result.metadata_json)
        )

    async def _native(self, request: ActionConnectorRequest, *, output_dir=None):
        from stackos_connectors import CallOptions, ConnectorClient
        from stackos_connectors.catalog import load_registry

        from stackos.actions.package_bridge import PackageActionConnector

        settings = _imap_settings(request)
        prepared = dict(request.input_json)
        if request.operation != "mailbox.list":
            prepared.pop("mailbox_ref", None)
            prepared["mailbox"] = _mailbox_name(request, settings)
        if request.operation == "messages.search":
            prepared.setdefault("limit", settings["search_limit"])
        if request.operation == "message.fetch":
            prepared.setdefault("max_body_bytes", _DEFAULT_BODY_BYTES)
            prepared["preview_chars"] = 500
            prepared.setdefault(
                "fields", ["subject", "from", "to", "date", "message_id", "text_preview", "flags"]
            )
        if request.operation == "message.export":
            prepared["limits"] = _export_limits()
        if self._client is None:
            self._client = ConnectorClient(registry=load_registry("connectors/imap/catalog.json"))
        options = self._options or CallOptions()
        if output_dir is not None:
            options = replace(options, output_dir=output_dir)
            request = replace(request, asset_dir=output_dir)
        return await PackageActionConnector(
            "imap", client=self._client, options=options
        ).execute_native(replace(request, input_json=prepared))


async def _export_message(
    request: ActionConnectorRequest, connector: ImapActionConnector
) -> ActionConnectorResult:
    settings = _imap_settings(request)
    transfer_id = transfer_dir = asset_root = None
    try:
        _require_export_tls(settings)
        account_ref = _safe_account_ref(request)
        transfer_id, transfer_dir, asset_root = _create_transfer_dir(request)
        native = await connector._native(request, output_dir=transfer_dir)
        output = native.output_json
        result = _export_result(
            operation="message.export",
            tls_mode=str(settings["tls_mode"]),
            body={
                "transfer_kind": "imap-staged-evidence.v1",
                "transfer_id": transfer_id,
                "staging_uri": _generated_assets_uri(asset_root, transfer_dir),
                "source_identity": {
                    "provider_key": "imap",
                    "account_ref": account_ref,
                    "mailbox_ref": _mailbox_ref(output["mailbox_name"]),
                    "uidvalidity": output["uidvalidity"],
                    "uid": output["uid"],
                    "content_sha256": output["content_sha256"],
                },
                **{
                    key: output[key]
                    for key in (
                        "raw_mime",
                        "attachments",
                        "attachment_count",
                        "attachment_total_bytes",
                    )
                },
            },
        )
        result.metadata_json = {**(result.metadata_json or {}), **(native.metadata_json or {})}
        return result
    except ActionConnectorError as exc:
        _cleanup_partial_export_or_raise(
            transfer_id=transfer_id, transfer_dir=transfer_dir, asset_root=asset_root
        )
        exc.metadata_json.update(
            {
                "evidence_transfer": True,
                "operation": "message.export",
                "tls_mode": str(settings.get("tls_mode") or "unknown"),
            }
        )
        raise
    except Exception as exc:
        _cleanup_partial_export_or_raise(
            transfer_id=transfer_id, transfer_dir=transfer_dir, asset_root=asset_root
        )
        raise _export_error(
            "export_failed",
            "IMAP evidence export could not complete safely",
            details={"error_category": type(exc).__name__},
        ) from exc


def _export_limits() -> dict[str, int]:
    return {
        "message_bytes": _MAX_EXPORT_MESSAGE_BYTES,
        "attachments": _MAX_EXPORT_ATTACHMENTS,
        "attachment_bytes": _MAX_EXPORT_ATTACHMENT_BYTES,
        "attachment_total_bytes": _MAX_EXPORT_ATTACHMENT_TOTAL_BYTES,
        "mime_parts": _MAX_EXPORT_MIME_PARTS,
        "mime_depth": _MAX_EXPORT_MIME_DEPTH,
    }


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
            "matched_count": result.get("matched_count"),
            "has_more": result.get("has_more"),
            "next_after_uid": result.get("next_after_uid"),
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
            "content_completeness": message.get("content_completeness"),
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
    from stackos_connectors.connectors.imap.actions import _required_uidvalidity
    from stackos_connectors.errors import ConnectorError

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
    except ConnectorError:
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
