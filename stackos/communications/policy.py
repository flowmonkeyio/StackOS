"""Shared static policy evaluation for inbound communication events."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session

from stackos.communications.contracts import CommunicationDecision
from stackos.communications.resources import communication_record_by_external_id


@dataclass(frozen=True)
class CommunicationPolicyProfile:
    """Provider-neutral policy envelope for one communication profile."""

    provider_key: str
    profile_key: str
    data: Mapping[str, Any]
    disabled_status: str
    store_non_trigger_default: bool
    visibility_blocked_status: str = "surface_blocked"
    ingress_mode_key: str | None = None
    ingress_required_value: str | None = None
    ingress_disabled_status: str = "ingress_disabled"
    allowed_update_types: tuple[str, ...] = ()
    update_blocked_status: str = "update_blocked"


@dataclass(frozen=True)
class CommunicationInteractionCheck:
    """Expected outbound interaction state for an inbound click/callback."""

    external_id: str | None
    trigger_reason: str
    blocked_status: str
    allow_unknown: bool = False
    resource_key: str = "communication-interaction"


@dataclass(frozen=True)
class CommunicationPolicyEvent:
    """Provider-normalized facts required by shared inbound policy."""

    update_type: str
    event_type: str
    text: str = ""
    is_direct: bool = False
    visibility_mode_keys: tuple[str, ...] = ()
    visibility_allowed_keys: tuple[str, ...] = ()
    visibility_denied_keys: tuple[str, ...] = ()
    surface_candidate_refs: tuple[str, ...] = ()
    user_candidate_refs: tuple[str, ...] = ()
    user_allowed_keys: tuple[str, ...] = ()
    user_denied_keys: tuple[str, ...] = ()
    surface_id_prefix: str = "communication-surface"
    user_id_prefix: str = "communication-user"
    username_prefix: str | None = None
    group_trigger_keys: tuple[str, ...] = ("group_trigger",)
    group_always_reason: str = "group_always"
    command_suffixes: tuple[str, ...] = ()
    mention_literals: tuple[str, ...] = ()
    is_self: bool = False
    is_reply_to_bot: bool = False
    interaction: CommunicationInteractionCheck | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def evaluate_inbound_policy(
    session: Session,
    *,
    project_id: int,
    profile: CommunicationPolicyProfile,
    event: CommunicationPolicyEvent,
) -> CommunicationDecision:
    """Evaluate static inbound communication policy in one shared path.

    Visibility answers "may StackOS store this context?". Activation answers
    "may this actor create agent work?". Channel/chat visibility is deliberately
    separate from actor access so a bot can observe broad context while only
    responding to approved users.
    """

    data = profile.data
    if data.get("enabled") is False:
        return CommunicationDecision(
            store=False,
            create_request=False,
            status=profile.disabled_status,
        )
    if profile.ingress_mode_key is not None:
        ingress_mode = data.get(profile.ingress_mode_key)
        if ingress_mode != profile.ingress_required_value:
            return CommunicationDecision(
                store=False,
                create_request=False,
                status=profile.ingress_disabled_status,
            )
    if profile.allowed_update_types and event.update_type not in profile.allowed_update_types:
        return CommunicationDecision(
            store=False,
            create_request=False,
            status=profile.update_blocked_status,
        )
    if event.is_self:
        return CommunicationDecision(
            store=True,
            create_request=False,
            status="self_message_ignored",
        )
    if not _visibility_allowed(profile, event):
        return CommunicationDecision(
            store=False,
            create_request=False,
            status=profile.visibility_blocked_status,
        )

    trigger_match = _trigger_match(profile, event)
    if trigger_match is None:
        visibility = config_policy(data, "visibility_policy")
        store_non_trigger = visibility.get(
            "store_non_trigger_messages",
            profile.store_non_trigger_default,
        )
        if store_non_trigger is not True:
            return CommunicationDecision(
                store=False,
                create_request=False,
                status="not_triggered",
            )
        return CommunicationDecision(store=True, create_request=False, status="observed")

    user_match = _user_match_type(profile, event)
    if user_match is None:
        return CommunicationDecision(
            store=True,
            create_request=False,
            status="invoker_blocked",
        )
    if event.interaction is not None and not _interaction_allowed(
        session,
        project_id=project_id,
        interaction=event.interaction,
        event=event,
    ):
        return CommunicationDecision(
            store=True,
            create_request=False,
            status=event.interaction.blocked_status,
        )

    metadata = dict(event.metadata)
    if user_match != "unreported":
        metadata["identity_confidence"] = user_match
    return CommunicationDecision(
        store=True,
        create_request=True,
        status="request_created",
        trigger_reason=trigger_match["reason"],
        matched_command=trigger_match.get("command"),
        metadata=metadata,
    )


def evaluate_inbound_event_allowlist(
    *,
    enabled: bool,
    event_key: str,
    allowed_event_keys: set[str] | tuple[str, ...] | list[str],
    store_unmatched: bool = True,
    trigger_reason: str = "event_allowlist",
) -> CommunicationDecision:
    """Apply the shared fail-closed request gate for non-message ingress.

    Provider adapters still verify transport authentication and normalize their
    payloads. This helper owns the one generic decision that follows: only an
    explicitly allowlisted event may create agent work. An authenticated but
    unmatched event can be retained as audit context without selecting or
    starting a workflow.
    """

    if not enabled:
        return CommunicationDecision(
            store=False,
            create_request=False,
            status="ingress_disabled",
        )
    normalized_key = event_key.strip()
    allowed = {item.strip() for item in allowed_event_keys if item.strip()}
    if not normalized_key or normalized_key not in allowed:
        return CommunicationDecision(
            store=store_unmatched,
            create_request=False,
            status="event_not_allowlisted",
        )
    return CommunicationDecision(
        store=True,
        create_request=True,
        status="request_created",
        trigger_reason=trigger_reason,
        metadata={"allowlist_match": True},
    )


def config_policy(data: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return dict(value) if isinstance(value, Mapping) else {}


def config_nested(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def config_refs(
    policy: Mapping[str, Any],
    *,
    keys: tuple[str, ...],
    surface_id_prefix: str,
    user_id_prefix: str,
    username_prefix: str | None = None,
) -> set[str]:
    refs: set[str] = set()
    for key in keys:
        for value in config_string_list(policy.get(key)):
            if key.endswith("_ids"):
                prefix = user_id_prefix if "user" in key else surface_id_prefix
                refs.add(f"{prefix}:{value}")
            elif key.endswith("_usernames") and username_prefix:
                refs.add(f"{username_prefix}:{value.lstrip('@')}")
            else:
                refs.add(value)
    return refs


def config_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def candidate_refs(raw_ref: Any, raw_id: Any, prefix: str) -> tuple[str, ...]:
    refs: list[str] = []
    if isinstance(raw_ref, str) and raw_ref:
        refs.append(raw_ref)
    if raw_id is not None:
        refs.append(f"{prefix}:{raw_id}")
        refs.append(str(raw_id))
    return tuple(dict.fromkeys(refs))


def _visibility_allowed(
    profile: CommunicationPolicyProfile,
    event: CommunicationPolicyEvent,
) -> bool:
    visibility = config_policy(profile.data, "visibility_policy")
    mode = _first_policy_value(
        visibility,
        event.visibility_mode_keys,
        default=visibility.get("surface_mode", "all"),
    )
    if mode == "disabled":
        return False
    denied = config_refs(
        visibility,
        keys=event.visibility_denied_keys,
        surface_id_prefix=event.surface_id_prefix,
        user_id_prefix=event.user_id_prefix,
        username_prefix=event.username_prefix,
    )
    if denied and any(candidate in denied for candidate in event.surface_candidate_refs):
        return False
    if mode in {None, "all", "denylist"}:
        return True
    if mode == "allowlist":
        allowed = config_refs(
            visibility,
            keys=event.visibility_allowed_keys,
            surface_id_prefix=event.surface_id_prefix,
            user_id_prefix=event.user_id_prefix,
            username_prefix=event.username_prefix,
        )
        return bool(
            allowed and any(candidate in allowed for candidate in event.surface_candidate_refs)
        )
    return True


def _user_match_type(
    profile: CommunicationPolicyProfile,
    event: CommunicationPolicyEvent,
) -> str | None:
    access = config_policy(profile.data, "access_policy")
    mode = access.get("user_mode")
    if mode == "disabled" or mode is None:
        return None
    denied = config_refs(
        access,
        keys=event.user_denied_keys,
        surface_id_prefix=event.surface_id_prefix,
        user_id_prefix=event.user_id_prefix,
        username_prefix=event.username_prefix,
    )
    if denied and any(candidate in denied for candidate in event.user_candidate_refs):
        return None
    if mode in {"all", "denylist"}:
        return "unrestricted"
    allowed = config_refs(
        access,
        keys=event.user_allowed_keys,
        surface_id_prefix=event.surface_id_prefix,
        user_id_prefix=event.user_id_prefix,
        username_prefix=event.username_prefix,
    )
    if not allowed:
        return None
    for candidate in event.user_candidate_refs:
        if candidate in allowed:
            if event.username_prefix and candidate.startswith(f"{event.username_prefix}:"):
                return "username"
            return "id"
    return None


def _trigger_match(
    profile: CommunicationPolicyProfile,
    event: CommunicationPolicyEvent,
) -> dict[str, Any] | None:
    if event.interaction is not None:
        return {"reason": event.interaction.trigger_reason}
    trigger = config_policy(profile.data, "trigger_policy")
    allowed_events = config_string_list(trigger.get("event_types") or trigger.get("allowed_events"))
    if allowed_events and event.event_type not in allowed_events:
        return None
    command = _matched_command(event.text, trigger, event.command_suffixes)
    if command is not None:
        return {"reason": "command", "command": command}
    if event.event_type == "app_mention":
        return {"reason": "mention"}
    if event.is_direct and trigger.get("dm_trigger", "always") == "always":
        return {"reason": "dm"}
    group_trigger = _first_policy_value(
        trigger,
        event.group_trigger_keys,
        default="mention_or_command",
    )
    if group_trigger == "always":
        return {"reason": event.group_always_reason}
    if group_trigger != "never" and _matches_mention(event.text, trigger, event):
        return {"reason": "mention"}
    if trigger.get("reply_to_bot_triggers") is True and event.is_reply_to_bot:
        return {"reason": "reply_to_bot"}
    return None


def _matched_command(
    text: str,
    trigger: Mapping[str, Any],
    command_suffixes: tuple[str, ...],
) -> dict[str, Any] | None:
    first_token = text.strip().split(maxsplit=1)[0] if text.strip() else ""
    if not first_token:
        return None
    for command in _command_specs(trigger.get("commands")):
        if command.get("enabled") is False:
            continue
        candidates = [
            str(command.get("command") or ""),
            *config_string_list(command.get("aliases")),
        ]
        for candidate in candidates:
            normalized = candidate if candidate.startswith("/") else f"/{candidate}"
            if first_token == normalized:
                return command
            for suffix in command_suffixes:
                if suffix and first_token == f"{normalized}@{suffix.lstrip('@')}":
                    return command
    return None


def _command_specs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    specs: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        command = str(item.get("command") or "").strip()
        if not command:
            continue
        spec = dict(item)
        spec["command"] = command if command.startswith("/") else f"/{command}"
        spec["aliases"] = config_string_list(spec.get("aliases"))
        specs.append(spec)
    return specs


def _matches_mention(
    text: str,
    trigger: Mapping[str, Any],
    event: CommunicationPolicyEvent,
) -> bool:
    folded = text.lower()
    for literal in event.mention_literals:
        if literal and literal.lower() in folded:
            return True
    for pattern in config_string_list(trigger.get("mention_patterns")):
        try:
            if re.search(pattern, text, flags=re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def _interaction_allowed(
    session: Session,
    *,
    project_id: int,
    interaction: CommunicationInteractionCheck,
    event: CommunicationPolicyEvent,
) -> bool:
    if not interaction.external_id:
        return interaction.allow_unknown
    record = communication_record_by_external_id(
        session,
        project_id=project_id,
        resource_key=interaction.resource_key,
        external_id=interaction.external_id,
    )
    if record is None:
        return interaction.allow_unknown
    data = record.data_json or {}
    allowed_users = config_refs(
        data,
        keys=("allowed_user_refs", "allowed_user_ids", "allowed_usernames", "allowed_users"),
        surface_id_prefix=event.surface_id_prefix,
        user_id_prefix=event.user_id_prefix,
        username_prefix=event.username_prefix,
    )
    if allowed_users and not any(
        candidate in allowed_users for candidate in event.user_candidate_refs
    ):
        return False
    allowed_surfaces = config_refs(
        data,
        keys=(
            "allowed_surface_refs",
            "allowed_channel_refs",
            "allowed_channel_ids",
            "allowed_channels",
            "allowed_chat_refs",
            "allowed_chat_ids",
            "allowed_chats",
        ),
        surface_id_prefix=event.surface_id_prefix,
        user_id_prefix=event.user_id_prefix,
        username_prefix=event.username_prefix,
    )
    if not allowed_surfaces:
        allowed_surfaces = {
            str(value)
            for value in (data.get("surface_ref"), data.get("channel_ref"), data.get("chat_ref"))
            if isinstance(value, str) and value
        }
    return not allowed_surfaces or any(
        candidate in allowed_surfaces for candidate in event.surface_candidate_refs
    )


def _first_policy_value(
    policy: Mapping[str, Any],
    keys: tuple[str, ...],
    *,
    default: Any = None,
) -> Any:
    for key in keys:
        value = policy.get(key)
        if value is not None:
            return value
    return default
