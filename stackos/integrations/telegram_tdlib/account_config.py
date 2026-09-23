"""Validate Account setup through the same proxy contract as the native session."""

from __future__ import annotations

from typing import Any

from stackos.repositories.base import ValidationError

from .proxy import TelegramProxyConfig, TelegramProxyValidationError

PROXY_SECRET_FIELDS = frozenset({"proxy_username", "proxy_password", "proxy_secret"})
PROXY_CONFIG_FIELDS = frozenset(
    {"proxy_enabled", "proxy_type", "proxy_host", "proxy_port", "proxy_http_only"}
)


def account_proxy(config: dict[str, Any], secrets: dict[str, Any]) -> TelegramProxyConfig | None:
    if not config.get("proxy_enabled", False):
        return None
    return TelegramProxyConfig(
        kind=config.get("proxy_type", ""),
        host=config.get("proxy_host", ""),
        port=config.get("proxy_port", 0),
        username=secrets.get("proxy_username", ""),
        password=secrets.get("proxy_password", ""),
        http_only=config.get("proxy_http_only", False),
        mtproto_secret=secrets.get("proxy_secret", ""),
    )


def normalize_account_config(
    *,
    config: dict[str, Any],
    secrets: dict[str, str],
    supplied_fields: dict[str, Any],
) -> None:
    """Normalize in place, dropping obsolete inherited settings, never explicit errors."""
    config.setdefault("proxy_enabled", False)
    if not config["proxy_enabled"]:
        for key in PROXY_CONFIG_FIELDS - {"proxy_enabled"}:
            config.pop(key, None)
        for key in PROXY_SECRET_FIELDS:
            secrets.pop(key, None)
        return

    kind = config.get("proxy_type")
    incompatible = (
        {"proxy_username", "proxy_password", "proxy_http_only"}
        if kind == "mtproto"
        else {"proxy_secret"}
    )
    for key in incompatible:
        if supplied_fields.get(key):
            raise ValidationError(
                f"{key} is not supported for the selected Telegram proxy type",
                data={"field": key, "provider_key": "telegram"},
            )
        config.pop(key, None)
        secrets.pop(key, None)
    try:
        account_proxy(config, secrets)
    except TelegramProxyValidationError as exc:
        raise ValidationError(str(exc), data={"provider_key": "telegram"}) from exc
