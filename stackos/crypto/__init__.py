"""Encryption-at-rest for integration credentials (M4 / PLAN.md L1106-L1140).

The crypto layer wraps Python's ``cryptography`` package so the rest of
the codebase never sees AES-GCM primitives directly. Public surface:

- ``ensure_seed_file(path)`` — generate the seed if absent, refuse to
  start otherwise (mirrors ``auth.ensure_token``).
- ``derive_key(seed)`` — HKDF-SHA256 → 32-byte AES-256 key.
- ``encrypt(plaintext, *, project_id, kind)`` and ``decrypt(...)`` —
  the only seam ``IntegrationCredentialRepository`` should call.
- ``CryptoError`` / ``SeedFileError`` — typed exceptions surfaced by the
  REST + MCP transports as ``-32603`` (internal) errors.
- ``rotate_seed`` — re-encrypts every row under a new seed in a single
  DB transaction (CLI ``rotate-seed --reencrypt`` per PLAN.md L1136).

The wire format inside ``integration_credentials.encrypted_payload`` is
``ciphertext || auth_tag`` (``cryptography``'s ``AESGCM.encrypt`` already
concatenates them). The ``nonce`` column carries the per-row 12-byte
random nonce. AAD = ``f"project_id={p}|kind={k}"`` where ``p`` is the
integer project_id or the literal string ``global`` for the project-less
case (PLAN.md L1119-L1122).
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
