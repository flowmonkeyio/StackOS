"""Provider setup guidance shared across agent-facing surfaces."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stackos.artifacts import redact_secret_text

_URL_FIELDS = (
    ("homepage_url", "Homepage", "provider_homepage"),
    ("signup_url", "Register", "provider_registration"),
    ("console_url", "Console", "provider_console"),
    ("credential_url", "Credentials", "provider_credential"),
    ("billing_url", "Billing", "provider_billing"),
    ("docs_url", "Docs", "provider_docs"),
    ("support_url", "Support", "provider_support"),
    ("callback_url", "OAuth callback", "provider_oauth_callback"),
)
_SETUP_TEXT_KEYS = frozenset(
    {
        "credential_label",
        "setup_note",
        "local_setup_label",
        "local_setup_note",
        "fallback_reason",
        "callback_note",
        "repair_note",
        "verified_at",
    }
)
_SETUP_URL_KEYS = frozenset(
    {
        "homepage_url",
        "signup_url",
        "console_url",
        "credential_url",
        "billing_url",
        "docs_url",
        "support_url",
        "fallback_url",
        "callback_url",
    }
)
_SETUP_ALLOWED_KEYS = _SETUP_TEXT_KEYS | _SETUP_URL_KEYS | {"url_confidence"}


class ProviderSetupUrlOut(BaseModel):
    """One provider setup URL with confidence for agent self-service answers."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    url: str
    purpose: str
    confidence: str = "directional"


class ProviderSetupOut(BaseModel):
    """Sanitized setup guidance for connecting a provider."""

    model_config = ConfigDict(extra="forbid")

    provider_key: str
    provider_name: str | None = None
    local_setup_url: str | None = None
    local_setup_label: str = "Connect in StackOS"
    local_setup_note: str | None = None
    credential_label: str | None = None
    setup_note: str | None = None
    homepage_url: str | None = None
    signup_url: str | None = None
    console_url: str | None = None
    credential_url: str | None = None
    billing_url: str | None = None
    docs_url: str | None = None
    support_url: str | None = None
    callback_url: str | None = None
    callback_note: str | None = None
    repair_note: str | None = None
    fallback_url: str | None = None
    fallback_reason: str | None = None
    url_confidence: dict[str, str] = Field(default_factory=dict)
    verified_at: str | None = None
    urls: list[ProviderSetupUrlOut] = Field(default_factory=list)


def provider_local_setup_url(project_id: int | None, provider_key: str | None = None) -> str | None:
    """Return the local StackOS connection URL for a project/provider."""
    if project_id is None:
        return None
    suffix = f"?provider_key={provider_key}" if provider_key else ""
    return f"http://127.0.0.1:5180/projects/{project_id}/connections{suffix}"


def build_provider_setup(
    *,
    project_id: int | None,
    provider_key: str | None,
    provider_name: str | None = None,
    provider_config_json: dict[str, Any] | None = None,
) -> ProviderSetupOut | None:
    """Build normalized setup guidance from safe provider manifest config."""
    if provider_key is None:
        return None
    config = provider_config_json if isinstance(provider_config_json, dict) else {}
    raw_setup = config.get("setup")
    setup = sanitize_provider_setup_config(raw_setup) or {}
    confidence = setup.get("url_confidence") or {}
    docs_url = _clean_url(setup.get("docs_url")) or _first_doc_url(config.get("docs"))
    values: dict[str, Any] = {
        "provider_key": provider_key,
        "provider_name": provider_name,
        "local_setup_url": provider_local_setup_url(project_id, provider_key),
        "local_setup_label": str(setup.get("local_setup_label") or "Connect in StackOS"),
        "local_setup_note": _clean_text(setup.get("local_setup_note")),
        "credential_label": _clean_text(setup.get("credential_label")),
        "setup_note": _clean_text(setup.get("setup_note")) or _clean_text(config.get("setup_note")),
        "homepage_url": _clean_url(setup.get("homepage_url")),
        "signup_url": _clean_url(setup.get("signup_url")),
        "console_url": _clean_url(setup.get("console_url")),
        "credential_url": _clean_url(setup.get("credential_url")),
        "billing_url": _clean_url(setup.get("billing_url")),
        "docs_url": docs_url,
        "support_url": _clean_url(setup.get("support_url")),
        "callback_url": _clean_url(setup.get("callback_url")),
        "callback_note": _clean_text(setup.get("callback_note")),
        "repair_note": _clean_text(setup.get("repair_note")),
        "fallback_url": _clean_url(setup.get("fallback_url")),
        "fallback_reason": _clean_text(setup.get("fallback_reason")),
        "url_confidence": {str(key): str(value) for key, value in confidence.items()},
        "verified_at": _clean_text(setup.get("verified_at")),
    }
    urls = _setup_urls(values)
    if values["fallback_url"] and not any(item.url == values["fallback_url"] for item in urls):
        urls.append(
            ProviderSetupUrlOut(
                key="fallback_url",
                label="Fallback",
                url=values["fallback_url"],
                purpose="provider_fallback",
                confidence=values["url_confidence"].get("fallback_url", "directional"),
            )
        )
    values["urls"] = urls
    if not values["local_setup_url"] and not values["urls"] and not values["setup_note"]:
        return None
    return ProviderSetupOut(**values)


def sanitize_provider_setup_config(value: Any) -> dict[str, Any] | None:
    """Return safe provider setup config for all agent-facing surfaces."""
    if not isinstance(value, dict):
        return None
    safe: dict[str, Any] = {}
    for key in _SETUP_TEXT_KEYS:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            safe[key] = redact_secret_text(item.strip())
    for key in _SETUP_URL_KEYS:
        item = value.get(key)
        if isinstance(item, str):
            text = item.strip()
            if text.startswith(("https://", "http://")):
                safe[key] = redact_secret_text(text)
    confidence = value.get("url_confidence")
    if isinstance(confidence, dict):
        safe["url_confidence"] = {
            str(key): str(item)
            for key, item in confidence.items()
            if key in _SETUP_URL_KEYS and str(item) in {"verified", "directional"}
        }
    return safe or None


def find_provider_setup_secret_paths(value: Any, *, path: str = "$") -> list[str]:
    """Return setup config paths with unsupported keys or secret-like values."""
    paths: list[str] = []
    if not isinstance(value, dict):
        return paths
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        child_path = f"{path}.{key}"
        if key not in _SETUP_ALLOWED_KEYS:
            paths.append(child_path)
            continue
        if key in _SETUP_URL_KEYS and isinstance(raw_value, str):
            text = raw_value.strip()
            if text and not text.startswith(("https://", "http://")):
                paths.append(child_path)
                continue
            if redact_secret_text(text) != text:
                paths.append(child_path)
            continue
        if key in _SETUP_TEXT_KEYS and isinstance(raw_value, str):
            if redact_secret_text(raw_value) != raw_value:
                paths.append(child_path)
            continue
        if key == "url_confidence":
            if not isinstance(raw_value, dict):
                paths.append(child_path)
                continue
            for confidence_key, confidence_value in raw_value.items():
                if confidence_key not in _SETUP_URL_KEYS or confidence_value not in {
                    "verified",
                    "directional",
                }:
                    paths.append(f"{child_path}.{confidence_key}")
    return paths


def _setup_urls(values: dict[str, Any]) -> list[ProviderSetupUrlOut]:
    urls: list[ProviderSetupUrlOut] = []
    confidence: dict[str, str] = values["url_confidence"]
    for field, label, purpose in _URL_FIELDS:
        url = values.get(field)
        if not isinstance(url, str) or not url:
            continue
        urls.append(
            ProviderSetupUrlOut(
                key=field,
                label=label,
                url=url,
                purpose=purpose,
                confidence=confidence.get(field, "directional"),
            )
        )
    return urls


def _first_doc_url(value: Any) -> str | None:
    if not isinstance(value, list):
        return None
    for item in value:
        url = _clean_url(item)
        if url:
            return url
    return None


def _clean_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text.startswith(("https://", "http://")):
        return None
    return text


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


__all__ = [
    "ProviderSetupOut",
    "ProviderSetupUrlOut",
    "build_provider_setup",
    "find_provider_setup_secret_paths",
    "provider_local_setup_url",
    "sanitize_provider_setup_config",
]
