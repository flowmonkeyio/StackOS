"""Unit tests for the M4 crypto layer (PLAN.md L1106-L1142).

Covers the AES-256-GCM encrypt/decrypt seam, AAD binding, nonce
discipline, seed-file mode validation, and ``rotate_seed`` re-encryption
of credential rows.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from stackos.crypto.aes_gcm import (
    NONCE_BYTES,
    CryptoError,
    configure_seed_path,
    decrypt,
    encrypt,
    format_aad,
)
from stackos.crypto.kdf import derive_key
from stackos.crypto.seed import (
    SeedFileError,
    abort_staged_seed_rotation,
    backup_seed_path,
    cleanup_old_backup,
    commit_staged_seed_rotation,
    ensure_seed_file,
    load_seed,
    reencrypt_rows_for_seed_rotation,
    rotate_seed,
    stage_seed_rotation,
    staged_seed_path,
)


@pytest.fixture
def seed_path(tmp_path: Path) -> Path:
    """Create a fresh seed file at 0600 and register it with the crypto layer."""
    p = tmp_path / "seed.bin"
    ensure_seed_file(p)
    configure_seed_path(p)
    return p


# ---------------------------------------------------------------------------
# encrypt / decrypt happy + adversarial paths.
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_round_trip(seed_path: Path) -> None:
    """A round trip with the same project_id+kind returns the original bytes."""
    plaintext = b"sk-live-abc123"
    ciphertext, nonce = encrypt(plaintext, project_id=42, kind="dataforseo")
    assert len(nonce) == NONCE_BYTES
    assert ciphertext != plaintext
    assert decrypt(ciphertext, nonce=nonce, project_id=42, kind="dataforseo") == plaintext


def test_aad_mismatch_project_id_raises_crypto_error(seed_path: Path) -> None:
    """Decrypting with a different project_id fails the GCM auth tag."""
    ciphertext, nonce = encrypt(b"secret", project_id=1, kind="firecrawl")
    with pytest.raises(CryptoError):
        decrypt(ciphertext, nonce=nonce, project_id=2, kind="firecrawl")


def test_aad_mismatch_kind_raises_crypto_error(seed_path: Path) -> None:
    """Decrypting with a different kind fails the GCM auth tag."""
    ciphertext, nonce = encrypt(b"secret", project_id=1, kind="firecrawl")
    with pytest.raises(CryptoError):
        decrypt(ciphertext, nonce=nonce, project_id=1, kind="dataforseo")


def test_global_aad_format(seed_path: Path) -> None:
    """``project_id=None`` rows use the literal ``global`` token in AAD."""
    aad = format_aad(None, "anthropic")
    assert aad == b"project_id=global|kind=anthropic"
    # Round-trip the AAD via encrypt/decrypt with project_id=None.
    ciphertext, nonce = encrypt(b"k", project_id=None, kind="anthropic")
    assert decrypt(ciphertext, nonce=nonce, project_id=None, kind="anthropic") == b"k"


def test_nonce_tamper_raises_crypto_error(seed_path: Path) -> None:
    """Mutating the nonce by 1 byte breaks decryption."""
    ciphertext, nonce = encrypt(b"hello", project_id=1, kind="firecrawl")
    bad_nonce = bytes((nonce[0] ^ 0x01,)) + nonce[1:]
    with pytest.raises(CryptoError):
        decrypt(ciphertext, nonce=bad_nonce, project_id=1, kind="firecrawl")


def test_ciphertext_tamper_raises_crypto_error(seed_path: Path) -> None:
    """Mutating any byte of the ciphertext breaks the auth tag."""
    ciphertext, nonce = encrypt(b"hello", project_id=1, kind="firecrawl")
    bad = bytearray(ciphertext)
    bad[0] ^= 0x01
    with pytest.raises(CryptoError):
        decrypt(bytes(bad), nonce=nonce, project_id=1, kind="firecrawl")


def test_short_nonce_rejected_by_decrypt(seed_path: Path) -> None:
    """Decrypting with a wrong-length nonce surfaces ``CryptoError`` not ValueError."""
    ciphertext, _ = encrypt(b"hello", project_id=1, kind="firecrawl")
    with pytest.raises(CryptoError):
        decrypt(ciphertext, nonce=b"\x00" * 8, project_id=1, kind="firecrawl")


def test_encrypt_rejects_non_bytes(seed_path: Path) -> None:
    """``encrypt`` refuses non-bytes plaintext early."""
    with pytest.raises(TypeError):
        encrypt("string-not-bytes", project_id=1, kind="firecrawl")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Seed file lifecycle.
# ---------------------------------------------------------------------------


def test_ensure_seed_file_generates_with_mode_0600(tmp_path: Path) -> None:
    """First call generates a 32-byte seed at mode 0600."""
    p = tmp_path / "seed.bin"
    seed = ensure_seed_file(p)
    assert len(seed) == 32
    assert p.exists()
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_load_seed_refuses_wrong_mode(tmp_path: Path) -> None:
    """A seed file at mode 0644 → SeedFileError."""
    p = tmp_path / "seed.bin"
    p.write_bytes(b"\x00" * 32)
    os.chmod(p, 0o644)
    with pytest.raises(SeedFileError) as exc_info:
        load_seed(p)
    assert "mode" in exc_info.value.detail


def test_load_seed_refuses_short_blob(tmp_path: Path) -> None:
    """A seed shorter than 32 bytes is refused."""
    p = tmp_path / "seed.bin"
    p.write_bytes(b"\x00" * 16)
    os.chmod(p, 0o600)
    with pytest.raises(SeedFileError):
        load_seed(p)


def test_load_seed_missing_raises(tmp_path: Path) -> None:
    """Missing seed → SeedFileError pointing at the path."""
    p = tmp_path / "nope.bin"
    with pytest.raises(SeedFileError):
        load_seed(p)


def test_kdf_deterministic_for_same_seed() -> None:
    """The HKDF derivation is deterministic — same seed → same key."""
    seed = b"\x01" * 32
    assert derive_key(seed) == derive_key(seed)


def test_kdf_changes_with_seed() -> None:
    """Different seeds yield different keys."""
    assert derive_key(b"\x01" * 32) != derive_key(b"\x02" * 32)


# ---------------------------------------------------------------------------
# rotate_seed re-encryption.
# ---------------------------------------------------------------------------


def test_rotate_seed_reencrypts_every_row_and_keeps_old_seed(tmp_path: Path) -> None:
    """``rotate_seed`` decrypts under the old seed, re-encrypts under the new one."""
    seed_path = tmp_path / "seed.bin"
    ensure_seed_file(seed_path)
    configure_seed_path(seed_path)

    # Encrypt two rows under the current seed.
    ct_a, n_a = encrypt(b"alpha-secret", project_id=1, kind="firecrawl")
    ct_b, n_b = encrypt(b"bravo-secret", project_id=2, kind="dataforseo")

    rows = [
        {
            "id": 1,
            "project_id": 1,
            "kind": "firecrawl",
            "encrypted_payload": ct_a,
            "nonce": n_a,
        },
        {
            "id": 2,
            "project_id": 2,
            "kind": "dataforseo",
            "encrypted_payload": ct_b,
            "nonce": n_b,
        },
    ]
    new_seed, rotated = rotate_seed(seed_path, rows=rows)
    # The seed file changed.
    assert load_seed(seed_path) == new_seed
    # ``seed.bin.bak`` exists for one boot.
    assert backup_seed_path(seed_path).exists()
    # Each rotated row has fresh ciphertext + nonce.
    assert rotated[0]["encrypted_payload"] != ct_a
    assert rotated[0]["nonce"] != n_a
    assert rotated[1]["encrypted_payload"] != ct_b

    # Reset the cache so the new seed file is loaded fresh.
    configure_seed_path(seed_path)
    # Decrypt round-trip under the new seed.
    assert (
        decrypt(
            rotated[0]["encrypted_payload"],
            nonce=rotated[0]["nonce"],
            project_id=1,
            kind="firecrawl",
        )
        == b"alpha-secret"
    )
    assert (
        decrypt(
            rotated[1]["encrypted_payload"],
            nonce=rotated[1]["nonce"],
            project_id=2,
            kind="dataforseo",
        )
        == b"bravo-secret"
    )


def test_seed_rotation_can_stage_after_db_reencrypt(tmp_path: Path) -> None:
    """Lower-level helpers let CLI commit DB rows before promoting the seed."""
    seed_path = tmp_path / "seed.bin"
    old_seed = ensure_seed_file(seed_path)
    configure_seed_path(seed_path)
    ct, nonce = encrypt(b"secret", project_id=1, kind="firecrawl")
    rows = [
        {
            "id": 1,
            "project_id": 1,
            "kind": "firecrawl",
            "encrypted_payload": ct,
            "nonce": nonce,
        }
    ]

    new_seed, rotated = reencrypt_rows_for_seed_rotation(seed_path, rows=rows)
    assert load_seed(seed_path) == old_seed
    assert rotated[0]["encrypted_payload"] != ct

    staged = stage_seed_rotation(seed_path, new_seed)
    assert staged == staged_seed_path(seed_path)
    assert load_seed(seed_path) == old_seed

    commit_staged_seed_rotation(seed_path)
    assert load_seed(seed_path) == new_seed
    assert backup_seed_path(seed_path).exists()


def test_staged_seed_blocks_boot_until_rotation_finishes(tmp_path: Path) -> None:
    """A crash with ``seed.bin.new`` present surfaces as explicit operator action."""
    seed_path = tmp_path / "seed.bin"
    ensure_seed_file(seed_path)
    stage_seed_rotation(seed_path, b"\x01" * 32)

    with pytest.raises(SeedFileError, match="incomplete seed rotation"):
        ensure_seed_file(seed_path)

    assert abort_staged_seed_rotation(seed_path) is True
    assert ensure_seed_file(seed_path)


def test_rotate_seed_aborts_on_decrypt_failure(tmp_path: Path) -> None:
    """A row that won't decrypt under the old seed aborts the rotation."""
    seed_path = tmp_path / "seed.bin"
    ensure_seed_file(seed_path)
    configure_seed_path(seed_path)
    # Encrypt a row, then mutate the ciphertext so decryption will fail.
    ct, n = encrypt(b"x", project_id=1, kind="firecrawl")
    rows = [
        {
            "id": 1,
            "project_id": 1,
            "kind": "firecrawl",
            "encrypted_payload": ct[:-1] + b"\x00",  # tampered tail
            "nonce": n,
        }
    ]
    with pytest.raises(CryptoError):
        rotate_seed(seed_path, rows=rows)
    # The seed file is untouched (not yet rotated).
    assert backup_seed_path(seed_path).exists() is False


def test_cleanup_old_backup_deletes_bak_after_one_boot(tmp_path: Path) -> None:
    """``cleanup_old_backup`` removes ``seed.bin.bak``; idempotent on repeat."""
    seed_path = tmp_path / "seed.bin"
    ensure_seed_file(seed_path)
    bak = backup_seed_path(seed_path)
    bak.write_bytes(b"\x00" * 32)
    os.chmod(bak, 0o600)
    assert cleanup_old_backup(seed_path) is True
    assert bak.exists() is False
    # Second call returns False without error.
    assert cleanup_old_backup(seed_path) is False
