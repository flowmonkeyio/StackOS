"""Canonical physical identities for provider communication surfaces."""

from __future__ import annotations

import hashlib


def communication_surface_binding_external_id(
    *,
    provider_key: str,
    profile_ref: str,
    surface_ref: str,
) -> str:
    """Return one bounded resource identity for a provider/profile/surface binding."""

    values = (provider_key.strip(), profile_ref.strip(), surface_ref.strip())
    canonical = "".join(f"{len(value)}:{value}" for value in values)
    return f"communication-surface:{hashlib.sha256(canonical.encode()).hexdigest()}"
