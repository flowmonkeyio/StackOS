"""Shared target authorization for intent delivery and provider batch adapters."""

from typing import Any


def _string_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def target_policy_allowed(
    policy: dict[str, Any],
    *,
    target_ref: str,
    profile_ref: str | None,
    source_surface_ref: str | None,
    invoker_ref: str | None,
) -> tuple[bool, str | None]:
    mode = str(policy.get("mode") or "explicit-target")
    if mode in {"disabled", "deny"}:
        return False, "send_policy_disabled"
    denied_invokers = set(_string_list(policy.get("denied_invoker_refs")))
    if invoker_ref is not None and invoker_ref in denied_invokers:
        return False, "invoker_denied"
    allowed_profiles = set(_string_list(policy.get("allowed_profile_refs")))
    allowed_sources = set(_string_list(policy.get("allowed_source_surface_refs")))
    allowed_targets = set(_string_list(policy.get("allowed_target_refs")))
    allowed_invokers = set(_string_list(policy.get("allowed_invoker_refs")))
    if mode == "denylist" and not (
        allowed_profiles or allowed_sources or allowed_targets or allowed_invokers
    ):
        if policy.get("requires_approval") is True:
            return False, "approval_required"
        return True, None
    if (
        not allowed_profiles
        and not allowed_sources
        and not allowed_targets
        and not allowed_invokers
    ):
        return False, "send_policy_missing_allowlist"
    if allowed_profiles and profile_ref not in allowed_profiles:
        return False, "profile_not_allowed"
    if allowed_sources and source_surface_ref not in allowed_sources:
        return False, "source_surface_not_allowed"
    if allowed_targets and target_ref not in allowed_targets:
        return False, "target_not_allowed"
    if allowed_invokers and invoker_ref not in allowed_invokers:
        return False, "invoker_not_allowed"
    if policy.get("requires_approval") is True:
        return False, "approval_required"
    return True, None
