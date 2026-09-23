"""Canonical physical identities for communication surfaces."""

from __future__ import annotations

from stackos.communication_surface_bindings import communication_surface_binding_external_id


def test_surface_binding_external_id_is_stable_and_scoped_to_profile_and_provider() -> None:
    first = communication_surface_binding_external_id(
        provider_key="slack-bot",
        profile_ref="communication-profile:support",
        surface_ref="slack-channel:C123",
    )

    assert first == communication_surface_binding_external_id(
        provider_key=" slack-bot ",
        profile_ref=" communication-profile:support ",
        surface_ref=" slack-channel:C123 ",
    )
    assert first != communication_surface_binding_external_id(
        provider_key="slack-bot",
        profile_ref="communication-profile:marketing",
        surface_ref="slack-channel:C123",
    )
    assert first != communication_surface_binding_external_id(
        provider_key="telegram",
        profile_ref="communication-profile:support",
        surface_ref="slack-channel:C123",
    )
    assert first != communication_surface_binding_external_id(
        provider_key="slack-bot\x00communication-profile:support",
        profile_ref="",
        surface_ref="slack-channel:C123",
    )
    assert first.startswith("communication-surface:")
    assert len(first) <= 300
