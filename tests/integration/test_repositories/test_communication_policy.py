"""Shared communication policy coverage."""

from __future__ import annotations

from sqlmodel import Session

from stackos.communications import (
    CommunicationInteractionCheck,
    CommunicationPolicyEvent,
    CommunicationPolicyProfile,
    evaluate_inbound_policy,
    telegram_callback_button_external_id,
)
from stackos.repositories.resources import ResourceRepository


def test_policy_observes_visible_channel_but_blocks_unapproved_actor(
    session: Session,
    project_id: int,
) -> None:
    profile = _profile()
    decision = evaluate_inbound_policy(
        session,
        project_id=project_id,
        profile=profile,
        event=_event(
            text="<@U_BOT> investigate",
            user_candidate_refs=("slack-user:U222",),
            surface_candidate_refs=("slack-channel:C-any",),
            mention_literals=("<@U_BOT>",),
        ),
    )

    assert decision.store is True
    assert decision.create_request is False
    assert decision.status == "invoker_blocked"


def test_policy_allows_approved_actor_in_any_visible_channel(
    session: Session,
    project_id: int,
) -> None:
    profile = _profile(
        access_policy={
            "channel_mode": "allowlist",
            "allowed_channel_refs": ["slack-channel:C-other"],
            "user_mode": "allowlist",
            "allowed_user_refs": ["slack-user:U111"],
        }
    )
    decision = evaluate_inbound_policy(
        session,
        project_id=project_id,
        profile=profile,
        event=_event(
            text="<@U_BOT> investigate",
            user_candidate_refs=("slack-user:U111",),
            surface_candidate_refs=("slack-channel:C-any",),
            mention_literals=("<@U_BOT>",),
        ),
    )

    assert decision.store is True
    assert decision.create_request is True
    assert decision.status == "request_created"
    assert decision.trigger_reason == "mention"


def test_policy_matches_command_suffix_for_platform_bots(
    session: Session,
    project_id: int,
) -> None:
    profile = _profile(
        trigger_policy={
            "commands": [
                {
                    "command": "/support",
                    "aliases": ["/help"],
                    "guidance": "Triage safely.",
                }
            ]
        }
    )
    decision = evaluate_inbound_policy(
        session,
        project_id=project_id,
        profile=profile,
        event=_event(
            text="/support@support_bot urgent",
            user_candidate_refs=("telegram-user:555",),
            surface_candidate_refs=("telegram-chat:999",),
            command_suffixes=("support_bot",),
            surface_id_prefix="telegram-chat",
            user_id_prefix="telegram-user",
        ),
    )

    assert decision.create_request is True
    assert decision.trigger_reason == "command"
    assert decision.matched_command["command"] == "/support"


def test_policy_enforces_button_actor_scope(
    session: Session,
    project_id: int,
) -> None:
    button_external_id = telegram_callback_button_external_id(
        profile_key="support",
        message_ref="message",
        callback_data="ixn_1",
    )
    ResourceRepository(session).upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-interaction",
        external_id=button_external_id,
        title="Review",
        data_json={
            "interaction_type": "outbound_inline_button",
            "allowed_user_refs": ["telegram-user:555"],
            "allowed_chat_refs": ["telegram-chat:999"],
        },
        provenance_json={"source": "test"},
    )

    blocked = evaluate_inbound_policy(
        session,
        project_id=project_id,
        profile=_profile(access_policy={"user_mode": "all"}),
        event=_event(
            user_candidate_refs=("telegram-user:777",),
            surface_candidate_refs=("telegram-chat:999",),
            interaction=CommunicationInteractionCheck(
                external_id=button_external_id,
                trigger_reason="callback",
                blocked_status="callback_blocked",
            ),
            surface_id_prefix="telegram-chat",
            user_id_prefix="telegram-user",
        ),
    )
    allowed = evaluate_inbound_policy(
        session,
        project_id=project_id,
        profile=_profile(access_policy={"user_mode": "all"}),
        event=_event(
            user_candidate_refs=("telegram-user:555",),
            surface_candidate_refs=("telegram-chat:999",),
            interaction=CommunicationInteractionCheck(
                external_id=button_external_id,
                trigger_reason="callback",
                blocked_status="callback_blocked",
            ),
            surface_id_prefix="telegram-chat",
            user_id_prefix="telegram-user",
        ),
    )

    assert blocked.store is True
    assert blocked.create_request is False
    assert blocked.status == "callback_blocked"
    assert allowed.create_request is True
    assert allowed.trigger_reason == "callback"


def test_policy_uses_interaction_surface_ref_as_default_scope(
    session: Session,
    project_id: int,
) -> None:
    ResourceRepository(session).upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-interaction",
        external_id="slack-button:support:1",
        title="Approve",
        data_json={
            "interaction_type": "outbound_block_button",
            "surface_ref": "slack-channel:C123",
        },
        provenance_json={"source": "test"},
    )

    blocked = evaluate_inbound_policy(
        session,
        project_id=project_id,
        profile=_profile(access_policy={"user_mode": "all"}),
        event=_event(
            user_candidate_refs=("slack-user:U111",),
            surface_candidate_refs=("slack-channel:C999",),
            interaction=CommunicationInteractionCheck(
                external_id="slack-button:support:1",
                trigger_reason="interaction",
                blocked_status="interaction_blocked",
            ),
        ),
    )
    allowed = evaluate_inbound_policy(
        session,
        project_id=project_id,
        profile=_profile(access_policy={"user_mode": "all"}),
        event=_event(
            user_candidate_refs=("slack-user:U111",),
            surface_candidate_refs=("slack-channel:C123",),
            interaction=CommunicationInteractionCheck(
                external_id="slack-button:support:1",
                trigger_reason="interaction",
                blocked_status="interaction_blocked",
            ),
        ),
    )

    assert blocked.status == "interaction_blocked"
    assert blocked.create_request is False
    assert allowed.create_request is True


def _profile(
    *,
    access_policy: dict | None = None,
    trigger_policy: dict | None = None,
    visibility_policy: dict | None = None,
) -> CommunicationPolicyProfile:
    return CommunicationPolicyProfile(
        provider_key="mock-chat",
        profile_key="support",
        disabled_status="profile_disabled",
        store_non_trigger_default=True,
        data={
            "enabled": True,
            "access_policy": access_policy
            or {
                "user_mode": "allowlist",
                "allowed_user_refs": ["slack-user:U111", "telegram-user:555"],
            },
            "trigger_policy": trigger_policy or {},
            "visibility_policy": visibility_policy or {},
        },
    )


def _event(
    *,
    text: str = "",
    user_candidate_refs: tuple[str, ...],
    surface_candidate_refs: tuple[str, ...],
    mention_literals: tuple[str, ...] = (),
    command_suffixes: tuple[str, ...] = (),
    interaction: CommunicationInteractionCheck | None = None,
    surface_id_prefix: str = "slack-channel",
    user_id_prefix: str = "slack-user",
) -> CommunicationPolicyEvent:
    return CommunicationPolicyEvent(
        update_type="message",
        event_type="message",
        text=text,
        surface_candidate_refs=surface_candidate_refs,
        user_candidate_refs=user_candidate_refs,
        user_allowed_keys=("allowed_user_refs", "allowed_user_ids", "allowed_users"),
        user_denied_keys=("denied_user_refs", "denied_user_ids", "denied_users"),
        surface_id_prefix=surface_id_prefix,
        user_id_prefix=user_id_prefix,
        mention_literals=mention_literals,
        command_suffixes=command_suffixes,
        interaction=interaction,
    )
