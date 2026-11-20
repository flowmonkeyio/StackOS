"""Indexing uses the existing auth, validation, and action registry owners."""

import asyncio

import pytest

from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository
from stackos.repositories.base import ConflictError, ValidationError

from .test_google_service_accounts import service_key as service_key

SCOPE = "https://www.googleapis.com/auth/indexing"


def account(session, project_id, service_key, provider="google-indexing"):
    return (
        AuthRepository(session)
        .store_credential(
            provider_key=provider,
            auth_method_key="service-account",
            display_name="Synthetic Indexing",
            attach_project_id=project_id,
            fields={"service_account_json": service_key},
        )
        .data.credential_ref
    )


def test_indexing_actions_describe_and_validate_static_risks(
    session, project_id, service_key, httpx_mock
):
    ref = account(session, project_id, service_key)
    repo = ActionRepository(session)
    for kind in ("URL_UPDATED", "URL_DELETED"):
        described = repo.describe(action_ref="seo.indexing.url-notifications.publish")
        assert described.manifest.risk_level == "destructive"
        assert repo.validate(
            project_id=project_id,
            action_ref="seo.indexing.url-notifications.publish",
            credential_ref=ref,
            input_json={"url": "https://example.com/job", "type": kind},
        ).valid
    assert (
        repo.describe(action_ref="seo.indexing.url-notifications.metadata.get").manifest.risk_level
        == "read"
    )
    for payload in (
        {"url": "https://[", "type": "URL_UPDATED"},
        {"url": "not-url", "type": "URL_UPDATED"},
        {"url": "https://example.com/job", "type": []},
        {"url": "https://example.com/job", "type": "URL_UPDATED", "content_kind": "article"},
    ):
        assert not repo.validate(
            project_id=project_id,
            action_ref="seo.indexing.url-notifications.publish",
            credential_ref=ref,
            input_json=payload,
        ).valid
    assert not httpx_mock.get_requests()


@pytest.mark.parametrize("scope", ["", "https://www.googleapis.com/auth/webmasters.readonly"])
def test_indexing_scope_evidence_rejects_empty_or_wrong_grants(
    session, project_id, service_key, httpx_mock, scope
):
    ref = account(session, project_id, service_key)
    httpx_mock.add_response(
        url="https://oauth2.googleapis.com/token",
        json={
            "access_token": "synthetic-indexing",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": scope,
        },
    )
    with pytest.raises(ConflictError):
        asyncio.run(
            AuthRepository(session).resolve_for_execution(
                project_id=project_id,
                provider_key="google-indexing",
                credential_ref=ref,
                operation="url_notifications.metadata.get",
                required_scopes=[SCOPE],
            )
        )
    assert len(httpx_mock.get_requests()) == 1


def test_indexing_rejects_interactive_method_delegation_and_wrong_provider(
    session, project_id, service_key, httpx_mock
):
    repo = AuthRepository(session)
    for method, fields in (
        ("oauth2_authorization_code", {"client_id": "synthetic", "client_secret": "synthetic"}),
        (
            "service-account",
            {"service_account_json": service_key, "delegated_subject": "reader@example.com"},
        ),
    ):
        with pytest.raises(ValidationError):
            repo.store_credential(
                provider_key="google-indexing",
                auth_method_key=method,
                display_name="Rejected",
                attach_project_id=project_id,
                fields=fields,
            )
    wrong = account(session, project_id, service_key, "google-search-console")
    with pytest.raises(ValidationError):
        asyncio.run(
            repo.resolve_for_execution(
                project_id=project_id,
                provider_key="google-indexing",
                credential_ref=wrong,
                operation="url_notifications.publish",
                required_scopes=[SCOPE],
            )
        )
    assert not httpx_mock.get_requests()


@pytest.mark.parametrize("publish", [False, True])
def test_indexing_batch_validation_is_bounded_and_precedes_all_http(
    session, project_id, service_key, httpx_mock, publish
):
    ref = account(session, project_id, service_key)
    repo = ActionRepository(session)
    action = "seo.indexing.batch.publish" if publish else "seo.indexing.batch.metadata.get"
    base = {"type": "URL_UPDATED"} if publish else {}

    def validate(payload):
        return repo.validate(
            project_id=project_id, action_ref=action, credential_ref=ref, input_json=payload
        )

    assert validate({**base, "urls": ["https://example.com/job"] * 100}).valid
    for payload in (
        {**base, "urls": []},
        {**base, "urls": ["https://example.com/job"] * 101},
        {**base, "urls": ["https://example.com/job", "https://["]},
        {**base, "urls": ["https://example.com/job", 7]},
        {**base, "urls": ["https://example.com/job"], "endpoint": "https://other.example/"},
        {**base, "urls": [{"urls": ["https://example.com/job"]}]},
        {**base, "urls": ["https://example.com/" + "é" * 200_000]},
    ):
        assert not validate(payload).valid
    if publish:
        assert not validate({"urls": ["https://example.com/job"], "type": []}).valid
    assert not httpx_mock.get_requests()
