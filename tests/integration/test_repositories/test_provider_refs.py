from __future__ import annotations

from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.db.models import Credential, CredentialAccount, ProviderObjectReference
from stackos.repositories.base import ConflictError, NotFoundError
from stackos.repositories.provider_refs import ProviderObjectReferenceRepository
from tests.integration.account_test_support import seed_test_account


def _credential(
    session: Session,
    *,
    project_id: int,
    ref: str,
    account_id: str | None,
) -> Credential:
    backing = seed_test_account(
        session,
        project_id=project_id,
        provider_key="hubspot",
        display_name=ref,
        secret_payload=b'{"access_token":"test"}',
        config_json={"auth_method_key": "oauth2_authorization_code"},
    )
    credential = session.exec(
        select(Credential).where(
            Credential.integration_credential_id == backing.data.id,
        )
    ).one()
    assert credential.id is not None
    if account_id is not None:
        session.add(
            CredentialAccount(
                credential_id=credential.id,
                provider_account_id=account_id,
            )
        )
        session.flush()
    return credential


def test_provider_object_refs_are_stable_opaque_and_account_type_bound(
    session: Session,
    project_id: int,
) -> None:
    primary = _credential(
        session,
        project_id=project_id,
        ref="cred_hubspot_primary",
        account_id="portal-1",
    )
    other_account = _credential(
        session,
        project_id=project_id,
        ref="cred_hubspot_other",
        account_id="portal-2",
    )
    repo = ProviderObjectReferenceRepository(session, project_id=project_id)

    safe_ref = repo.upsert(
        credential=primary,
        object_type="contact",
        provider_object_id="123456",
        display_name="Ada Example",
        metadata_json={"api_key": "must-redact", "kind": "contact"},
    )
    repeated = repo.upsert(
        credential=primary,
        object_type="contact",
        provider_object_id="123456",
        display_name="Ada Updated",
    )

    assert safe_ref == repeated
    assert safe_ref.startswith("provider-object:")
    assert "123456" not in safe_ref
    resolved = repo.resolve(
        credential=primary,
        safe_ref=safe_ref,
        expected_object_type="contact",
    )
    assert resolved.provider_object_id == "123456"
    assert "123456" not in repr(resolved)
    row = session.exec(
        select(ProviderObjectReference).where(ProviderObjectReference.safe_ref == safe_ref)
    ).one()
    assert row.display_name == "Ada Updated"
    assert row.metadata_json == {"api_key": "[redacted]", "kind": "contact"}

    for credential, object_type in (
        (other_account, "contact"),
        (primary, "company"),
    ):
        try:
            repo.resolve(
                credential=credential,
                safe_ref=safe_ref,
                expected_object_type=object_type,
            )
        except NotFoundError:
            pass
        else:  # pragma: no cover - assertion message is more useful than raises context here
            raise AssertionError("cross-account/type provider ref unexpectedly resolved")


def test_provider_object_ref_staleness_fails_closed(
    session: Session,
    project_id: int,
) -> None:
    credential = _credential(
        session,
        project_id=project_id,
        ref="cred_hubspot_stale",
        account_id="portal-1",
    )
    repo = ProviderObjectReferenceRepository(session, project_id=project_id)
    safe_ref = repo.upsert(
        credential=credential,
        object_type="deal",
        provider_object_id="deal-1",
    )

    repo.mark_stale(
        credential=credential,
        safe_ref=safe_ref,
        expected_object_type="deal",
    )

    try:
        repo.resolve(
            credential=credential,
            safe_ref=safe_ref,
            expected_object_type="deal",
        )
    except ConflictError as exc:
        assert "Refresh provider metadata" in exc.data["next_action"]
    else:  # pragma: no cover
        raise AssertionError("stale provider ref unexpectedly resolved")


def test_provider_object_refs_require_one_verified_account(
    session: Session,
    project_id: int,
) -> None:
    credential = _credential(
        session,
        project_id=project_id,
        ref="cred_hubspot_no_account",
        account_id=None,
    )

    try:
        ProviderObjectReferenceRepository(session, project_id=project_id).upsert(
            credential=credential,
            object_type="contact",
            provider_object_id="123",
        )
    except ConflictError as exc:
        assert exc.data["account_count"] == 0
    else:  # pragma: no cover
        raise AssertionError("provider ref unexpectedly accepted an unverified account")


def test_provider_object_refs_use_the_shared_project_attachment_boundary(
    session: Session,
    project_id: int,
) -> None:
    credential = _credential(
        session,
        project_id=project_id,
        ref="cred_hubspot_detached",
        account_id="portal-1",
    )
    AuthRepository(session).detach_account(
        project_id=project_id,
        credential_ref=credential.credential_ref,
    )

    try:
        ProviderObjectReferenceRepository(session, project_id=project_id).upsert(
            credential=credential,
            object_type="contact",
            provider_object_id="123",
        )
    except NotFoundError as exc:
        assert "not attached" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("provider ref unexpectedly bypassed Account attachment")
