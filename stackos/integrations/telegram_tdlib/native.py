"""Host credential invalidation from explicit native authorization rejection facts."""

from stackos_connectors.connectors.telegram.tdlib.native import TelegramTdlibRequestError

_SAVED_AUTH_REJECTIONS = frozenset(
    {
        "AUTH_KEY_UNREGISTERED",
        "SESSION_REVOKED",
        "BOT_TOKEN_INVALID",
        "TOKEN_INVALID",
        "USER_DEACTIVATED",
        "USER_DEACTIVATED_BAN",
    }
)


def saved_authorization_rejected(error: BaseException) -> bool:
    """Only explicit, already-allowlisted provider auth evidence invalidates sign-in."""
    return (
        isinstance(error, TelegramTdlibRequestError) and error.error_name in _SAVED_AUTH_REJECTIONS
    )
