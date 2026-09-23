"""Mocked finalized PDF transport and private staging proof; no live invoices."""

import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock, IteratorStream
from sqlmodel import Session, select

from stackos.actions import ActionRepository, stripe_pdf
from stackos.actions.connectors import ActionConnectorError, ActionConnectorRequest
from stackos.actions.media_artifacts import artifact_path
from stackos.db.models import ActionCall, CredentialAccount
from stackos.repositories.base import ConflictError, ValidationError
from stackos.server import GeneratedAssetsStaticFiles
from tests.helpers.stripe import stripe_invoice
from tests.integration.test_repositories.test_stripe_actions import (
    STRIPE_ROOT,
    STRIPE_SECRET,
    _safe_ref,
    _stripe_credential_ref,
)

PDF_URL = "https://pay.stripe.com/invoice/acct_fixture/opaque-fixture/pdf?s=private-link"
PDF_REDIRECT_HOST = "stripe-upload-api.s3.us-west-1.amazonaws.com"
PDF_REDIRECT_URL = f"https://{PDF_REDIRECT_HOST}/private-invoice.pdf?signature=private-token"
PDF = b"%PDF-1.7\nfixture invoice contains private billing details\n%%EOF"


@pytest.fixture
def pdf_target(session: Session, project_id: int, httpx_mock: HTTPXMock) -> tuple[str, str]:
    credential = _stripe_credential_ref(session, project_id, httpx_mock)
    invoice = _safe_ref(
        session,
        project_id=project_id,
        credential_ref=credential,
        object_type="stripe.invoice",
        provider_id="in_pdf_fixture",
    )
    return credential, invoice


def _invoice(httpx_mock: HTTPXMock, **changes) -> None:
    invoice = stripe_invoice(
        id="in_pdf_fixture",
        status="open",
        invoice_pdf=PDF_URL,
        status_transitions={"finalized_at": 1790118000},
    )
    invoice.update(changes)
    httpx_mock.add_response(
        method="GET",
        url=f"{STRIPE_ROOT}/invoices/in_pdf_fixture",
        headers={"Request-Id": "req_pdf_invoice", "Stripe-Version": "2026-08-26.dahlia"},
        json=invoice,
    )


def _call(session, project_id, tmp_path, pdf_target, operation="download", **inputs):
    payload = {"invoice_ref": pdf_target[1]} if operation == "download" else {}
    return asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref=f"finance.stripe.invoices.pdf.{operation}",
            credential_ref=pdf_target[0],
            input_json={**payload, **inputs},
        )
    ).data


def test_pdf_download_and_cleanup_preserve_bytes_private_permissions_and_audit(
    session, project_id, tmp_path, httpx_mock, pdf_target
) -> None:
    _invoice(httpx_mock)
    httpx_mock.add_response(
        method="GET", url=PDF_URL, content=PDF, headers={"Content-Type": "application/pdf"}
    )
    result = _call(session, project_id, tmp_path, pdf_target)
    assert result.action_call.status == "success", result
    assert result.metadata_json["request_id"] == "req_pdf_invoice"
    assert result.metadata_json["request_api_version"] == "2026-08-26.dahlia"
    assert result.metadata_json["response_api_version"] == "2026-08-26.dahlia"
    data = result.output_json["data"]
    pdf = Path(data["local_path"])
    assert pdf.read_bytes() == PDF
    assert data["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert data["bytes"] == len(PDF)
    assert data["invoice_ref"] == pdf_target[1]
    assert data["filename"] == "invoice.pdf"
    assert pdf.stat().st_mode & 0o777 == 0o600
    assert pdf.parent.stat().st_mode & 0o777 == 0o700
    requests = httpx_mock.get_requests()
    assert requests[-2].headers["Authorization"] == f"Bearer {STRIPE_SECRET}"
    assert "Authorization" not in requests[-1].headers
    assert "Stripe-Version" not in requests[-1].headers
    assert "Cookie" not in requests[-1].headers
    audit = session.exec(
        select(ActionCall).where(ActionCall.action_key == "stripe.invoices.pdf.download")
    ).one()
    assert audit.status == "success"
    assert PDF_URL not in json.dumps(audit.response_json)
    assert STRIPE_SECRET not in json.dumps(audit.response_json)
    before = len(requests)
    cleanup = _call(
        session, project_id, tmp_path, pdf_target, "cleanup", transfer_ref=data["transfer_ref"]
    )
    assert cleanup.output_json["data"]["cleanup_status"] == "deleted"
    assert not pdf.parent.exists()
    assert len(httpx_mock.get_requests()) == before


@pytest.mark.parametrize("case", ["draft", "mismatched-invoice", "missing-pdf", "not-finalized"])
def test_pdf_rejects_unavailable_or_mismatched_invoice_before_download(
    session, project_id, tmp_path, httpx_mock, pdf_target, case
) -> None:
    changes = {
        "draft": {"status": "draft"},
        "mismatched-invoice": {"id": "in_other"},
        "missing-pdf": {"invoice_pdf": None},
        "not-finalized": {"status_transitions": {"finalized_at": None}},
    }[case]
    invoice = stripe_invoice(
        id="in_pdf_fixture",
        status="open",
        invoice_pdf=PDF_URL,
        status_transitions={"finalized_at": 1790118000},
    )
    invoice.update(changes)
    httpx_mock.add_response(
        method="GET", url=f"{STRIPE_ROOT}/invoices/in_pdf_fixture", json=invoice
    )
    before = len(httpx_mock.get_requests())
    with pytest.raises(ConflictError):
        _call(session, project_id, tmp_path, pdf_target)
    assert len(httpx_mock.get_requests()) == before + 1
    assert not list(tmp_path.rglob("invoice.pdf"))


@pytest.mark.parametrize(
    ("url", "hostname", "client_rejects"),
    [
        ("file:///private/invoice.pdf", None, False),
        ("ftp://files.example.test/invoice.pdf", "files.example.test", False),
        ("data:application/pdf;base64,fixture", None, True),
        ("https://files.example.test:invalid/invoice.pdf", "files.example.test", True),
    ],
)
def test_pdf_rejects_unsupported_transport_redirect_without_forwarding_credentials(
    session, project_id, tmp_path, httpx_mock, pdf_target, url, hostname, client_rejects
) -> None:
    _invoice(httpx_mock)
    httpx_mock.add_response(method="GET", url=PDF_URL, status_code=302, headers={"Location": url})
    with pytest.raises(ConflictError) as caught:
        _call(session, project_id, tmp_path, pdf_target)
    detail = caught.value.data["provider_error"]
    # HTTPX may reject a Location while constructing next_request before yielding the response.
    assert detail["reason_code"] == (
        "pdf_transport_failure" if client_rejects else "unsupported_pdf_url"
    )
    assert detail["pdf_phase"] == ("download" if client_rejects else "redirect_url")
    assert detail["pdf_redirect_hop"] == (0 if client_rejects else 1)
    assert detail["pdf_hostname"] == ("pay.stripe.com" if client_rejects else hostname)
    assert "Authorization" not in httpx_mock.get_requests()[-1].headers
    assert not list(tmp_path.rglob("invoice.pdf"))


@pytest.mark.parametrize(
    ("url", "hostname"),
    [
        ("ftp://cdn.example.test/private-path?token=private-query", "cdn.example.test"),
        (
            "file://private-user:private-password@files.example.test/private-path",
            "files.example.test",
        ),
        ("https://pay.stripe.com:invalid/private-path", "pay.stripe.com"),
        ("https:///private-path?token=private-query", None),
        ("/private-path?token=private-query", None),
        (None, None),
    ],
)
def test_pdf_initial_url_diagnostics_preserve_only_phase_host_and_hop(
    session, project_id, tmp_path, httpx_mock, pdf_target, url, hostname
) -> None:
    _invoice(httpx_mock, invoice_pdf=url)
    before = len(httpx_mock.get_requests())
    with pytest.raises(ConflictError) as caught:
        _call(session, project_id, tmp_path, pdf_target)
    detail = caught.value.data["provider_error"]
    assert detail["reason_code"] == "unsupported_pdf_url"
    assert detail["pdf_phase"] == "initial_url"
    assert detail["pdf_hostname"] == hostname
    assert detail["pdf_redirect_hop"] == 0
    assert len(httpx_mock.get_requests()) == before + 1
    audit = session.exec(
        select(ActionCall).where(ActionCall.action_key == "stripe.invoices.pdf.download")
    ).one()
    assert audit.status == "failed"
    serialized = json.dumps([caught.value.data, audit.response_json])
    for private in ("private-path", "private-query", "private-user", "private-password"):
        assert private not in serialized
    assert not list(tmp_path.rglob("invoice.pdf"))


@pytest.mark.parametrize("via_redirect", [True, False])
@pytest.mark.parametrize(
    "target",
    [
        PDF_REDIRECT_URL,
        "https://arbitrary.example.test:8443/private-invoice.pdf?signature=private-token",
        "http://arbitrary.example.test:8080/private-invoice.pdf",
        "http://private-user:private-password@arbitrary.example.test:8080/invoice.pdf#private-fragment",
        "http://127.0.0.1:8080/invoice.pdf",
    ],
)
def test_pdf_accepts_provider_http_destinations_and_strips_url_credentials(
    session, project_id, tmp_path, httpx_mock, pdf_target, via_redirect, target
) -> None:
    requested_url = str(httpx.URL(target).copy_with(userinfo=b"", fragment=None))
    _invoice(httpx_mock, invoice_pdf=PDF_URL if via_redirect else target)
    if via_redirect:
        httpx_mock.add_response(
            method="GET",
            url=PDF_URL,
            status_code=302,
            headers={"Location": target, "Set-Cookie": "private-cookie=never-forward"},
        )
    httpx_mock.add_response(
        method="GET",
        url=requested_url,
        content=PDF,
        headers={"Content-Type": "application/pdf"},
    )
    result = _call(session, project_id, tmp_path, pdf_target)
    data = result.output_json["data"]
    assert Path(data["local_path"]).read_bytes() == PDF
    assert data["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert data["bytes"] == len(PDF)
    for request in httpx_mock.get_requests()[-(2 if via_redirect else 1) :]:
        assert "Authorization" not in request.headers
        assert "Stripe-Version" not in request.headers
        assert "Cookie" not in request.headers
        assert not request.url.userinfo
        assert not request.url.fragment
    serialized = json.dumps(result.model_dump(mode="json"))
    for private in (target, requested_url, "private-token", "private-user", "private-password"):
        assert private not in serialized


def test_pdf_redirect_chain_cannot_switch_to_local_file_transport(
    session, project_id, tmp_path, httpx_mock, pdf_target
) -> None:
    _invoice(httpx_mock)
    httpx_mock.add_response(
        method="GET", url=PDF_URL, status_code=302, headers={"Location": PDF_REDIRECT_URL}
    )
    httpx_mock.add_response(
        method="GET",
        url=PDF_REDIRECT_URL,
        status_code=302,
        headers={"Location": "file:///private-metadata?token=private-token"},
    )
    with pytest.raises(ConflictError) as caught:
        _call(session, project_id, tmp_path, pdf_target)
    detail = caught.value.data["provider_error"]
    assert detail["reason_code"] == "unsupported_pdf_url"
    assert detail["pdf_phase"] == "redirect_url"
    assert detail["pdf_hostname"] is None
    assert detail["pdf_redirect_hop"] == 2
    assert [str(request.url) for request in httpx_mock.get_requests()[-2:]] == [
        PDF_URL,
        PDF_REDIRECT_URL,
    ]
    assert "private-metadata" not in json.dumps(caught.value.data)
    assert "private-token" not in json.dumps(caught.value.data)
    assert not list(tmp_path.rglob("invoice.pdf"))


@pytest.mark.parametrize("via_redirect", [True, False])
@pytest.mark.parametrize("valid_pdf", [True, False])
def test_pdf_octet_stream_requires_pdf_bytes_for_initial_and_redirect_downloads(
    session, project_id, tmp_path, httpx_mock, pdf_target, via_redirect, valid_pdf
) -> None:
    _invoice(httpx_mock, invoice_pdf=PDF_URL if via_redirect else PDF_REDIRECT_URL)
    if via_redirect:
        httpx_mock.add_response(
            method="GET", url=PDF_URL, status_code=302, headers={"Location": PDF_REDIRECT_URL}
        )
    httpx_mock.add_response(
        method="GET",
        url=PDF_REDIRECT_URL,
        content=PDF if valid_pdf else b"<html>not a PDF</html>",
        headers={"Content-Type": "application/octet-stream"},
    )
    if valid_pdf:
        result = _call(session, project_id, tmp_path, pdf_target)
        data = result.output_json["data"]
        assert Path(data["local_path"]).read_bytes() == PDF
        assert data["sha256"] == hashlib.sha256(PDF).hexdigest()
        assert data["mime_type"] == "application/pdf"
    else:
        with pytest.raises(ConflictError) as caught:
            _call(session, project_id, tmp_path, pdf_target)
        detail = caught.value.data["provider_error"]
        assert detail["reason_code"] == "pdf_signature"
        assert detail["pdf_phase"] == "download"
        assert detail["pdf_redirect_hop"] == (1 if via_redirect else 0)
        assert not list(tmp_path.rglob("invoice.pdf"))


def test_pdf_verified_bucket_redirect_loop_remains_bounded(
    session, project_id, tmp_path, httpx_mock, pdf_target
) -> None:
    _invoice(httpx_mock)
    before = len(httpx_mock.get_requests())
    httpx_mock.add_response(
        method="GET", url=PDF_URL, status_code=302, headers={"Location": PDF_REDIRECT_URL}
    )
    for _ in range(3):
        httpx_mock.add_response(
            method="GET",
            url=PDF_REDIRECT_URL,
            status_code=302,
            headers={"Location": PDF_REDIRECT_URL, "Set-Cookie": "private-cookie=never-forward"},
        )
    with pytest.raises(ConflictError) as caught:
        _call(session, project_id, tmp_path, pdf_target)
    detail = caught.value.data["provider_error"]
    assert detail["reason_code"] == "pdf_redirect_limit"
    assert detail["pdf_hostname"] == PDF_REDIRECT_HOST
    assert detail["pdf_redirect_hop"] == 3
    requests = httpx_mock.get_requests()
    assert len(requests) == before + 5  # Authenticated invoice GET + four bounded PDF GETs.
    assert all("Cookie" not in request.headers for request in requests[-4:])
    assert all("Authorization" not in request.headers for request in requests[-4:])
    assert not list(tmp_path.rglob("invoice.pdf"))


@pytest.mark.parametrize("case", ["content-type", "signature", "size", "timeout", "http-status"])
def test_pdf_failure_has_no_file_or_false_success(
    session, project_id, tmp_path, httpx_mock, pdf_target, case
) -> None:
    _invoice(httpx_mock)
    if case == "timeout":
        httpx_mock.add_exception(
            httpx.ReadTimeout("private link must not escape"), method="GET", url=PDF_URL
        )
    else:
        httpx_mock.add_response(
            method="GET",
            url=PDF_URL,
            status_code=404 if case == "http-status" else 200,
            content=b"<html>secret</html>" if case == "signature" else PDF,
            headers={"Content-Type": "text/html" if case == "content-type" else "application/pdf"},
        )
    with pytest.raises(ConflictError) as caught:
        _call(session, project_id, tmp_path, pdf_target, max_bytes=8 if case == "size" else 1024)
    assert "private link must not escape" not in json.dumps(caught.value.data)
    assert caught.value.data["provider_error"]["pdf_phase"] == "download"
    assert caught.value.data["provider_error"]["pdf_hostname"] == "pay.stripe.com"
    assert caught.value.data["provider_error"]["pdf_redirect_hop"] == 0
    assert not list(tmp_path.rglob("invoice.pdf"))


def test_pdf_cleanup_is_bound_to_account_and_opaque_ref(
    session, project_id, tmp_path, httpx_mock, pdf_target
) -> None:
    _invoice(httpx_mock)
    httpx_mock.add_response(
        method="GET", url=PDF_URL, content=PDF, headers={"Content-Type": "application/pdf"}
    )
    downloaded = _call(session, project_id, tmp_path, pdf_target).output_json["data"]
    account = session.exec(select(CredentialAccount)).one()
    account.provider_account_id = "acct_different"
    session.add(account)
    session.commit()
    with pytest.raises(ConflictError):
        _call(
            session,
            project_id,
            tmp_path,
            pdf_target,
            "cleanup",
            transfer_ref=downloaded["transfer_ref"],
        )
    assert Path(downloaded["local_path"]).is_file()
    with pytest.raises((ValidationError, ConflictError)):
        _call(session, project_id, tmp_path, pdf_target, "cleanup", transfer_ref="../other")


def test_pdf_same_host_redirect_drops_cookies_and_checks_stream_size(
    session, project_id, tmp_path, httpx_mock, pdf_target
) -> None:
    _invoice(httpx_mock)
    redirected = "https://pay.stripe.com/invoice/opaque/pdf-final"
    httpx_mock.add_response(
        method="GET",
        url=PDF_URL,
        status_code=302,
        headers={"Location": redirected, "Set-Cookie": "private-cookie=never-forward"},
    )
    httpx_mock.add_response(
        method="GET",
        url=redirected,
        stream=IteratorStream([PDF[:8], PDF[8:]]),
        headers={"Content-Type": "application/pdf"},
    )
    with pytest.raises(ConflictError) as caught:
        _call(session, project_id, tmp_path, pdf_target, max_bytes=10)
    assert caught.value.data["provider_error"]["reason_code"] == "pdf_size_limit"
    assert caught.value.data["provider_error"]["pdf_phase"] == "download"
    assert caught.value.data["provider_error"]["pdf_redirect_hop"] == 1
    assert "Cookie" not in httpx_mock.get_requests()[-1].headers
    assert "Authorization" not in httpx_mock.get_requests()[-1].headers
    assert not list(tmp_path.rglob("invoice.pdf"))


def test_pdf_wrong_account_reference_makes_no_provider_request(
    session, project_id, tmp_path, httpx_mock, pdf_target
) -> None:
    account = session.exec(select(CredentialAccount)).one()
    account.provider_account_id = "acct_different"
    session.add(account)
    session.commit()
    before = len(httpx_mock.get_requests())
    with pytest.raises(ConflictError):
        _call(session, project_id, tmp_path, pdf_target)
    assert len(httpx_mock.get_requests()) == before


@pytest.mark.parametrize("case", ["truncated", "lying-length"])
def test_pdf_incomplete_body_is_rejected_with_authenticated_request_metadata(
    session, project_id, tmp_path, httpx_mock, pdf_target, case
) -> None:
    _invoice(httpx_mock)
    payload = b"%PDF-1.7\n" if case == "truncated" else PDF
    headers = {"Content-Type": "application/pdf"}
    if case == "lying-length":
        headers["Content-Length"] = str(len(PDF) + 10)
    httpx_mock.add_response(method="GET", url=PDF_URL, content=payload, headers=headers)
    with pytest.raises(ConflictError) as caught:
        _call(session, project_id, tmp_path, pdf_target)
    expected = "pdf_signature" if case == "truncated" else "pdf_length_mismatch"
    assert caught.value.data["provider_error"]["reason_code"] == expected
    audit = session.exec(
        select(ActionCall).where(ActionCall.action_key == "stripe.invoices.pdf.download")
    ).one()
    assert "req_pdf_invoice" in json.dumps(audit.metadata_json)
    assert "2026-08-26.dahlia" in json.dumps(audit.metadata_json)
    assert PDF_URL not in json.dumps(audit.metadata_json)
    assert not list(tmp_path.rglob("invoice.pdf"))


def test_pdf_partial_stage_retains_identity_for_account_verified_cleanup(
    session, project_id, tmp_path, httpx_mock, pdf_target, monkeypatch
) -> None:
    request = ActionConnectorRequest(
        project_id=project_id,
        plugin_slug="finance",
        action_key="stripe.invoices.pdf.download",
        action_ref="finance.stripe.invoices.pdf.download",
        provider_key="stripe",
        operation="rest.v1",
        input_json={},
        config_json={},
        asset_dir=tmp_path,
    )
    original_write = stripe_pdf._write_private

    def fail_pdf_write(path, payload):
        if path.name == "invoice.pdf":
            raise OSError("fixture write failure")
        original_write(path, payload)

    def fail_removal(_directory):
        raise OSError("fixture cleanup failure")

    with monkeypatch.context() as patch:
        patch.setattr(stripe_pdf, "_write_private", fail_pdf_write)
        patch.setattr(stripe_pdf, "_remove_transfer_files", fail_removal)
        with pytest.raises(ActionConnectorError) as caught:
            stripe_pdf._stage(request, pdf_target[1], PDF)
    transfer = caught.value.output_json
    assert transfer["cleanup_recovery"] == "retry-account-bound-cleanup"
    assert len(list(tmp_path.rglob("transfer.json"))) == 1
    cleaned = _call(
        session, project_id, tmp_path, pdf_target, "cleanup", transfer_ref=transfer["transfer_ref"]
    )
    assert cleaned.output_json["data"]["cleanup_status"] == "deleted"
    assert not list(tmp_path.rglob("transfer.json"))


def test_pdf_missing_identity_receipt_requires_operator_recovery(
    session, project_id, tmp_path, httpx_mock, pdf_target
) -> None:
    transfer_id = "f" * 32
    directory = tmp_path / "stripe-invoice-transfers" / f"project-{project_id}" / transfer_id
    directory.mkdir(parents=True)
    (directory / "invoice.pdf").write_bytes(PDF)
    with pytest.raises(ConflictError) as caught:
        _call(
            session,
            project_id,
            tmp_path,
            pdf_target,
            "cleanup",
            transfer_ref=f"stripe-pdf-transfer:{transfer_id}",
        )
    assert "operator-review" in json.dumps(caught.value.data)
    assert (directory / "invoice.pdf").read_bytes() == PDF


def test_pdf_staging_is_not_public_or_media_even_through_alias(tmp_path) -> None:
    private = tmp_path / "stripe-invoice-transfers" / "project-1" / "fixture"
    private.mkdir(parents=True)
    pdf = private / "invoice.pdf"
    pdf.write_bytes(PDF)
    (tmp_path / "public-alias.pdf").symlink_to(pdf)
    static = GeneratedAssetsStaticFiles(directory=tmp_path)
    for relative in ("stripe-invoice-transfers/project-1/fixture/invoice.pdf", "public-alias.pdf"):
        assert static.lookup_path(relative) == ("", None)
        with pytest.raises(ValidationError, match="private staging"):
            artifact_path(tmp_path, relative)
