"""Provider-neutral communication delivery policy gates."""

from __future__ import annotations

from typing import Any

from stackos.communications.target_policy import target_policy_allowed
from stackos.operations.communication_platform import (
    CommunicationTargetOut,
    _string_list,
)

from .errors import _reject


def _ensure_target_policy(
    *,
    policy: dict[str, Any],
    target: CommunicationTargetOut,
    actor_ref: str,
    source_surface_ref: str | None,
    invoker_ref: str | None,
    resolved: dict[str, Any],
) -> None:
    allowed, reason = target_policy_allowed(
        policy,
        target_ref=target.target_ref,
        profile_ref=actor_ref,
        source_surface_ref=source_surface_ref,
        invoker_ref=invoker_ref,
    )
    if target.enabled and allowed:
        return
    if not target.enabled:
        reason = "target_disabled"
    _reject(
        code="COMM_TARGET_NOT_ALLOWED",
        category="policy",
        message=f"Communication target policy rejected send: {reason}.",
        resolved=resolved,
        failed_paths=[
            {
                "path": "/to",
                "requested": target.target_ref,
                "policy_reason": reason,
            }
        ],
        repair_options=[
            {
                "id": "choose_allowed_target_or_actor",
                "description": (
                    "Retry with an allowed target/from pair or update communicationTarget policy."
                ),
            }
        ],
    )


def _ensure_reply_policy(
    *,
    operation: str,
    provider_key: str,
    actor: dict[str, Any],
    source: dict[str, Any],
    target: CommunicationTargetOut,
) -> None:
    profile = dict(actor.get("profile") or {})
    response_policy = dict(profile.get("response_policy") or {})
    mode = str(response_policy.get("mode") or "origin").strip()
    if mode in {"disabled", "deny"}:
        _reject(
            code="COMM_REPLY_NOT_ALLOWED",
            category="policy",
            message="Profile response_policy disables replies to request origins.",
            resolved={
                "operation": operation,
                "provider": provider_key,
                "from": actor["profile_ref"],
                "source_request_id": source.get("source_request_id"),
            },
            failed_paths=[{"path": "/request_id", "policy_reason": "response_policy_disabled"}],
        )
    _ensure_ref_policy(
        policy=response_policy,
        allowed_key="allowed_source_surface_refs",
        denied_key="denied_source_surface_refs",
        value=source.get("source_surface_ref"),
        path="/request_id",
        requested="origin.surface",
        denial_code="COMM_REPLY_NOT_ALLOWED",
        resolved={
            "operation": operation,
            "provider": provider_key,
            "from": actor["profile_ref"],
            "surface_ref": source.get("source_surface_ref"),
        },
    )
    _ensure_ref_policy(
        policy=response_policy,
        allowed_key="allowed_invoker_refs",
        denied_key="denied_invoker_refs",
        value=source.get("invoker_ref"),
        path="/request_id",
        requested="origin.invoker",
        denial_code="COMM_REPLY_NOT_ALLOWED",
        resolved={
            "operation": operation,
            "provider": provider_key,
            "from": actor["profile_ref"],
            "invoker_ref": source.get("invoker_ref"),
        },
    )
    access = dict(profile.get("access_policy") or {})
    invoker_ref = source.get("invoker_ref")
    if not isinstance(invoker_ref, str) or not invoker_ref:
        if access.get("user_mode") in {"allowlist", "denylist", "all"}:
            _reject(
                code="COMM_REPLY_INVOKER_UNKNOWN",
                category="policy",
                message=(
                    "Reply origin has no invoker_ref, so user response policy cannot be verified."
                ),
                resolved={
                    "operation": operation,
                    "provider": provider_key,
                    "from": actor["profile_ref"],
                    "surface_ref": target.surface_ref,
                },
                failed_paths=[{"path": "/request_id", "requested": "origin.invoker_ref"}],
            )
        return
    user_mode = access.get("user_mode")
    denied = set(_string_list(access.get("denied_user_refs")))
    if invoker_ref in denied:
        _reject(
            code="COMM_REPLY_NOT_ALLOWED",
            category="policy",
            message=f"Profile access_policy denies replies to invoker {invoker_ref}.",
            resolved={
                "operation": operation,
                "provider": provider_key,
                "from": actor["profile_ref"],
                "invoker_ref": invoker_ref,
            },
            failed_paths=[{"path": "/request_id", "policy_reason": "invoker_denied"}],
        )
    if user_mode == "allowlist":
        allowed = set(_string_list(access.get("allowed_user_refs")))
        if invoker_ref not in allowed:
            _reject(
                code="COMM_REPLY_NOT_ALLOWED",
                category="policy",
                message=f"Profile access_policy does not allow replies to invoker {invoker_ref}.",
                resolved={
                    "operation": operation,
                    "provider": provider_key,
                    "from": actor["profile_ref"],
                    "invoker_ref": invoker_ref,
                },
                failed_paths=[{"path": "/request_id", "policy_reason": "invoker_not_allowed"}],
            )


def _ensure_ref_policy(
    *,
    policy: dict[str, Any],
    allowed_key: str,
    denied_key: str,
    value: Any,
    path: str,
    requested: str,
    denial_code: str,
    resolved: dict[str, Any],
) -> None:
    if not isinstance(value, str) or not value:
        return
    denied = set(_string_list(policy.get(denied_key)))
    if value in denied:
        _reject(
            code=denial_code,
            category="policy",
            message=f"Response policy denies {value}.",
            resolved=resolved,
            failed_paths=[{"path": path, "requested": requested, "policy_reason": "denied"}],
        )
    allowed = set(_string_list(policy.get(allowed_key)))
    if allowed and value not in allowed:
        _reject(
            code=denial_code,
            category="policy",
            message=f"Response policy does not allow {value}.",
            resolved=resolved,
            failed_paths=[{"path": path, "requested": requested, "policy_reason": "not_allowed"}],
        )


def _ensure_provider_action_ref(
    *,
    operation: str,
    provider_key: str,
    action_ref: str,
    allowed: set[str],
    target: CommunicationTargetOut,
) -> None:
    if action_ref in allowed:
        return
    _reject(
        code="COMM_UNSUPPORTED_PROVIDER_ACTION",
        category="provider",
        message=f"{provider_key} target action {action_ref!r} is not supported by {operation}.",
        resolved={
            "operation": operation,
            "provider": provider_key,
            "target_ref": target.target_ref,
            "action_ref": action_ref,
        },
        failed_paths=[
            {
                "path": "/to",
                "requested": action_ref,
                "target_supports": sorted(allowed),
            }
        ],
        repair_options=[
            {
                "id": "use_provider_action_escape_hatch",
                "description": (
                    "Call action.run directly only when the agent intentionally needs a "
                    "provider-specific custom action."
                ),
            }
        ],
    )


def _ensure_target_allows_resolved_action_ref(
    *,
    operation: str,
    provider_key: str,
    configured_action_ref: str,
    resolved_action_ref: str,
    target: CommunicationTargetOut,
) -> None:
    if resolved_action_ref == configured_action_ref:
        return
    metadata = dict(target.metadata_json or {})
    if metadata.get("action_mode") == "auto":
        return
    allowed = set(_string_list(metadata.get("allowed_action_refs")))
    if resolved_action_ref in allowed:
        return
    _reject(
        code="COMM_TARGET_ACTION_VARIANT_NOT_ALLOWED",
        category="provider",
        message=(
            f"Target {target.key} resolves to {configured_action_ref}, but this content "
            f"requires {resolved_action_ref}."
        ),
        resolved={
            "operation": operation,
            "provider": provider_key,
            "target_ref": target.target_ref,
            "configured_action_ref": configured_action_ref,
            "required_action_ref": resolved_action_ref,
        },
        failed_paths=[
            {
                "path": "/content/attachments",
                "requested": resolved_action_ref,
                "target_supports": sorted({configured_action_ref, *allowed}),
            }
        ],
        repair_options=[
            {
                "id": "allow_target_action_variant",
                "description": (
                    "Configure target metadata action_mode=auto or allowed_action_refs "
                    "for this provider action variant."
                ),
            }
        ],
    )
