"""Encryption-at-rest for reusable Account backings and daemon-held secrets.

The crypto layer wraps Python's ``cryptography`` package so the rest of
the codebase never sees AES-GCM primitives directly. Public surface:

- ``ensure_seed_file(path)`` — generate the seed if absent, refuse to
  start otherwise (mirrors ``auth.ensure_token``).
- ``derive_key(seed)`` — HKDF-SHA256 → 32-byte AES-256 key.
- ``encrypt_account(..., credential_ref, provider_key)`` and
  ``decrypt_account(...)`` bind reusable Account backing rows to their stable
  global identity.
- Legacy ``encrypt(..., project_id, kind)`` and ``decrypt(...)`` remain for
  payload secrets and the pre-global-Account migration path.
- ``CryptoError`` / ``SeedFileError`` — typed exceptions surfaced by the
  REST + MCP transports as ``-32603`` (internal) errors.
- ``rotate_seed`` — re-encrypts every row under a new seed in a single
  DB transaction (CLI ``rotate-seed --reencrypt`` per PLAN.md L1136).

The wire format is ``ciphertext || auth_tag`` (``cryptography``'s
``AESGCM.encrypt`` already concatenates them), with a per-row 12-byte nonce.
Current Account AAD is derived from ``credential_ref`` and ``provider_key``;
legacy project/kind AAD is not the Account ownership model.
"""

from __future__ import annotations

from stackos.crypto.aes_gcm import (
    CryptoError,
    decrypt,
    encrypt,
    format_aad,
)
from stackos.crypto.kdf import derive_key
from stackos.crypto.seed import (
    SeedFileError,
    backup_seed_path,
    cleanup_old_backup,
    ensure_seed_file,
    load_seed,
    rotate_seed,
)

__all__ = [
    "CryptoError",
    "SeedFileError",
    "backup_seed_path",
    "cleanup_old_backup",
    "decrypt",
    "derive_key",
    "encrypt",
    "ensure_seed_file",
    "format_aad",
    "load_seed",
    "rotate_seed",
]
