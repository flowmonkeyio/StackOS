"""Private TDLib runtime primitives used by the Telegram integration owner."""

from stackos.integrations.telegram_tdlib.runtime import (
    TDLIB_SOURCE_COMMIT,
    TelegramTdlibRuntime,
    TelegramTdlibRuntimeError,
    load_managed_tdlib,
    tdlib_library_path,
    tdlib_runtime_root,
    verify_tdlib_runtime,
)
from stackos.integrations.telegram_tdlib.service import (
    TelegramTdlibMessageReceiptTimeout,
    TelegramTdlibService,
    TelegramTdlibServiceError,
)
from stackos.integrations.telegram_tdlib.sessions import (
    TelegramTdlibSessionRegistration,
    TelegramTdlibSessionRegistry,
)

__all__ = [
    "TDLIB_SOURCE_COMMIT",
    "TelegramTdlibMessageReceiptTimeout",
    "TelegramTdlibRuntime",
    "TelegramTdlibRuntimeError",
    "TelegramTdlibService",
    "TelegramTdlibServiceError",
    "TelegramTdlibSessionRegistration",
    "TelegramTdlibSessionRegistry",
    "load_managed_tdlib",
    "tdlib_library_path",
    "tdlib_runtime_root",
    "verify_tdlib_runtime",
]
