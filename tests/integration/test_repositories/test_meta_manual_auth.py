"""Fail-closed proof for non-interactive Meta OAuth tokens."""

from __future__ import annotations

import asyncio

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session

from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository
from stackos.repositories.base import ConflictError


@pytest.mark.parametrize(
    ("action_ref", "input_json", "required_scopes"),
    [
        ("media-buying.meta.account.list", {}, ["ads_read"]),
        (
            "media-buying.meta.campaign.create",
            {
                "account_ref": "primary",
                "campaign": {
                    "name": "Blocked campaign",
                    "objective": "OUTCOME_TRAFFIC",
                    "special_ad_categories": [],
                },
            },
            ["ads_management"],
        ),
    ],
)
def test_meta_manual_oauth_token_stays_scope_blocked_before_provider_http(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    action_ref: str,
    input_json: dict[str, object],
    required_scopes: list[str],
) -> None:
    stored = (
        AuthRepository(session)
        .store_credential(
            project_id=project_id,
            provider_key="meta-ads",
            auth_method_key="oauth2_token",
            profile_key="meta-manual-token",
            fields={
                "access_token": "meta-manual-token-canary",
                "business_ref": "business-primary",
                "api_version": "v25.0",
            },
        )
        .data
    )

    with pytest.raises(ConflictError, match="credential scopes are unknown") as exc_info:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref=action_ref,
                input_json=input_json,
                credential_ref=stored.credential_ref,
            )
        )

    assert httpx_mock.get_requests() == []
    rendered = str(exc_info.value.data)
    assert "meta-manual-token-canary" not in rendered
    assert exc_info.value.data["required_scopes"] == required_scopes
