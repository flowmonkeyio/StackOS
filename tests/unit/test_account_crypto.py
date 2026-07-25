"""Account-bound credential encryption contracts."""

from __future__ import annotations

import pytest

from stackos.crypto.aes_gcm import CryptoError, decrypt_account, encrypt_account


def test_account_ciphertext_is_bound_to_immutable_account_identity() -> None:
    seed = b"s" * 32
    ciphertext, nonce = encrypt_account(
        b"provider-secret",
        credential_ref="cred_first",
        provider_key="openrouter",
        seed=seed,
    )

    assert (
        decrypt_account(
            ciphertext,
            nonce=nonce,
            credential_ref="cred_first",
            provider_key="openrouter",
            seed=seed,
        )
        == b"provider-secret"
    )

    with pytest.raises(CryptoError, match="account identity"):
        decrypt_account(
            ciphertext,
            nonce=nonce,
            credential_ref="cred_second",
            provider_key="openrouter",
            seed=seed,
        )


def test_account_ciphertext_is_bound_to_provider_identity() -> None:
    seed = b"s" * 32
    ciphertext, nonce = encrypt_account(
        b"provider-secret",
        credential_ref="cred_first",
        provider_key="openrouter",
        seed=seed,
    )

    with pytest.raises(CryptoError, match="account identity"):
        decrypt_account(
            ciphertext,
            nonce=nonce,
            credential_ref="cred_first",
            provider_key="telegram-bot",
            seed=seed,
        )
