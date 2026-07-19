"""Stable communication provider identifier coverage."""

from __future__ import annotations

from stackos.actions.slack_bot import refs as slack_action_refs
from stackos.api import slack_ingress
from stackos.communications.provider_ids import (
    slack_message_ref,
    slack_outbound_button_external_id,
    slack_surface_ref,
    slack_thread_ref,
)


def test_slack_provider_ids_are_exact_and_shared_by_both_consumers() -> None:
    message_ref = "slack-message:C123:1770000000.000100"

    assert slack_surface_ref("C123") == "slack-channel:C123"
    assert slack_message_ref("C123", "1770000000.000100") == message_ref
    assert slack_thread_ref("C123", "1770000000.000100") == ("slack-thread:C123:1770000000.000100")
    assert (
        slack_outbound_button_external_id(
            profile_key="support-agent",
            message_ref=message_ref,
            action_id="approve",
            value="approuvé_177",
            block_id="décision",
        )
        == "slack-button:support-agent:7f600f20f3c8fb9246b581ac"
    )

    assert slack_action_refs._surface_ref is slack_surface_ref
    assert slack_action_refs._message_ref is slack_message_ref
    assert slack_action_refs._thread_ref is slack_thread_ref
    assert slack_action_refs._outbound_button_external_id is slack_outbound_button_external_id
    assert slack_ingress._surface_ref is slack_surface_ref
    assert slack_ingress._message_ref is slack_message_ref
    assert slack_ingress._thread_ref is slack_thread_ref
    assert slack_ingress._outbound_button_external_id is slack_outbound_button_external_id
