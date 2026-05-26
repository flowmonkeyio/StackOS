"""Telegram communication profile and request-origin policy."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from stackos.actions.connectors import ActionConnectorRequest
from stackos.communications import communication_profile_record_by_key, merged_provider_profile
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.base import ValidationError


def _request_profile_key(request: ActionConnectorRequest) -> str:
    profile_key = request.input_json.get("profile_key")
    if not isinstance(profile_key, str) or not profile_key.strip():
        raise ValidationError("Telegram profile_key is required")
    return profile_key.strip()


def _enforce_telegram_profile(request: ActionConnectorRequest) -> dict[str, Any]:
    profile_key = _request_profile_key(request)
    if request.session is None:
        raise ValidationError("Telegram profile enforcement requires a repository session")
    record = communication_profile_record_by_key(
        request.session,
        project_id=request.project_id,
        key=profile_key,
    )
    if record is None:
        raise ValidationError("Telegram communication profile was not found")
    data = merged_provider_profile(dict(record.data_json or {}), "telegram-bot")
    if data.get("key") != profile_key or data.get("provider_key") != "telegram-bot":
        raise ValidationError("Telegram communication profile was not found")
    _validate_telegram_profile(data)
    expected_profile = data.get("auth_profile_key")
    actual_profile = request.credential.integration.profile_key if request.credential else None
    actual_project = request.credential.integration.project_id if request.credential else None
    if actual_project != request.project_id:
        raise ValidationError("Telegram communication profile requires a project-scoped credential")
    if expected_profile != actual_profile:
        raise ValidationError("Telegram communication profile does not match credential profile")
    if data.get("enabled") is False:
        raise ValidationError("Telegram communication profile is disabled")
    return data


def _enforce_profile_chat(
    request: ActionConnectorRequest,
    raw_ref: str,
) -> dict[str, Any]:
    profile = _enforce_telegram_profile(request)
    resolved_ref = _resolve_profile_ref(profile, raw_ref, "chats", "chat_refs")
    access = profile.get("access_policy")
    access_policy = access if isinstance(access, Mapping) else {}
    denied = _profile_refs(access_policy, "denied_chat_refs", "denied_chat_ids", "denied_chats")
    candidates = _candidate_refs(raw_ref, resolved_ref, "telegram-chat")
    if denied and any(candidate in denied for candidate in candidates):
        raise ValidationError(f"Telegram communication profile does not allow chat {raw_ref!r}")
    _enforce_response_origin(request, profile, candidates)
    allowed = _profile_refs(access_policy, "allowed_chat_refs", "allowed_chat_ids", "allowed_chats")
    if not allowed:
        return profile
    if any(candidate in allowed for candidate in candidates):
        return profile
    raise ValidationError(f"Telegram communication profile does not allow chat {raw_ref!r}")


def _enforce_response_origin(
    request: ActionConnectorRequest,
    profile: Mapping[str, Any],
    chat_candidates: list[str],
) -> None:
    response = profile.get("response_policy")
    response_policy = response if isinstance(response, Mapping) else {}
    request_id = request.input_json.get("source_agent_request_id")
    if not isinstance(request_id, int) or isinstance(request_id, bool):
        if response_policy.get("origin_required") is True and request.input_json.get(
            "reply_to_message_ref"
        ):
            raise ValidationError("Telegram response requires source_agent_request_id")
        return
    if request.session is None:
        raise ValidationError("Telegram response origin enforcement requires a repository session")
    source = AgentRequestRepository(request.session).get(
        project_id=request.project_id,
        request_id=request_id,
    )
    if source.source_provider != "telegram-bot":
        raise ValidationError("Telegram response source must be a Telegram agent request")
    if not source.source_message_ref:
        raise ValidationError("Telegram response source must include a Telegram source message")
    metadata = source.metadata_json or {}
    profile_key = _request_profile_key(request)
    profile_ref = profile.get("profile_ref")
    if metadata.get("profile_key") != profile_key or metadata.get("profile_ref") != profile_ref:
        raise ValidationError("Telegram response source does not match communication profile")
    source_chat = metadata.get("chat_ref")
    if not isinstance(source_chat, str) or source_chat not in chat_candidates:
        raise ValidationError("Telegram response chat does not match request origin")
    source_message = source.source_message_ref
    if response_policy.get("reply_to_source_message") is True and (
        source_message is None or request.input_json.get("reply_to_message_ref") != source_message
    ):
        raise ValidationError("Telegram response must reply to the source message")
    source_thread = metadata.get("thread_ref")
    if (
        response_policy.get("same_thread") is True
        and isinstance(source_thread, str)
        and request.input_json.get("thread_ref") != source_thread
    ):
        raise ValidationError("Telegram response thread does not match request origin")


def _validate_telegram_profile(profile: Mapping[str, Any]) -> None:
    access = profile.get("access_policy")
    if not isinstance(access, Mapping):
        raise ValidationError("Telegram communication profile requires access_policy")
    for key in ("dm_mode", "group_mode", "user_mode"):
        mode = access.get(key)
        if mode not in {"all", "allowlist", "denylist", "disabled"}:
            raise ValidationError(f"Telegram communication profile access_policy.{key} is required")
        if key == "user_mode" and mode == "allowlist":
            has_user_allowlist = bool(
                _profile_refs(
                    access,
                    "allowed_user_refs",
                    "allowed_user_ids",
                    "allowed_usernames",
                    "allowed_users",
                )
            )
            if not has_user_allowlist:
                raise ValidationError(
                    "Telegram communication profile access_policy.user_mode=allowlist "
                    "requires allowed users"
                )


def _resolve_profile_ref(profile: Mapping[str, Any], value: Any, *map_keys: str) -> Any:
    if not isinstance(value, str) or not value:
        return value
    for map_key in map_keys:
        mapping = profile.get(map_key)
        if isinstance(mapping, Mapping):
            mapped = mapping.get(value)
            if mapped is not None:
                return mapped
    refs = profile.get("refs")
    if isinstance(refs, Mapping):
        mapped = refs.get(value)
        if mapped is not None:
            return mapped
    provider_id = _provider_id_from_ref(value)
    if provider_id is not None:
        return provider_id
    return value


def _provider_id_from_ref(value: str) -> int | None:
    if value.startswith("telegram-chat:"):
        return _int_text(value.removeprefix("telegram-chat:"))
    if value.startswith("telegram-message:"):
        parts = value.split(":")
        if len(parts) == 3:
            return _int_text(parts[2])
    if value.startswith("telegram-thread:"):
        parts = value.split(":")
        if len(parts) == 3 and parts[2] != "default":
            return _int_text(parts[2])
    return None


def _int_text(value: str) -> int | None:
    stripped = value.strip()
    if re.fullmatch(r"-?\d+", stripped):
        return int(stripped)
    return None


def _profile_refs(policy: Mapping[str, Any], *keys: str) -> set[str]:
    refs: set[str] = set()
    for key in keys:
        for value in _split_config_values(policy.get(key)):
            if key.endswith("_ids"):
                prefix = "telegram-user" if "user" in key else "telegram-chat"
                refs.add(f"{prefix}:{value}")
            elif key.endswith("_usernames"):
                refs.add(f"telegram-username:{value.lstrip('@')}")
            else:
                refs.add(value)
    return refs


def _candidate_refs(raw_ref: Any, resolved_ref: Any, prefix: str) -> list[str]:
    refs: list[str] = []
    if isinstance(raw_ref, str) and raw_ref:
        refs.append(raw_ref)
    if resolved_ref is not None:
        refs.append(str(resolved_ref))
        refs.append(f"{prefix}:{resolved_ref}")
    return refs


def _enforce_allowed_updates(
    request: ActionConnectorRequest,
    profile: Mapping[str, Any] | None = None,
) -> None:
    configured = _split_config_values((profile or {}).get("allowed_updates"))
    visibility = (profile or {}).get("visibility_policy")
    if not configured and isinstance(visibility, Mapping):
        configured = _split_config_values(visibility.get("allowed_updates"))
    if not configured:
        return
    requested = request.input_json.get("allowed_updates")
    if not isinstance(requested, list):
        return
    extra = sorted({str(item) for item in requested} - set(configured))
    if extra:
        raise ValidationError(
            "Telegram requested updates are outside the credential allowlist",
            data={"updates": extra},
        )


def _split_config_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []
