"""Stable ownership token for automatic Telegram Account delivery holds."""


def telegram_native_auth_hold_reason(credential_ref: str) -> str:
    """Return the Account-scoped hold owner shared by auth and runtime callbacks."""
    return f"telegram-native-auth:{credential_ref}"


__all__ = ["telegram_native_auth_hold_reason"]
