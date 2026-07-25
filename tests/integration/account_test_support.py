"""Test-only Account seeding for consumer and connector integration tests.

Auth lifecycle tests should prefer ``AuthRepository.store_credential``. Consumer
tests that need a precise acquired-token payload can use this helper to seed the
same global Account plus explicit project attachment invariant without
reimplementing provider setup.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session, col, select

from stackos.auth_providers import AuthRepository
from stackos.auth_providers.repository.utils import credential_ref as new_credential_ref
from stackos.db.models import AuthProvider, Credential, ProjectCredential
from stackos.repositories.base import Envelope
from stackos.repositories.projects import (
    IntegrationCredentialOut,
    IntegrationCredentialRepository,
)


def seed_test_account(
    session: Session,
    *,
    project_id: int | None,
    provider_key: str,
    secret_payload: bytes,
    display_name: str | None = None,
    config_json: dict[str, Any] | None = None,
    expires_at: datetime | None = None,
    status: str = "connected",
) -> Envelope[IntegrationCredentialOut]:
    """Seed one reusable Account and optionally attach it to a project."""

    AuthRepository(session).sync_providers()
    provider = session.exec(
        select(AuthProvider).where(col(AuthProvider.key) == provider_key)
    ).first()
    safe_config = dict(config_json or {})
    legacy_profile = str(safe_config.pop("profile_key", "") or "").strip()
    legacy_label = str(safe_config.pop("label", "") or "").strip()
    account_name = (
        display_name
        or legacy_label
        or (f"{provider_key} - {legacy_profile}" if legacy_profile else f"{provider_key} - Default")
    )
    account_name = " ".join(account_name.split())
    auth_type = provider.auth_type if provider is not None else "api-key"
    methods = (provider.config_json or {}).get("auth_methods", []) if provider is not None else []
    method_key = str(safe_config.get("auth_method_key") or "")
    if not method_key:
        first_method = next(
            (method for method in methods if isinstance(method, dict) and method.get("key")),
            None,
        )
        method_key = str(first_method["key"]) if first_method is not None else "default"
    safe_config["auth_method_key"] = method_key
    if provider is not None:
        for method in methods:
            if isinstance(method, dict) and method.get("key") == method_key:
                auth_type = str(method.get("auth_type") or auth_type)
                break

    existing = session.exec(
        select(Credential).where(
            col(Credential.provider_key) == provider_key,
            col(Credential.display_name_key) == account_name.casefold(),
        )
    ).first()
    account_ref = existing.credential_ref if existing is not None else new_credential_ref()
    env = IntegrationCredentialRepository(session).set(
        credential_ref=account_ref,
        provider_key=provider_key,
        secret_payload=secret_payload,
        integration_credential_id=(
            existing.integration_credential_id if existing is not None else None
        ),
        commit=False,
    )
    if existing is None:
        credential = Credential(
            auth_provider_id=provider.id if provider is not None else None,
            integration_credential_id=env.data.id,
            credential_ref=account_ref,
            provider_key=provider_key,
            display_name=account_name,
            display_name_key=account_name.casefold(),
            auth_type=auth_type,
            auth_method_key=method_key,
            status=status,
            expires_at=expires_at,
            config_json=safe_config,
        )
        session.add(credential)
        session.flush()
    else:
        credential = existing
        credential.integration_credential_id = env.data.id
        credential.auth_provider_id = provider.id if provider is not None else None
        credential.auth_type = auth_type
        credential.auth_method_key = method_key
        credential.status = status
        credential.expires_at = expires_at
        credential.config_json = safe_config
        session.add(credential)
        session.flush()

    assert credential.id is not None
    if project_id is not None:
        attached = session.exec(
            select(ProjectCredential).where(
                col(ProjectCredential.project_id) == project_id,
                col(ProjectCredential.credential_id) == credential.id,
            )
        ).first()
        if attached is None:
            session.add(
                ProjectCredential(
                    project_id=project_id,
                    credential_id=credential.id,
                    attached_by="test-fixture",
                )
            )
    session.commit()
    return env
