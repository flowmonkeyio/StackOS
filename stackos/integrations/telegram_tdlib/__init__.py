"""Private TDLib runtime primitives used by the Telegram integration owner."""

from stackos.integrations.telegram_tdlib.native import (
    TelegramTdlibClient,
    TelegramTdlibClosedError,
    TelegramTdlibCloseTimeout,
    TelegramTdlibNativeError,
    TelegramTdlibRequestError,
    load_managed_tdlib,
)
from stackos.integrations.telegram_tdlib.proxy import (
    TelegramProxyBootstrapReceipt,
    TelegramProxyConfig,
    TelegramProxyController,
    TelegramProxyReceipt,
    TelegramProxyValidationError,
)
from stackos.integrations.telegram_tdlib.runtime import (
    TDLIB_SOURCE_COMMIT,
    TelegramTdlibRuntime,
    TelegramTdlibRuntimeError,
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
    TelegramApplicationCredentials,
    TelegramTdlibSession,
    TelegramTdlibSessionConfig,
    TelegramTdlibSessionError,
    TelegramTdlibSessionReceipt,
    TelegramTdlibSessionRegistration,
    TelegramTdlibSessionRegistry,
)

__all__ = [
    "TDLIB_SOURCE_COMMIT",
    "TelegramApplicationCredentials",
    "TelegramProxyBootstrapReceipt",
    "TelegramProxyConfig",
    "TelegramProxyController",
    "TelegramProxyReceipt",
    "TelegramProxyValidationError",
    "TelegramTdlibClient",
    "TelegramTdlibCloseTimeout",
    "TelegramTdlibClosedError",
    "TelegramTdlibMessageReceiptTimeout",
    "TelegramTdlibNativeError",
    "TelegramTdlibRequestError",
    "TelegramTdlibRuntime",
    "TelegramTdlibRuntimeError",
    "TelegramTdlibService",
    "TelegramTdlibServiceError",
    "TelegramTdlibSession",
    "TelegramTdlibSessionConfig",
    "TelegramTdlibSessionError",
    "TelegramTdlibSessionReceipt",
    "TelegramTdlibSessionRegistration",
    "TelegramTdlibSessionRegistry",
    "load_managed_tdlib",
    "tdlib_library_path",
    "tdlib_runtime_root",
    "verify_tdlib_runtime",
]
