"""Clean-cut retirement checks for the legacy Telegram HTTP ingress."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError as PydanticValidationError

from stackos.auth import WHITELIST_PREFIXES
from stackos.operations.communication_platform.ingress import _provider_ingress_path
from stackos.operations.communication_platform.schemas import (
    IngressEndpointRefreshInput,
    IngressEndpointSyncInput,
)
from stackos.repositories.base import ValidationError
from stackos.server import _PUBLIC_INGRESS_PREFIXES


def test_legacy_telegram_webhook_has_no_registered_or_public_path(api: TestClient) -> None:
    """TDLib owns Telegram updates; the old HTTP webhook must not remain reachable."""

    legacy_prefix = "/api/v1/ingress/telegram"

    assert all(not getattr(route, "path", "").startswith(legacy_prefix) for route in api.app.routes)
    assert legacy_prefix not in WHITELIST_PREFIXES
    assert legacy_prefix not in _PUBLIC_INGRESS_PREFIXES

    response = api.post(f"{legacy_prefix}/1/support-bot", json={"update_id": 1})

    # The application shell may reserve a GET-only fallback for unknown paths.
    # Neither result is a callable Telegram ingress route.
    assert response.status_code in {404, 405}


def test_telegram_has_no_webhook_route_or_automatic_sync_contract() -> None:
    with pytest.raises(PydanticValidationError):
        IngressEndpointSyncInput(project_id=1, apply_provider_webhooks=True)
    with pytest.raises(PydanticValidationError):
        IngressEndpointRefreshInput(project_id=1, dry_run_provider_webhooks=False)
    with pytest.raises(ValidationError, match="does not support webhook ingress"):
        _provider_ingress_path(project_id=1, provider_key="telegram", profile_key="support")
