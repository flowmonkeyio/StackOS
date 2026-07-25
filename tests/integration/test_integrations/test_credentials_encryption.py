"""End-to-end credential encryption via the repository layer.

Mirrors the unit-level crypto test (``test_crypto.py``) but exercises
the seam through ``IntegrationCredentialRepository`` so the
encrypt-on-set / decrypt-on-get contract is verified at the API
boundary that integration wrappers actually use.
"""

from __future__ import annotations

from sqlmodel import Session, select

from stackos.db.models import Credential
from stackos.repositories.projects import IntegrationCredentialRepository
from tests.integration.account_test_support import seed_test_account


def test_set_then_get_decrypted_round_trips_payload(session: Session, project_id: int) -> None:
    """``set`` persists ciphertext; ``get_decrypted`` returns the original bytes."""
    repo = IntegrationCredentialRepository(session)
    out = seed_test_account(
        session,
        project_id=project_id,
        provider_key="dataforseo",
        display_name="DataForSEO - Default",
        secret_payload=b"sk-original",
    )
    plaintext = repo.get_decrypted(out.data.id)
    assert plaintext == b"sk-original"


def test_get_decrypted_requires_explicit_row_id_for_each_account(
    session: Session, project_id: int
) -> None:
    """Decrypting credential material does not resolve by provider or display name."""
    repo = IntegrationCredentialRepository(session)
    first = seed_test_account(
        session,
        project_id=project_id,
        provider_key="firecrawl",
        display_name="Firecrawl - Primary",
        secret_payload=b"FIRST",
    )
    second = seed_test_account(
        session,
        project_id=project_id,
        provider_key="firecrawl",
        display_name="Firecrawl - Secondary",
        secret_payload=b"SECOND",
    )

    assert repo.get_decrypted(first.data.id) == b"FIRST"
    assert repo.get_decrypted(second.data.id) == b"SECOND"
    assert first.data.id != second.data.id


def test_set_updates_exact_account_backing(session: Session, project_id: int) -> None:
    """Updating an Account reuses its exact backing row."""
    repo = IntegrationCredentialRepository(session)
    out_a = seed_test_account(
        session,
        project_id=project_id,
        provider_key="firecrawl",
        display_name="Firecrawl - Default",
        secret_payload=b"first",
    )
    account = session.exec(
        select(Credential).where(
            Credential.integration_credential_id == out_a.data.id,
        )
    ).one()
    out_b = repo.set(
        credential_ref=account.credential_ref,
        provider_key="firecrawl",
        integration_credential_id=out_a.data.id,
        secret_payload=b"second",
    )
    assert out_a.data.id == out_b.data.id
    assert repo.get_decrypted(out_b.data.id) == b"second"


def test_cross_machine_seed_swap_breaks_decryption(
    session: Session,
    project_id: int,
    tmp_path,
    _crypto_seed,
) -> None:
    """Configuring a different seed file post-encrypt breaks decryption.

    Simulates "restored DB without the seed file" — the ciphertext
    must surface as ``CryptoError``, never silently decrypt to garbage.
    The autouse session fixture's seed is captured via the
    ``_crypto_seed`` fixture parameter so we can restore after the
    swap, ensuring downstream tests still see the canonical seed.
    """
    import pytest

    from stackos.crypto.aes_gcm import CryptoError, configure_seed_path
    from stackos.crypto.seed import ensure_seed_file

    repo = IntegrationCredentialRepository(session)
    out = seed_test_account(
        session,
        project_id=project_id,
        provider_key="firecrawl",
        display_name="Firecrawl - Default",
        secret_payload=b"original",
    )
    cred_id = out.data.id

    # Swap to a fresh seed file. The old credential was encrypted under
    # the session-fixture seed; the new seed yields a different key.
    new_seed_path = tmp_path / "alien-seed.bin"
    ensure_seed_file(new_seed_path)
    configure_seed_path(new_seed_path)
    try:
        with pytest.raises(CryptoError):
            repo.get_decrypted(cred_id)
    finally:
        # Restore the canonical session seed.
        configure_seed_path(_crypto_seed)
