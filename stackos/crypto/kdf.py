"""HKDF-SHA256 key derivation for AES-256-GCM (PLAN.md L1114-L1116).

The seed (32 random bytes from disk) is the IKM. We derive a single
32-byte AES-256 key with the constant info string
``b"stackos:integration-credentials:v1"``. Per-row separation of
ciphertexts comes from the GCM nonce + AAD pair, not from per-row keys.

We expose only ``derive_key`` so callers cannot accidentally vary the
``info`` string and lose the ability to decrypt existing rows.
"""

from __future__ import annotations

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# Versioned info string. If we ever need to rotate the key derivation
# (e.g. switch hash algorithms) we bump the suffix; the seed file stays
# the same and the migration logic walks every credential row to
# re-derive under the new info.
_INFO = b"stackos:integration-credentials:v1"
_KEY_LENGTH = 32  # AES-256


def derive_key(seed: bytes) -> bytes:
    """Return a 32-byte AES-256 key derived from the seed.

    Uses HKDF-SHA256 with no salt (we have a per-install random IKM, so
    salt would be redundant). The single derivation is cheap; callers
    typically cache the result for the daemon's lifetime via a module-
    level singleton in ``aes_gcm`` rather than re-deriving on every
    call.
    """
    if len(seed) != 32:
        raise ValueError(f"seed must be 32 bytes, got {len(seed)}")
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_LENGTH,
        salt=None,
        info=_INFO,
    )
    return hkdf.derive(seed)


__all__ = ["derive_key"]
