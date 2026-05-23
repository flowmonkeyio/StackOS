"""End-to-end credential encryption via the repository layer.

Mirrors the unit-level crypto test (``test_crypto.py``) but exercises
the seam through ``IntegrationCredentialRepository`` so the
encrypt-on-set / decrypt-on-get contract is verified at the API
boundary that integration wrappers actually use.
"""

from __future__ import annotations

from sqlmodel import Session

from stackos.repositories.projects import IntegrationCredentialRepository


def test_set_then_get_decrypted_round_trips_payload(session: Session, project_id: int) -> None:
    """``set`` persists ciphertext; ``get_decrypted`` returns the original bytes."""
    repo = IntegrationCredentialRepository(session)
    out = repo.set(
        project_id=project_id,
        kind="dataforseo",
        secret_payload=b"sk-original",
        config_json={"login": "alice"},
    )
    plaintext = repo.get_decrypted(out.data.id)
    assert plaintext == b"sk-original"


def test_get_decrypted_requires_explicit_row_id_for_each_scope(
    session: Session, project_id: int
) -> None:
    """Decrypting credential material does not resolve by provider kind/profile."""
    repo = IntegrationCredentialRepository(session)
    global_row = repo.set(project_id=None, kind="firecrawl", secret_payload=b"GLOBAL")
    project_row = repo.set(project_id=project_id, kind="firecrawl", secret_payload=b"PROJECT")

    assert repo.get_decrypted(project_row.data.id) == b"PROJECT"
    assert repo.get_decrypted(global_row.data.id) == b"GLOBAL"
    assert project_row.data.id != global_row.data.id


def test_set_overwrites_existing_row_for_same_project_and_kind(
    session: Session, project_id: int
) -> None:
    """Re-setting the same (project_id, kind) updates ciphertext + nonce."""
    repo = IntegrationCredentialRepository(session)
    out_a = repo.set(project_id=project_id, kind="firecrawl", secret_payload=b"first")
    out_b = repo.set(project_id=project_id, kind="firecrawl", secret_payload=b"second")
    # Same row id (upsert), but new payload bytes.
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
    out = repo.set(project_id=project_id, kind="firecrawl", secret_payload=b"original")
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
