"""Download finalized Stripe invoice PDFs into temporary private evidence custody.

The public invoice PDF is null for drafts: https://docs.stripe.com/api/invoices/object
Download the returned HTTP(S) URL and redirects without a host allowlist.
No API credential or URL userinfo is forwarded, including across redirects.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRequest,
    ActionConnectorResult,
)
from stackos.artifacts import redact_secrets
from stackos.config import Settings
from stackos.integrations.stripe import StripeIntegration
from stackos.mcp.errors import IntegrationDownError, RateLimitedError
from stackos.repositories.base import ValidationError
from stackos.repositories.provider_refs import ProviderObjectReferenceRepository

PDF_ACTIONS = frozenset({"stripe.invoices.pdf.download", "stripe.invoices.pdf.cleanup"})
MAX_PDF_BYTES = 20 * 1024 * 1024
_TRANSFER_RE = re.compile(r"stripe-pdf-transfer:([a-f0-9]{32})\Z")
_DIAGNOSTIC_HOST_RE = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\.?\Z"
)
_STAGING_ROOT = "stripe-invoice-transfers"


def _error(
    reason: str,
    *,
    status: int | None = None,
    transfer_ref: str | None = None,
    cleanup_recovery: str = "retry-account-bound-cleanup",
    pdf_diagnostics: Mapping[str, object] | None = None,
) -> ActionConnectorError:
    details: dict[str, object] = {
        "reason_code": reason,
        "outcome_unknown": False,
        "retry_safe": True,
    }
    if pdf_diagnostics is not None:
        details.update(pdf_diagnostics)
    output = {"status": "failed", "provider_error": details}
    if transfer_ref is not None:
        repair = {
            "transfer_ref": transfer_ref,
            "cleanup_status": "unresolved",
            "cleanup_recovery": cleanup_recovery,
        }
        output.update(repair)
        details.update(repair)
    return ActionConnectorError(
        "Stripe invoice PDF transfer failed",
        provider_status_code=status,
        provider_error=details,
        output_json=output,
    )


def _url_diagnostics(value: object, *, phase: str, redirect_hop: int) -> dict[str, object]:
    # Never retain a URL, authority/userinfo, path, query, or Location header.
    hostname = None
    if isinstance(value, str) and len(value) <= 8192 and not any(ord(c) < 33 for c in value):
        try:
            candidate = urlsplit(value).hostname
            if candidate and len(candidate) <= 253 and _DIAGNOSTIC_HOST_RE.fullmatch(candidate):
                hostname = candidate
        except ValueError:
            pass
    return {
        "pdf_phase": phase,
        "pdf_hostname": hostname,
        "pdf_redirect_hop": redirect_hop,
    }


def _safe_url(value: object, *, phase: str = "initial_url", redirect_hop: int = 0) -> str:
    diagnostics = _url_diagnostics(value, phase=phase, redirect_hop=redirect_hop)
    if not isinstance(value, str):
        raise _error("unsupported_pdf_url", pdf_diagnostics=diagnostics)
    try:
        parsed = httpx.URL(value)
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            raise ValueError("unsupported HTTP transport URL")
        # HTTPX would turn URL userinfo into Basic authorization. Never forward it.
        return str(parsed.copy_with(userinfo=b"", fragment=None))
    except (httpx.InvalidURL, ValueError) as exc:
        raise _error("unsupported_pdf_url", pdf_diagnostics=diagnostics) from exc


async def _pdf_bytes(url: str, limit: int) -> bytes:
    # Separate client: no API Authorization, cookies, environment proxy, or auto redirects.
    diagnostics = _url_diagnostics(url, phase="download", redirect_hop=0)
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False, trust_env=False) as http:
            for attempt in range(4):
                url = _safe_url(
                    url,
                    phase="initial_url" if attempt == 0 else "redirect_url",
                    redirect_hop=attempt,
                )
                diagnostics = _url_diagnostics(url, phase="download", redirect_hop=attempt)
                http.cookies.clear()
                async with http.stream(
                    "GET", url, headers={"Accept": "application/pdf"}
                ) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location or attempt == 3:
                            raise _error("pdf_redirect_limit", pdf_diagnostics=diagnostics)
                        try:
                            destination = str(httpx.URL(url).join(location))
                        except (httpx.InvalidURL, ValueError) as exc:
                            raise _error(
                                "unsupported_pdf_url",
                                pdf_diagnostics=_url_diagnostics(
                                    location, phase="redirect_url", redirect_hop=attempt + 1
                                ),
                            ) from exc
                        url = _safe_url(destination, phase="redirect_url", redirect_hop=attempt + 1)
                        continue
                    if response.status_code != 200:
                        raise _error(
                            "pdf_http_failure",
                            status=response.status_code,
                            pdf_diagnostics=diagnostics,
                        )
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if content_type not in {"application/pdf", "application/octet-stream"}:
                        raise _error("pdf_content_type", pdf_diagnostics=diagnostics)
                    length = response.headers.get("content-length")
                    if length is not None and (not length.isdigit() or int(length) > limit):
                        raise _error("pdf_size_limit", pdf_diagnostics=diagnostics)
                    payload = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        if len(payload) + len(chunk) > limit:
                            raise _error("pdf_size_limit", pdf_diagnostics=diagnostics)
                        payload.extend(chunk)
                    if (
                        length is not None
                        and response.headers.get("content-encoding", "identity").lower()
                        == "identity"
                        and len(payload) != int(length)
                    ):
                        raise _error("pdf_length_mismatch", pdf_diagnostics=diagnostics)
                    if not payload.startswith(b"%PDF-") or not payload.rstrip().endswith(b"%%EOF"):
                        raise _error("pdf_signature", pdf_diagnostics=diagnostics)
                    return bytes(payload)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        raise _error("pdf_transport_failure", pdf_diagnostics=diagnostics) from exc
    raise _error("pdf_redirect_limit", pdf_diagnostics=diagnostics)


def _project_root(request: ActionConnectorRequest) -> Path:
    configured = request.asset_dir or Settings().generated_assets_dir
    if configured.is_symlink():
        raise _error("unsafe_staging")
    asset_root = configured.resolve()
    asset_root.mkdir(parents=True, exist_ok=True)
    root = asset_root / _STAGING_ROOT / f"project-{request.project_id}"
    for directory in (asset_root / _STAGING_ROOT, root):
        if directory.is_symlink():
            raise _error("unsafe_staging")
        directory.mkdir(mode=0o700, exist_ok=True)
        if not directory.is_dir():
            raise _error("unsafe_staging")
        directory.chmod(0o700)
    return root


def _write_private(path: Path, payload: bytes) -> None:
    # Exclusive creation under an unpredictable mode-0700 directory; no caller file names.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _stage(request: ActionConnectorRequest, invoice_ref: str, payload: bytes) -> dict:
    directory = _project_root(request) / uuid4().hex
    directory.mkdir(mode=0o700)
    receipt_written = False
    try:
        receipt = {
            "project_id": request.project_id,
            "invoice_ref": invoice_ref,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
            "staged_at": datetime.now(UTC).isoformat(),
        }
        _write_private(directory / "transfer.json", json.dumps(receipt).encode())
        receipt_written = True
        pdf = directory / "invoice.pdf"
        _write_private(pdf, payload)
        staged = pdf.read_bytes()
        if staged != payload:
            raise _error("staging_mismatch")
        return {
            **receipt,
            "transfer_ref": f"stripe-pdf-transfer:{directory.name}",
            "filename": "invoice.pdf",
            "mime_type": "application/pdf",
            "local_path": str(pdf),
            "custody": "temporary-private-staging",
            "next_action": (
                "Copy to external finance evidence storage, verify SHA-256, "
                "then clean up this transfer."
            ),
        }
    except Exception:
        try:
            _remove_transfer_files(directory)
        except OSError as exc:
            raise _error(
                "partial_transfer_cleanup_failed",
                transfer_ref=f"stripe-pdf-transfer:{directory.name}",
                cleanup_recovery=(
                    "retry-account-bound-cleanup" if receipt_written else "operator-review"
                ),
            ) from exc
        raise


def _remove_transfer_files(directory: Path) -> None:
    # Preserve the account identity receipt until the PDF deletion succeeds.
    (directory / "invoice.pdf").unlink(missing_ok=True)
    (directory / "transfer.json").unlink(missing_ok=True)
    directory.rmdir()


def _cleanup(request: ActionConnectorRequest, refs: ProviderObjectReferenceRepository) -> dict:
    assert request.credential is not None
    transfer_ref = request.input_json.get("transfer_ref")
    match = _TRANSFER_RE.fullmatch(transfer_ref) if isinstance(transfer_ref, str) else None
    if match is None:
        raise ValidationError("PDF cleanup requires an opaque Stripe PDF transfer reference")
    directory = _project_root(request) / match[1]
    if directory.is_symlink() or not directory.is_dir():
        raise _error("transfer_not_found")
    children = list(directory.iterdir())
    if any(child.is_symlink() or not child.is_file() for child in children):
        raise _error("unsafe_staging")
    if {child.name for child in children} - {"invoice.pdf", "transfer.json"}:
        raise _error("invalid_transfer")
    try:
        receipt = json.loads((directory / "transfer.json").read_text())
    except (OSError, ValueError) as exc:
        raise _error(
            "transfer_identity_unavailable",
            transfer_ref=transfer_ref,
            cleanup_recovery="operator-review",
        ) from exc
    if not isinstance(receipt, dict) or receipt.get("project_id") != request.project_id:
        raise _error("invalid_transfer")
    invoice_ref = receipt.get("invoice_ref")
    if not isinstance(invoice_ref, str):
        raise _error("invalid_transfer")
    # Resolving the saved invoice reference verifies the current credential's exact account.
    refs.resolve(
        credential=request.credential.credential,
        safe_ref=invoice_ref,
        expected_object_type="stripe.invoice",
    )
    try:
        _remove_transfer_files(directory)
    except OSError as exc:
        raise _error("cleanup_failed", transfer_ref=transfer_ref) from exc
    if directory.exists():
        raise _error("cleanup_failed")
    return {
        "transfer_ref": transfer_ref,
        "invoice_ref": receipt["invoice_ref"],
        "cleanup_status": "deleted",
    }


async def execute_pdf(request: ActionConnectorRequest) -> ActionConnectorResult:
    if request.credential is None or request.session is None:
        raise ValidationError(
            "Stripe PDF actions require a resolved credential and repository session"
        )
    refs = ProviderObjectReferenceRepository(request.session, project_id=request.project_id)
    metadata = {"vendor": "stripe", "operation": request.action_key}
    try:
        if request.action_key == "stripe.invoices.pdf.cleanup":
            if set(request.input_json) != {"transfer_ref"}:
                raise ValidationError("PDF cleanup accepts only transfer_ref")
            output = _cleanup(request, refs)
        else:
            if set(request.input_json) - {"invoice_ref", "max_bytes"}:
                raise ValidationError("PDF download accepts only invoice_ref and max_bytes")
            limit = request.input_json.get("max_bytes", MAX_PDF_BYTES)
            if (
                isinstance(limit, bool)
                or not isinstance(limit, int)
                or not 1 <= limit <= MAX_PDF_BYTES
            ):
                raise ValidationError("max_bytes must be between 1 and 20971520")
            invoice_ref = request.input_json.get("invoice_ref")
            if not isinstance(invoice_ref, str):
                raise ValidationError("PDF download requires an account-bound invoice_ref")
            resolved = refs.resolve(
                credential=request.credential.credential,
                safe_ref=invoice_ref,
                expected_object_type="stripe.invoice",
            )
            async with httpx.AsyncClient(timeout=30.0) as http:
                integration = StripeIntegration(
                    payload=request.credential.secret_payload,
                    project_id=request.project_id,
                    http=http,
                    auth_method_key=request.credential.credential.auth_method_key,
                )
                result = await integration.request(
                    method="GET",
                    path=f"/invoices/{resolved.provider_object_id}",
                    op=request.action_key,
                )
            if result.metadata:
                metadata.update(redact_secrets(result.metadata))
            invoice = result.data
            if (
                not isinstance(invoice, Mapping)
                or invoice.get("object") != "invoice"
                or invoice.get("id") != resolved.provider_object_id
            ):
                raise _error("invoice_identity_mismatch")
            if invoice.get("status") not in {"open", "paid", "void", "uncollectible"}:
                raise _error("invoice_not_finalized")
            transitions = invoice.get("status_transitions")
            finalized_at = (
                transitions.get("finalized_at") if isinstance(transitions, Mapping) else None
            )
            if (
                isinstance(finalized_at, bool)
                or not isinstance(finalized_at, int)
                or finalized_at <= 0
            ):
                raise _error("invoice_not_finalized")
            payload = await _pdf_bytes(_safe_url(invoice.get("invoice_pdf")), limit)
            output = _stage(request, invoice_ref, payload)
            output["finalized_at"] = finalized_at
    except (IntegrationDownError, RateLimitedError) as exc:
        from stackos.actions.stripe import _connector_error

        raise _connector_error(exc) from exc
    except ActionConnectorError as exc:
        exc.metadata_json.update(metadata)
        raise
    except (OSError, ValueError) as exc:
        failure = _error("private_transfer_failure")
        failure.metadata_json.update(metadata)
        raise failure from exc
    return ActionConnectorResult(
        output_json={"provider": "stripe", "operation": request.action_key, "data": output},
        metadata_json=metadata,
    )
