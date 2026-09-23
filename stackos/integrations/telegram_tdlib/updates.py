"""Normalize safe TDLib receive updates into shared communications state.

The TDLib session owner calls :func:`process_telegram_update` for one ordered
native update.  This adapter intentionally has no transport policy: it resolves
the Account's attached communication profiles, reduces TDLib objects to safe
communication facts, and delegates visibility, trigger matching, allowlists,
dedupe, and agent-request creation to the shared processor.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session, select

from stackos.actions.telegram_schema import telegram_file_ref
from stackos.artifacts import redact_secret_text
from stackos.communications import (
    CommunicationInteractionCheck,
    CommunicationPolicyEvent,
    CommunicationPolicyProfile,
    CommunicationProcessingResult,
    NormalizedInboundEvent,
    NormalizedResourcePatch,
    NormalizedResourceWrite,
    candidate_refs,
    communication_profile_account_uses,
    communication_profile_record_by_key,
    communication_record_by_external_id,
    communication_surface_binding_external_id,
    config_policy,
    evaluate_inbound_policy,
    process_inbound_event,
    telegram_callback_button_external_id,
)
from stackos.db.models import Credential, CredentialAccount

_PROVIDER_KEY = "telegram"
_SOURCE = "telegram-tdlib"
_MAX_PREVIEW = 4_000


@dataclass(frozen=True)
class _TelegramProfile:
    project_id: int
    key: str
    profile_ref: str
    credential_ref: str
    data: dict[str, Any]
    account_identity: _TelegramAccountIdentity


@dataclass(frozen=True)
class _TelegramAccountIdentity:
    """Verified native Account identity; profiles never duplicate this fact."""

    user_id: int | None
    username: str | None


@dataclass(frozen=True)
class _TelegramUpdate:
    update_type: str
    event_type: str
    source_kind: str
    event_key: str
    chat_id: int | None = None
    message_id: int | None = None
    message_thread_id: int | None = None
    sender_user_id: int | None = None
    sender_chat_id: int | None = None
    sender_username: str | None = None
    sender_name: str | None = None
    chat_title: str | None = None
    chat_kind: str | None = None
    chat_permissions: dict[str, bool] | None = None
    text_preview: str = ""
    content_type: str | None = None
    attachments: tuple[dict[str, Any], ...] = ()
    is_outgoing: bool = False
    is_new_message: bool = False
    is_message_content_update: bool = False
    is_message_edited: bool = False
    edit_date: int | None = None
    deleted_message_ids: tuple[int, ...] = ()
    delete_permanent: bool | None = None
    callback_query_id: int | None = None
    callback_data_digest: str | None = None
    callback_data: str | None = None
    membership_ref: str | None = None
    membership_status: str | None = None
    membership_roles: tuple[str, ...] = ()
    membership_permissions: dict[str, Any] | None = None
    file_facts: dict[str, Any] | None = None


def process_telegram_update(
    session: Session,
    *,
    credential_ref: str,
    update: Mapping[str, Any],
) -> list[CommunicationProcessingResult]:
    """Process one native TDLib update for all ready attached profile facets.

    The caller only receives safe processor receipts.  TDLib session paths,
    configuration, authorization values, and raw provider payloads are never
    persisted or returned from this boundary.
    """

    if not isinstance(credential_ref, str) or not credential_ref.strip():
        return []
    parsed = _parse_update(update)
    if parsed is None:
        return []
    return [
        _process_for_profile(session, profile=profile, parsed=parsed)
        for profile in _telegram_profiles(session, credential_ref=credential_ref.strip())
    ]


def _telegram_profiles(session: Session, *, credential_ref: str) -> list[_TelegramProfile]:
    """Resolve only enabled, attached, connected project Telegram facets."""

    profiles: list[_TelegramProfile] = []
    account_identity = _telegram_account_identity(session, credential_ref=credential_ref)
    for use in communication_profile_account_uses(session):
        if (
            use.get("provider_key") != _PROVIDER_KEY
            or use.get("credential_ref") != credential_ref
            or use.get("profile_enabled") is not True
            or use.get("binding_state") != "ready"
        ):
            continue
        project_id = int(use["project_id"])
        profile_key = str(use.get("profile_key") or "").strip()
        profile_ref = str(use.get("profile_ref") or "").strip()
        if not profile_key or not profile_ref:
            continue
        record = communication_profile_record_by_key(
            session,
            project_id=project_id,
            key=profile_key,
        )
        if record is None:
            continue
        data = dict(record.data_json or {})
        facets = data.get("provider_facets")
        facet = facets.get(_PROVIDER_KEY) if isinstance(facets, Mapping) else None
        if not isinstance(facet, Mapping):
            continue
        if facet.get("enabled") is False:
            continue
        if str(facet.get("credential_ref") or "").strip() != credential_ref:
            continue
        profiles.append(
            _TelegramProfile(
                project_id=project_id,
                key=profile_key,
                profile_ref=profile_ref,
                credential_ref=credential_ref,
                data=data,
                account_identity=account_identity,
            )
        )
    return profiles


def _telegram_account_identity(
    session: Session, *, credential_ref: str
) -> _TelegramAccountIdentity:
    """Read only Account-test evidence; a profile must not author identity facts."""

    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).first()
    if credential is None or credential.id is None:
        return _TelegramAccountIdentity(user_id=None, username=None)
    account = session.exec(
        select(CredentialAccount).where(CredentialAccount.credential_id == credential.id)
    ).first()
    if account is None:
        return _TelegramAccountIdentity(user_id=None, username=None)
    metadata = account.metadata_json if isinstance(account.metadata_json, Mapping) else {}
    return _TelegramAccountIdentity(
        user_id=_as_int(account.provider_account_id),
        username=_safe_identifier(metadata.get("username")),
    )


def _process_for_profile(
    session: Session,
    *,
    profile: _TelegramProfile,
    parsed: _TelegramUpdate,
) -> CommunicationProcessingResult:
    policy_event = _policy_event(profile, parsed)
    decision = evaluate_inbound_policy(
        session,
        project_id=profile.project_id,
        profile=CommunicationPolicyProfile(
            provider_key=_PROVIDER_KEY,
            profile_key=profile.key,
            data=profile.data,
            disabled_status="profile_disabled",
            store_non_trigger_default=True,
            visibility_blocked_status="surface_blocked",
            visibility_default_mode="allowlist",
            require_selected_update_types=True,
        ),
        event=policy_event,
    )
    return process_inbound_event(
        session,
        project_id=profile.project_id,
        event=_normalized_event(
            session,
            profile=profile,
            parsed=parsed,
            mark_interaction_clicked=decision.create_request,
        ),
        decision=decision,
    )


def _policy_event(
    profile: _TelegramProfile,
    parsed: _TelegramUpdate,
) -> CommunicationPolicyEvent:
    surface_ref = _surface_ref(parsed.chat_id)
    is_message_activation = parsed.is_new_message
    is_callback = parsed.callback_query_id is not None
    self_user_id = profile.account_identity.user_id
    is_self = parsed.is_outgoing or (
        parsed.sender_user_id is not None and parsed.sender_user_id == self_user_id
    )
    direct = (
        parsed.chat_id is not None and parsed.chat_id > 0 and (is_message_activation or is_callback)
    )
    username = profile.account_identity.username
    return CommunicationPolicyEvent(
        update_type=parsed.update_type,
        event_type=parsed.event_type,
        text=parsed.text_preview if is_message_activation else "",
        is_direct=direct,
        visibility_mode_keys=(
            ()
            if parsed.chat_id is None
            else ("dm_mode",)
            if parsed.chat_id > 0
            else ("channel_mode", "group_mode")
        ),
        visibility_allowed_keys=(
            "allowed_surface_refs",
            "allowed_channel_refs",
            "allowed_chat_refs",
            "allowed_channel_ids",
            "allowed_chat_ids",
            "allowed_channels",
            "allowed_chats",
        ),
        visibility_denied_keys=(
            "denied_surface_refs",
            "denied_channel_refs",
            "denied_chat_refs",
            "denied_channel_ids",
            "denied_chat_ids",
            "denied_channels",
            "denied_chats",
        ),
        surface_candidate_refs=candidate_refs(surface_ref, parsed.chat_id, "telegram-chat"),
        user_candidate_refs=candidate_refs(
            _user_ref(parsed.sender_user_id),
            parsed.sender_user_id,
            "telegram-user",
        ),
        user_allowed_keys=(
            "allowed_user_refs",
            "allowed_user_ids",
            "allowed_usernames",
            "allowed_users",
        ),
        user_denied_keys=(
            "denied_user_refs",
            "denied_user_ids",
            "denied_usernames",
            "denied_users",
        ),
        surface_id_prefix="telegram-chat",
        user_id_prefix="telegram-user",
        username_prefix="telegram-username",
        group_trigger_keys=("channel_trigger", "group_trigger"),
        group_always_reason="channel_always",
        command_suffixes=(username,) if username else (),
        mention_literals=(f"@{username}",) if username else (),
        is_self=is_self,
        interaction=_interaction_check(profile, parsed),
    )


def _interaction_check(
    profile: _TelegramProfile,
    parsed: _TelegramUpdate,
) -> CommunicationInteractionCheck | None:
    if parsed.callback_query_id is None:
        return None
    external_id = None
    message_ref = _message_ref(parsed.chat_id, parsed.message_id)
    if message_ref and parsed.callback_data is not None:
        external_id = telegram_callback_button_external_id(
            profile_key=profile.key,
            message_ref=message_ref,
            callback_data=parsed.callback_data,
        )
    trigger = config_policy(profile.data, "trigger_policy")
    return CommunicationInteractionCheck(
        external_id=external_id,
        trigger_reason="interaction",
        blocked_status="interaction_blocked",
        allow_unknown=trigger.get("allow_unknown_interactions") is True,
    )


def _normalized_event(
    session: Session,
    *,
    profile: _TelegramProfile,
    parsed: _TelegramUpdate,
    mark_interaction_clicked: bool,
) -> NormalizedInboundEvent:
    message_ref = _message_ref(parsed.chat_id, parsed.message_id)
    interaction_ref = _interaction_ref(parsed.callback_query_id)
    return NormalizedInboundEvent(
        provider_key=_PROVIDER_KEY,
        profile_key=profile.key,
        event_key=parsed.event_key,
        update_type=parsed.update_type,
        source_kind=parsed.source_kind,
        request_key=_request_key(
            profile,
            parsed,
            message_ref=message_ref,
            interaction_ref=interaction_ref,
        ),
        request_title=_request_title(parsed),
        body_preview=parsed.text_preview,
        source_message_ref=message_ref,
        surface=_surface_write(session, profile=profile, parsed=parsed),
        event=NormalizedResourceWrite(
            resource_key="communication-event",
            external_id=f"telegram-event:{profile.key}:{parsed.event_key}",
            title=f"Telegram {parsed.event_type}",
            data_json={
                "provider_key": _PROVIDER_KEY,
                "profile_key": profile.key,
                "profile_ref": profile.profile_ref,
                "credential_ref": profile.credential_ref,
                "event_key": parsed.event_key,
                "update_type": parsed.update_type,
                "event_type": parsed.event_type,
                "surface_ref": _surface_ref(parsed.chat_id),
                "thread_ref": _thread_ref(parsed.chat_id, parsed.message_thread_id),
                "message_ref": message_ref,
                "interaction_ref": interaction_ref,
                "file": _qualified_file_facts(parsed.file_facts, profile.credential_ref),
            },
            provenance_json={"source": _SOURCE},
            preserve_existing_on_dedupe=True,
        ),
        message=_message_write(session, profile=profile, parsed=parsed),
        interaction=_interaction_write(profile, parsed),
        related_resources=_related_writes(session, profile=profile, parsed=parsed),
        state_patches=(
            [_callback_click_patch(profile, parsed)]
            if mark_interaction_clicked and parsed.callback_query_id is not None
            else []
        ),
        request_metadata_json={
            "profile_key": profile.key,
            "profile_ref": profile.profile_ref,
            "credential_ref": profile.credential_ref,
            "interaction_ref": interaction_ref,
            "invoker_ref": _user_ref(parsed.sender_user_id),
            "surface_ref": _surface_ref(parsed.chat_id),
            "channel_ref": _surface_ref(parsed.chat_id),
            "thread_ref": _thread_ref(parsed.chat_id, parsed.message_thread_id),
            "identity": profile.data.get("identity"),
            "agent_guidance": profile.data.get("agent_guidance"),
            "context_policy": profile.data.get("context_policy"),
            "response_policy": profile.data.get("response_policy"),
        },
        response_json={
            "profile_key": profile.key,
            "event_key": parsed.event_key,
            "update_type": parsed.update_type,
        },
    )


def _surface_write(
    session: Session,
    *,
    profile: _TelegramProfile,
    parsed: _TelegramUpdate,
) -> NormalizedResourceWrite | None:
    surface_ref = _surface_ref(parsed.chat_id)
    if surface_ref is None:
        return None
    external_id = communication_surface_binding_external_id(
        provider_key=_PROVIDER_KEY,
        profile_ref=profile.profile_ref,
        surface_ref=surface_ref,
    )
    existing = communication_record_by_external_id(
        session,
        project_id=profile.project_id,
        resource_key="communication-channel",
        external_id=external_id,
    )
    data = dict(existing.data_json or {}) if existing is not None else {}
    display_name = parsed.chat_title or str(data.get("display_name") or surface_ref)
    data.update(
        {
            "provider_key": _PROVIDER_KEY,
            "profile_key": profile.key,
            "profile_ref": profile.profile_ref,
            "credential_ref": profile.credential_ref,
            "surface_ref": surface_ref,
            "channel_ref": surface_ref,
            "provider_chat_id": parsed.chat_id,
            "kind": parsed.chat_kind or data.get("kind") or _chat_kind(parsed.chat_id),
            "display_name": display_name,
            "safe_external_ref": surface_ref,
            "ingest_enabled": True,
            "send_enabled": bool(data.get("send_enabled", True)),
            "capabilities": dict(data.get("capabilities") or {"can_read": True}),
        }
    )
    if parsed.chat_permissions:
        data["metadata_json"] = {
            **dict(data.get("metadata_json") or {}),
            "telegram_default_permissions": parsed.chat_permissions,
        }
    return NormalizedResourceWrite(
        resource_key="communication-channel",
        external_id=external_id,
        title=display_name,
        data_json=data,
        provenance_json={"source": _SOURCE},
    )


def _message_write(
    session: Session,
    *,
    profile: _TelegramProfile,
    parsed: _TelegramUpdate,
) -> NormalizedResourceWrite | None:
    if not (parsed.is_new_message or parsed.is_message_content_update or parsed.is_message_edited):
        return None
    message_ref = _message_ref(parsed.chat_id, parsed.message_id)
    if message_ref is None:
        return None
    if parsed.is_new_message and (
        parsed.is_outgoing
        or (
            parsed.sender_user_id is not None
            and parsed.sender_user_id == profile.account_identity.user_id
        )
    ):
        return None
    external_id = f"telegram-message:{profile.key}:{parsed.chat_id}:{parsed.message_id}"
    existing = communication_record_by_external_id(
        session,
        project_id=profile.project_id,
        resource_key="communication-message",
        external_id=external_id,
    )
    data = dict(existing.data_json or {}) if existing is not None else {}
    existing_thread_ref = data.get("thread_ref")
    existing_from_ref = data.get("from_ref")
    has_new_content = parsed.is_new_message or parsed.is_message_content_update
    data.update(
        {
            "provider_key": _PROVIDER_KEY,
            "profile_key": profile.key,
            "profile_ref": profile.profile_ref,
            "credential_ref": profile.credential_ref,
            "direction": data.get("direction") or "inbound",
            "surface_ref": _surface_ref(parsed.chat_id),
            "channel_ref": _surface_ref(parsed.chat_id),
            "thread_ref": _thread_ref(parsed.chat_id, parsed.message_thread_id)
            or existing_thread_ref,
            "message_ref": message_ref,
            "provider_message_id": parsed.message_id,
            "content_type": (
                parsed.content_type if has_new_content else data.get("content_type") or "unknown"
            ),
            "text_preview": parsed.text_preview
            if has_new_content
            else data.get("text_preview") or "",
            "attachments": _qualified_attachments(parsed.attachments, profile.credential_ref)
            if has_new_content
            else data.get("attachments") or [],
            "transport_status": "received",
            "attention_status": data.get("attention_status") or "unread",
            "from_ref": _user_ref(parsed.sender_user_id) or existing_from_ref,
        }
    )
    if parsed.is_message_edited:
        data["edited_at"] = parsed.edit_date
    return NormalizedResourceWrite(
        resource_key="communication-message",
        external_id=external_id,
        title=_request_title(parsed),
        data_json=data,
        provenance_json={"source": _SOURCE},
        preserve_existing_on_dedupe=parsed.is_new_message,
    )


def _interaction_write(
    profile: _TelegramProfile,
    parsed: _TelegramUpdate,
) -> NormalizedResourceWrite | None:
    interaction_ref = _interaction_ref(parsed.callback_query_id)
    if interaction_ref is None:
        return None
    return NormalizedResourceWrite(
        resource_key="communication-interaction",
        external_id=f"telegram-callback:{profile.key}:{parsed.callback_query_id}",
        title="Telegram callback",
        data_json={
            "provider_key": _PROVIDER_KEY,
            "profile_key": profile.key,
            "profile_ref": profile.profile_ref,
            "credential_ref": profile.credential_ref,
            "interaction_ref": interaction_ref,
            "interaction_type": "callback_query",
            "surface_ref": _surface_ref(parsed.chat_id),
            "channel_ref": _surface_ref(parsed.chat_id),
            "message_ref": _message_ref(parsed.chat_id, parsed.message_id),
            "callback_data_digest": parsed.callback_data_digest,
            "from_ref": _user_ref(parsed.sender_user_id),
        },
        provenance_json={"source": _SOURCE},
        preserve_existing_on_dedupe=True,
    )


def _related_writes(
    session: Session,
    *,
    profile: _TelegramProfile,
    parsed: _TelegramUpdate,
) -> tuple[NormalizedResourceWrite, ...]:
    writes: list[NormalizedResourceWrite] = []
    contact = _contact_write(session, profile=profile, parsed=parsed)
    if contact is not None:
        writes.append(contact)
    thread = _thread_write(session, profile=profile, parsed=parsed)
    if thread is not None:
        writes.append(thread)
    membership = _membership_write(session, profile=profile, parsed=parsed)
    if membership is not None:
        writes.append(membership)
    writes.extend(_deleted_message_writes(session, profile=profile, parsed=parsed))
    return tuple(writes)


def _contact_write(
    session: Session,
    *,
    profile: _TelegramProfile,
    parsed: _TelegramUpdate,
) -> NormalizedResourceWrite | None:
    user_id = parsed.sender_user_id
    if user_id is None:
        return None
    user_ref = _user_ref(user_id)
    assert user_ref is not None
    external_id = f"communication-contact:telegram:{user_id}"
    existing = communication_record_by_external_id(
        session,
        project_id=profile.project_id,
        resource_key="communication-contact",
        external_id=external_id,
    )
    data = dict(existing.data_json or {}) if existing is not None else {}
    display_name = parsed.sender_name or str(data.get("display_name") or user_ref)
    provider_refs = dict(data.get("provider_refs") or {})
    provider_refs[_PROVIDER_KEY] = sorted(set([*provider_refs.get(_PROVIDER_KEY, []), user_ref]))
    safe_external_refs = [str(value) for value in data.get("safe_external_refs") or []]
    if user_ref not in safe_external_refs:
        safe_external_refs.append(user_ref)
    data.update(
        {
            "contact_ref": external_id,
            "key": f"telegram-{user_id}",
            "display_name": display_name,
            "kind": "person",
            "status": "active",
            "provider_refs": provider_refs,
            "safe_external_refs": safe_external_refs,
            "metadata_json": {
                **dict(data.get("metadata_json") or {}),
                "telegram_username": parsed.sender_username,
            },
        }
    )
    return NormalizedResourceWrite(
        resource_key="communication-contact",
        external_id=external_id,
        title=display_name,
        data_json=data,
        provenance_json={"source": _SOURCE},
    )


def _thread_write(
    session: Session,
    *,
    profile: _TelegramProfile,
    parsed: _TelegramUpdate,
) -> NormalizedResourceWrite | None:
    thread_ref = _thread_ref(parsed.chat_id, parsed.message_thread_id)
    if thread_ref is None:
        return None
    external_id = f"telegram-thread:{profile.key}:{parsed.chat_id}:{parsed.message_thread_id}"
    existing = communication_record_by_external_id(
        session,
        project_id=profile.project_id,
        resource_key="communication-thread",
        external_id=external_id,
    )
    data = dict(existing.data_json or {}) if existing is not None else {}
    data.update(
        {
            "provider_key": _PROVIDER_KEY,
            "profile_key": profile.key,
            "profile_ref": profile.profile_ref,
            "credential_ref": profile.credential_ref,
            "surface_ref": _surface_ref(parsed.chat_id),
            "channel_ref": _surface_ref(parsed.chat_id),
            "thread_ref": thread_ref,
            "provider_thread_id": parsed.message_thread_id,
            "kind": "telegram-topic",
        }
    )
    return NormalizedResourceWrite(
        resource_key="communication-thread",
        external_id=external_id,
        title=str(data.get("title") or thread_ref),
        data_json=data,
        provenance_json={"source": _SOURCE},
    )


def _membership_write(
    session: Session,
    *,
    profile: _TelegramProfile,
    parsed: _TelegramUpdate,
) -> NormalizedResourceWrite | None:
    if parsed.membership_ref is None or parsed.chat_id is None:
        return None
    surface_ref = _surface_ref(parsed.chat_id)
    assert surface_ref is not None
    external_id = f"telegram-membership:{profile.key}:{parsed.chat_id}:{parsed.membership_ref}"
    existing = communication_record_by_external_id(
        session,
        project_id=profile.project_id,
        resource_key="communication-membership",
        external_id=external_id,
    )
    data = dict(existing.data_json or {}) if existing is not None else {}
    data.update(
        {
            "membership_ref": f"communication-membership:{surface_ref}:{parsed.membership_ref}",
            "surface_ref": surface_ref,
            "member_ref": parsed.membership_ref,
            "provider_key": _PROVIDER_KEY,
            "profile_ref": profile.profile_ref,
            "membership_kind": (
                "user" if parsed.membership_ref.startswith("telegram-user:") else "external"
            ),
            "status": parsed.membership_status or "unknown",
            "roles": list(parsed.membership_roles),
            "permissions": dict(parsed.membership_permissions or {}),
            "scope_status": {},
            "metadata_json": {"source_profile_ref": profile.profile_ref},
        }
    )
    return NormalizedResourceWrite(
        resource_key="communication-membership",
        external_id=external_id,
        title=f"{parsed.membership_ref} in {surface_ref}",
        data_json=data,
        provenance_json={"source": _SOURCE},
    )


def _deleted_message_writes(
    session: Session,
    *,
    profile: _TelegramProfile,
    parsed: _TelegramUpdate,
) -> list[NormalizedResourceWrite]:
    if parsed.chat_id is None:
        return []
    writes: list[NormalizedResourceWrite] = []
    for message_id in parsed.deleted_message_ids:
        message_ref = _message_ref(parsed.chat_id, message_id)
        assert message_ref is not None
        external_id = f"telegram-message:{profile.key}:{parsed.chat_id}:{message_id}"
        existing = communication_record_by_external_id(
            session,
            project_id=profile.project_id,
            resource_key="communication-message",
            external_id=external_id,
        )
        data = dict(existing.data_json or {}) if existing is not None else {}
        data.update(
            {
                "provider_key": _PROVIDER_KEY,
                "profile_key": profile.key,
                "profile_ref": profile.profile_ref,
                "credential_ref": profile.credential_ref,
                "direction": data.get("direction") or "inbound",
                "surface_ref": _surface_ref(parsed.chat_id),
                "channel_ref": _surface_ref(parsed.chat_id),
                "message_ref": message_ref,
                "provider_message_id": message_id,
                "transport_status": "deleted",
                "delete_permanent": parsed.delete_permanent,
            }
        )
        writes.append(
            NormalizedResourceWrite(
                resource_key="communication-message",
                external_id=external_id,
                title=str(data.get("title") or f"Telegram message {message_id}"),
                data_json=data,
                provenance_json={"source": _SOURCE},
            )
        )
    return writes


def _callback_click_patch(
    profile: _TelegramProfile,
    parsed: _TelegramUpdate,
) -> NormalizedResourcePatch:
    message_ref = _message_ref(parsed.chat_id, parsed.message_id)
    assert message_ref is not None
    assert parsed.callback_data is not None
    return NormalizedResourcePatch(
        resource_key="communication-interaction",
        external_id=telegram_callback_button_external_id(
            profile_key=profile.key,
            message_ref=message_ref,
            callback_data=parsed.callback_data,
        ),
        data_json={
            "status": "clicked",
            "last_clicked_by_ref": _user_ref(parsed.sender_user_id),
            "last_callback_ref": _interaction_ref(parsed.callback_query_id),
        },
    )


def _request_key(
    profile: _TelegramProfile,
    parsed: _TelegramUpdate,
    *,
    message_ref: str | None,
    interaction_ref: str | None,
) -> str | None:
    if parsed.is_new_message and message_ref is not None:
        return f"telegram-message-trigger:{profile.key}:{message_ref}"
    if parsed.callback_query_id is not None and interaction_ref is not None:
        return f"telegram-interaction:{profile.key}:{interaction_ref}"
    return None


def _request_title(parsed: _TelegramUpdate) -> str:
    if parsed.callback_query_id is not None:
        return "Telegram callback"
    if parsed.is_new_message:
        return (
            f"Telegram message {parsed.message_id}"
            if parsed.message_id is not None
            else "Telegram message"
        )
    return f"Telegram {parsed.event_type}"


def _parse_update(update: Mapping[str, Any]) -> _TelegramUpdate | None:
    if not isinstance(update, Mapping):
        return None
    update_type = _safe_type(update.get("@type"))
    if update_type == "updateNewMessage":
        return _parse_new_message(update)
    if update_type == "updateMessageContent":
        return _parse_message_content(update)
    if update_type == "updateMessageEdited":
        return _parse_message_edited(update)
    if update_type == "updateDeleteMessages":
        return _parse_deleted_messages(update)
    if update_type == "updateNewCallbackQuery":
        return _parse_callback(update)
    if update_type in {
        "updateNewChat",
        "updateChatTitle",
        "updateChatPhoto",
        "updateChatPermissions",
        "updateChatPosition",
        "updateChatLastMessage",
    }:
        return _parse_chat_update(update, update_type=update_type)
    if update_type == "updateChatMember":
        return _parse_membership(update)
    if update_type == "updateUser":
        return _parse_user(update)
    if update_type == "updateFile":
        return _parse_file(update)
    return None


def _parse_new_message(update: Mapping[str, Any]) -> _TelegramUpdate | None:
    message = _mapping(update.get("message"))
    chat_id = _as_int(message.get("chat_id"))
    message_id = _as_int(message.get("id"))
    if chat_id is None or message_id is None:
        return None
    sender_user_id, sender_chat_id = _sender_ids(message.get("sender_id"))
    content_type, text_preview, attachments = _content_facts(message.get("content"))
    return _TelegramUpdate(
        update_type="updateNewMessage",
        event_type="message",
        source_kind="telegram-message",
        event_key=f"new-message:{chat_id}:{message_id}",
        chat_id=chat_id,
        message_id=message_id,
        message_thread_id=_nonzero_int(message.get("message_thread_id")),
        sender_user_id=sender_user_id,
        sender_chat_id=sender_chat_id,
        text_preview=text_preview,
        content_type=content_type,
        attachments=attachments,
        is_outgoing=message.get("is_outgoing") is True,
        is_new_message=True,
    )


def _parse_message_content(update: Mapping[str, Any]) -> _TelegramUpdate | None:
    chat_id = _as_int(update.get("chat_id"))
    message_id = _as_int(update.get("message_id"))
    if chat_id is None or message_id is None:
        return None
    content_type, text_preview, attachments = _content_facts(update.get("new_content"))
    return _TelegramUpdate(
        update_type="updateMessageContent",
        event_type="message_content_updated",
        source_kind="telegram-message-edit",
        event_key=(
            f"message-content:{chat_id}:{message_id}:"
            f"{_digest({'type': content_type, 'text': text_preview, 'attachments': attachments})}"
        ),
        chat_id=chat_id,
        message_id=message_id,
        text_preview=text_preview,
        content_type=content_type,
        attachments=attachments,
        is_message_content_update=True,
    )


def _parse_message_edited(update: Mapping[str, Any]) -> _TelegramUpdate | None:
    chat_id = _as_int(update.get("chat_id"))
    message_id = _as_int(update.get("message_id"))
    if chat_id is None or message_id is None:
        return None
    edit_date = _as_int(update.get("edit_date"))
    return _TelegramUpdate(
        update_type="updateMessageEdited",
        event_type="message_edited",
        source_kind="telegram-message-edit",
        event_key=f"message-edited:{chat_id}:{message_id}:{edit_date or 0}",
        chat_id=chat_id,
        message_id=message_id,
        is_message_edited=True,
        edit_date=edit_date,
    )


def _parse_deleted_messages(update: Mapping[str, Any]) -> _TelegramUpdate | None:
    chat_id = _as_int(update.get("chat_id"))
    raw_message_ids = update.get("message_ids")
    if chat_id is None or not isinstance(raw_message_ids, list):
        return None
    message_ids = tuple(
        sorted({value for raw in raw_message_ids if (value := _as_int(raw)) is not None})
    )
    if not message_ids:
        return None
    permanent = update.get("is_permanent")
    delete_facts = {
        "ids": message_ids,
        "permanent": permanent is True,
        "from_cache": update.get("from_cache") is True,
    }
    return _TelegramUpdate(
        update_type="updateDeleteMessages",
        event_type="message_deleted",
        source_kind="telegram-message-delete",
        event_key=f"message-delete:{chat_id}:{_digest(delete_facts)}",
        chat_id=chat_id,
        deleted_message_ids=message_ids,
        delete_permanent=permanent if isinstance(permanent, bool) else None,
    )


def _parse_callback(update: Mapping[str, Any]) -> _TelegramUpdate | None:
    # TDLib JSON serializes int64 as decimal strings and bytes as base64 strings.
    # https://core.telegram.org/tdlib/docs/td__json__client_8h.html
    raw_id = update.get("id")
    if (
        not isinstance(raw_id, str)
        or not 1 <= len(raw_id) <= 19
        or not raw_id.isascii()
        or not raw_id.isdecimal()
        or raw_id[0] == "0"
    ):
        return None
    callback_query_id = int(raw_id)
    if callback_query_id > 2**63 - 1:
        return None
    chat_id = _as_int(update.get("chat_id"))
    message_id = _as_int(update.get("message_id"))
    sender_user_id = _as_int(update.get("sender_user_id"))
    payload = _mapping(update.get("payload"))
    encoded_data = payload.get("data")
    if (
        chat_id is None
        or message_id is None
        or sender_user_id is None
        or payload.get("@type") != "callbackQueryPayloadData"
        or not isinstance(encoded_data, str)
        or not 4 <= len(encoded_data) <= 88
        or not encoded_data.isascii()
    ):
        return None
    try:
        raw_data = base64.b64decode(encoded_data, validate=True)
        if not 1 <= len(raw_data) <= 64:
            return None
        if base64.b64encode(raw_data).decode("ascii") != encoded_data:
            return None
        callback_data = raw_data.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return _TelegramUpdate(
        update_type="updateNewCallbackQuery",
        event_type="callback_query",
        source_kind="telegram-callback",
        event_key=f"callback:{callback_query_id}",
        chat_id=chat_id,
        message_id=message_id,
        sender_user_id=sender_user_id,
        callback_query_id=callback_query_id,
        callback_data=callback_data,
        callback_data_digest=_digest(callback_data),
    )


def _parse_chat_update(update: Mapping[str, Any], *, update_type: str) -> _TelegramUpdate | None:
    chat = _mapping(update.get("chat"))
    chat_id = _as_int(chat.get("id")) or _as_int(update.get("chat_id"))
    if chat_id is None:
        return None
    title = _safe_text(chat.get("title") or update.get("title")) or None
    kind = _chat_kind_from_type(_mapping(chat.get("type"))) or _chat_kind(chat_id)
    permissions = _permission_facts(update.get("permissions") or chat.get("permissions"))
    return _TelegramUpdate(
        update_type=update_type,
        event_type="chat_updated",
        source_kind="telegram-chat",
        event_key=f"chat:{chat_id}:{update_type}:{_digest({'title': title, 'kind': kind})}",
        chat_id=chat_id,
        chat_title=title,
        chat_kind=kind,
        chat_permissions=permissions,
    )


def _parse_membership(update: Mapping[str, Any]) -> _TelegramUpdate | None:
    chat_id = _as_int(update.get("chat_id"))
    new_member = _mapping(update.get("new_chat_member"))
    member = _mapping(new_member.get("member_id"))
    member_ref = _message_sender_ref(member)
    if chat_id is None or member_ref is None:
        return None
    status = _membership_status(_mapping(new_member.get("status")))
    return _TelegramUpdate(
        update_type="updateChatMember",
        event_type="membership_updated",
        source_kind="telegram-membership",
        event_key=f"membership:{chat_id}:{member_ref}:{_digest({'status': status})}",
        chat_id=chat_id,
        membership_ref=member_ref,
        membership_status=status,
        membership_roles=_membership_roles(_mapping(new_member.get("status"))),
        membership_permissions=_permission_facts(new_member.get("status")),
        sender_user_id=_as_int(member.get("user_id")),
    )


def _parse_user(update: Mapping[str, Any]) -> _TelegramUpdate | None:
    user = _mapping(update.get("user"))
    user_id = _as_int(user.get("id"))
    if user_id is None:
        return None
    display_name = " ".join(
        part
        for part in (_safe_text(user.get("first_name")), _safe_text(user.get("last_name")))
        if part
    )
    return _TelegramUpdate(
        update_type="updateUser",
        event_type="user_updated",
        source_kind="telegram-user",
        event_key=(
            f"user:{user_id}:"
            f"{_digest({'name': display_name, 'username': _safe_identifier(user.get('username'))})}"
        ),
        sender_user_id=user_id,
        sender_username=_safe_identifier(user.get("username")),
        sender_name=display_name or None,
    )


def _parse_file(update: Mapping[str, Any]) -> _TelegramUpdate | None:
    facts = _file_facts(update.get("file"))
    file_id = _as_int(facts.get("file_id")) if facts else None
    if file_id is None:
        return None
    return _TelegramUpdate(
        update_type="updateFile",
        event_type="file_updated",
        source_kind="telegram-file",
        event_key=f"file:{file_id}:{_digest(facts)}",
        file_facts=facts,
    )


def _content_facts(content: Any) -> tuple[str, str, tuple[dict[str, Any], ...]]:
    data = _mapping(content)
    raw_type = _safe_type(data.get("@type"))
    content_type = raw_type.removeprefix("message").lower() or "unknown"
    text = _formatted_text(data.get("text")) or _formatted_text(data.get("caption"))
    attachments: list[dict[str, Any]] = []
    for key in (
        "photo",
        "video",
        "animation",
        "audio",
        "voice_note",
        "video_note",
        "document",
        "sticker",
    ):
        facts = _content_file_facts(key, data.get(key))
        if facts:
            attachments.append({"type": key, **facts})
    if not text:
        if raw_type == "messagePoll":
            text = _safe_text(_mapping(data.get("poll")).get("question"))
        elif raw_type == "messageDice":
            text = _safe_text(_mapping(data.get("dice")).get("emoji"))
        elif raw_type:
            text = f"[{content_type}]"
    return content_type, text, tuple(attachments)


def _content_file_facts(content_key: str, value: Any) -> dict[str, Any] | None:
    data = _mapping(value)
    if content_key != "photo":
        return _file_facts(data.get("file"))
    sizes = data.get("sizes")
    if not isinstance(sizes, list):
        return None
    for size in reversed(sizes):
        photo_size = _mapping(size)
        if photo_size.get("type") in {"i", "j"}:
            continue
        facts = _file_facts(photo_size.get("photo"))
        if facts is not None:
            return facts
    return None


def _file_facts(file: Any) -> dict[str, Any] | None:
    data = _mapping(file)
    file_id = _as_int(data.get("id"))
    if file_id is None:
        return None
    local = _mapping(data.get("local"))
    remote = _mapping(data.get("remote"))
    result: dict[str, Any] = {"file_id": file_id}
    for key in ("size", "expected_size"):
        value = _as_int(data.get(key))
        if value is not None:
            result[key] = value
    for key in ("is_downloading_active", "is_downloading_completed", "downloaded_size"):
        value = local.get(key)
        if isinstance(value, bool) or (key == "downloaded_size" and _as_int(value) is not None):
            result[key] = _as_int(value) if key == "downloaded_size" else value
    for key in ("is_uploading_active", "is_uploading_completed", "uploaded_size"):
        value = remote.get(key)
        if isinstance(value, bool) or (key == "uploaded_size" and _as_int(value) is not None):
            result[key] = _as_int(value) if key == "uploaded_size" else value
    return result


def _qualified_file_facts(
    facts: dict[str, Any] | None, credential_ref: str
) -> dict[str, Any] | None:
    """Publish TDLib file facts with only the Account-qualified reusable ref."""

    if facts is None:
        return None
    raw_id = _as_int(facts.get("file_id"))
    if raw_id is None:
        return None
    return {
        **{key: value for key, value in facts.items() if key != "file_id"},
        "file_ref": telegram_file_ref(credential_ref=credential_ref, file_id=raw_id),
    }


def _qualified_attachments(
    attachments: tuple[dict[str, Any], ...], credential_ref: str
) -> list[dict[str, Any]]:
    qualified: list[dict[str, Any]] = []
    for attachment in attachments:
        facts = _qualified_file_facts(attachment, credential_ref)
        if facts is not None:
            qualified.append(facts)
    return qualified


def _sender_ids(value: Any) -> tuple[int | None, int | None]:
    sender = _mapping(value)
    kind = _safe_type(sender.get("@type"))
    if kind == "messageSenderUser":
        return _as_int(sender.get("user_id")), None
    if kind == "messageSenderChat":
        return None, _as_int(sender.get("chat_id"))
    return None, None


def _message_sender_ref(value: Any) -> str | None:
    user_id, chat_id = _sender_ids(value)
    if user_id is not None:
        return _user_ref(user_id)
    if chat_id is not None:
        return _surface_ref(chat_id)
    return None


def _membership_status(status: Mapping[str, Any]) -> str:
    raw_type = _safe_type(status.get("@type"))
    return {
        "chatMemberStatusCreator": "joined",
        "chatMemberStatusAdministrator": "joined",
        "chatMemberStatusMember": "joined",
        "chatMemberStatusRestricted": "joined",
        "chatMemberStatusLeft": "left",
        "chatMemberStatusBanned": "removed",
    }.get(raw_type, "unknown")


def _membership_roles(status: Mapping[str, Any]) -> tuple[str, ...]:
    raw_type = _safe_type(status.get("@type"))
    return {
        "chatMemberStatusCreator": ("owner",),
        "chatMemberStatusAdministrator": ("administrator",),
        "chatMemberStatusMember": ("member",),
        "chatMemberStatusRestricted": ("restricted",),
    }.get(raw_type, ())


def _permission_facts(value: Any) -> dict[str, bool]:
    data = _mapping(value)
    allowed = (
        "can_manage_chat",
        "can_change_info",
        "can_post_messages",
        "can_edit_messages",
        "can_delete_messages",
        "can_invite_users",
        "can_restrict_members",
        "can_pin_messages",
        "can_manage_topics",
        "can_promote_members",
        "can_post_stories",
        "can_edit_stories",
        "can_delete_stories",
        "can_send_basic_messages",
        "can_send_audios",
        "can_send_documents",
        "can_send_photos",
        "can_send_videos",
        "can_send_video_notes",
        "can_send_voice_notes",
        "can_send_polls",
        "can_send_other_messages",
        "can_add_link_previews",
        "can_create_topics",
    )
    return {key: value for key in allowed if isinstance((value := data.get(key)), bool)}


def _chat_kind_from_type(chat_type: Mapping[str, Any]) -> str | None:
    return {
        "chatTypePrivate": "telegram-dm",
        "chatTypeBasicGroup": "telegram-group",
        "chatTypeSupergroup": "telegram-channel",
        "chatTypeSecret": "telegram-dm",
    }.get(_safe_type(chat_type.get("@type")))


def _chat_kind(chat_id: int | None) -> str:
    return "telegram-dm" if chat_id is not None and chat_id > 0 else "telegram-chat"


def _message_ref(chat_id: int | None, message_id: int | None) -> str | None:
    if chat_id is None or message_id is None:
        return None
    return f"telegram-message:{chat_id}:{message_id}"


def _thread_ref(chat_id: int | None, thread_id: int | None) -> str | None:
    if chat_id is None or thread_id is None:
        return None
    return f"telegram-thread:{chat_id}:{thread_id}"


def _surface_ref(chat_id: int | None) -> str | None:
    return f"telegram-chat:{chat_id}" if chat_id is not None else None


def _interaction_ref(callback_query_id: int | None) -> str | None:
    return f"telegram-callback:{callback_query_id}" if callback_query_id is not None else None


def _user_ref(user_id: int | None) -> str | None:
    return f"telegram-user:{user_id}" if user_id is not None else None


def _formatted_text(value: Any) -> str:
    data = _mapping(value)
    return _safe_text(data.get("text"))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _nonzero_int(value: Any) -> int | None:
    parsed = _as_int(value)
    return parsed if parsed not in {None, 0} else None


def _safe_type(value: Any) -> str:
    return value if isinstance(value, str) and value else ""


def _safe_text(value: Any) -> str:
    return redact_secret_text(value)[:_MAX_PREVIEW] if isinstance(value, str) else ""


def _safe_identifier(value: Any) -> str | None:
    text = _safe_text(value).lstrip("@").strip()
    return text or None


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()[:24]


__all__ = ["process_telegram_update"]
