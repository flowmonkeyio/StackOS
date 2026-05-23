"""AES-256-GCM encrypt / decrypt with project-bound AAD (PLAN.md L1117-L1124).

Every row in ``integration_credentials`` is encrypted under the same
HKDF-derived key (PLAN.md L1114-L1116) but with a fresh per-row 12-byte
nonce and an AAD that binds the ciphertext to its row context::

    AAD = f"project_id={p}|kind={k}".encode()

where ``p`` is the integer ``project_id`` for project-scoped rows or the
literal string ``global`` for project-less rows. Tampering with either
column or moving a row between projects renders the ciphertext
undecryptable.

The wire format inside ``encrypted_payload`` is ``ciphertext || auth_tag``
— that's what ``cryptography``'s ``AESGCM.encrypt`` returns natively, so
we simply persist the byte string without further wrapping.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from stackos.crypto.kdf import derive_key
from stackos.repositories.base import RepositoryError

NONCE_BYTES = 12  # GCM standard


class CryptoError(RepositoryError):
    """Raised when AES-GCM authentication fails or seed I/O misbehaves.

    Mapped to JSON-RPC -32603 (internal error) — a credential that
    won't decrypt is a wrong-machine, wrong-seed, or tampered-row signal
    we surface verbatim to the operator, never silently mask.
    """

    code = -32603
    http_status = 500
    retryable = False


# ---------------------------------------------------------------------------
# Key cache.
# ---------------------------------------------------------------------------


# The HKDF derivation is cheap but the seed file read is cached for the
# daemon's lifetime so request-path encryption stays I/O-free. The cache
# is keyed on the seed *bytes* so the same daemon can hold two derived
# keys briefly during a rotation (rare).
_lock = threading.Lock()
_cache: dict[bytes, bytes] = {}
_default_seed_path: Path | None = None


def configure_seed_path(path: Path) -> None:
    """Register the seed file path the encrypt/decrypt helpers will read.

    Called once during daemon startup (``server._ensure_seed`` already
    creates the file; this just remembers where to load it from). Tests
    pass an isolated tmp path via the conftest fixture.
    """
    global _default_seed_path
    _default_seed_path = path
    # Drop any cached keys from a previous configuration so tests with
    # different seeds don't see stale derivations.
    with _lock:
        _cache.clear()


def _seed_for_call(seed: bytes | None) -> bytes:
    """Resolve the seed bytes for the current encrypt/decrypt call.

    Priority: explicit ``seed`` argument (used by ``rotate_seed`` and
    tests) → cached default → fresh load from ``_default_seed_path``.
    """
    if seed is not None:
        return seed
    # Local import dodges a circular dependency at module load.
    from stackos.crypto.seed import load_seed

    if _default_seed_path is None:
        raise CryptoError(
            "crypto seed path not configured — call configure_seed_path() at daemon startup",
        )
    return load_seed(_default_seed_path)


def _key_for_seed(seed: bytes) -> bytes:
    """Return the cached HKDF-derived key for ``seed``."""
    with _lock:
        cached = _cache.get(seed)
        if cached is not None:
            return cached
        derived = derive_key(seed)
        _cache[seed] = derived
        return derived


# ---------------------------------------------------------------------------
# AAD format.
# ---------------------------------------------------------------------------


def format_aad(project_id: int | None, kind: str) -> bytes:
    """Build the per-row AAD per PLAN.md L1119-L1122.

    Project-scoped rows: ``project_id={int}|kind={str}``.
    Global rows (``project_id IS NULL``): ``project_id=global|kind={str}``.
    """
    p = "global" if project_id is None else str(int(project_id))
    return f"project_id={p}|kind={kind}".encode()


# ---------------------------------------------------------------------------
# Public encrypt / decrypt.
# ---------------------------------------------------------------------------


def encrypt(
    plaintext: bytes,
    *,
    project_id: int | None,
    kind: str,
    seed: bytes | None = None,
    nonce: bytes | None = None,
) -> tuple[bytes, bytes]:
    """Encrypt ``plaintext`` under the seed-derived AES-256-GCM key.

    Returns ``(ciphertext_with_tag, nonce)``. The caller persists both
    columns: ``encrypted_payload = ciphertext_with_tag``, ``nonce =
    nonce``.

    ``seed`` is for testing + rotation; production callers leave it
    ``None`` and the helper resolves it via ``configure_seed_path``.
    ``nonce`` is for testing only — production always generates a fresh
    nonce per row (PLAN.md L1117).
    """
    if not isinstance(plaintext, bytes):
        raise TypeError("plaintext must be bytes")
    seed_bytes = _seed_for_call(seed)
    key = _key_for_seed(seed_bytes)
    aesgcm = AESGCM(key)
    nonce_bytes = nonce if nonce is not None else os.urandom(NONCE_BYTES)
    if len(nonce_bytes) != NONCE_BYTES:
        raise ValueError(f"nonce must be {NONCE_BYTES} bytes, got {len(nonce_bytes)}")
    aad = format_aad(project_id, kind)
    payload = aesgcm.encrypt(nonce_bytes, plaintext, aad)
    return payload, nonce_bytes


def decrypt(
    ciphertext: bytes,
    *,
    nonce: bytes,
    project_id: int | None,
    kind: str,
    seed: bytes | None = None,
) -> bytes:
    """Decrypt ``ciphertext`` (must be ``ciphertext || tag``) under the seed.

    Raises ``CryptoError`` on auth tag mismatch (``InvalidTag``) — the
    caller never gets back garbage data. Mismatched ``project_id`` or
    ``kind`` bypassed via SQL UPDATE will fail here because the AAD
    won't match (PLAN.md L1119-L1122).
    """
    if not isinstance(ciphertext, bytes):
        raise TypeError("ciphertext must be bytes")
    if len(nonce) != NONCE_BYTES:
        raise CryptoError(
            f"nonce length {len(nonce)} != {NONCE_BYTES}",
            data={"project_id": project_id, "kind": kind},
        )
    seed_bytes = _seed_for_call(seed)
    key = _key_for_seed(seed_bytes)
    aesgcm = AESGCM(key)
    aad = format_aad(project_id, kind)
    try:
        return aesgcm.decrypt(nonce, ciphertext, aad)
    except InvalidTag as exc:
        raise CryptoError(
            "credential decryption failed (auth tag mismatch — wrong seed, "
            "wrong project_id/kind, or tampered row)",
            data={"project_id": project_id, "kind": kind},
        ) from exc


__all__ = [
    "NONCE_BYTES",
    "CryptoError",
    "configure_seed_path",
    "decrypt",
    "encrypt",
    "format_aad",
]
