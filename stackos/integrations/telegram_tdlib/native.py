"""Private ctypes bridge for TDLib's supported JSON C interface.

TDLib returns receive buffers that are invalidated by the next call on the same
thread.  ``CtypesTdlibJsonAbi.receive`` copies that buffer immediately; one
``TelegramTdlibClient`` receiver thread is the only code path that calls
receive, which preserves TDLib update order.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import re
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from stackos.integrations.telegram_tdlib.runtime import (
    TelegramTdlibRuntime,
    TelegramTdlibRuntimeError,
    verify_tdlib_runtime,
)

_JSON_SEPARATORS = (",", ":")
_TERMINAL_UPDATE = object()
_FLOOD_WAIT = re.compile(r"^FLOOD_WAIT_(?P<seconds>[1-9][0-9]{0,6})$")
_SLOWMODE_WAIT = re.compile(r"^SLOWMODE_WAIT_(?P<seconds>[1-9][0-9]{0,6})$")
_RETRY_AFTER = re.compile(r"^Too Many Requests: retry after (?P<seconds>[1-9][0-9]{0,6})$")
_SAFE_TDLIB_ERROR_NAMES = frozenset(
    {
        "AUTH_KEY_UNREGISTERED",
        "AUTH_RESTART",
        "BOT_TOKEN_INVALID",
        "EMAIL_CODE_EXPIRED",
        "EMAIL_CODE_INVALID",
        "EMAIL_INVALID",
        "EMAIL_NOT_ALLOWED",
        "PASSWORD_HASH_INVALID",
        "PEER_FLOOD",
        "PHONE_CODE_EMPTY",
        "PHONE_CODE_EXPIRED",
        "PHONE_CODE_HASH_EMPTY",
        "PHONE_CODE_INVALID",
        "PHONE_CODE_UNOCCUPIED",
        "PHONE_NUMBER_INVALID",
        "SESSION_PASSWORD_NEEDED",
        "SESSION_REVOKED",
        "TOKEN_INVALID",
        "USER_DEACTIVATED",
        "USER_DEACTIVATED_BAN",
    }
)
_MAX_RETRY_AFTER_SECONDS = 7 * 24 * 60 * 60


class TelegramTdlibNativeError(RuntimeError):
    """TDLib's local JSON ABI couldn't be used safely."""


class TelegramTdlibRequestError(TelegramTdlibNativeError):
    """A TDLib request was rejected without exposing TDLib's raw error text."""

    def __init__(
        self,
        *,
        code: int | None,
        phase: str,
        error_name: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        self.code = code
        self.phase = phase
        self.error_name = error_name
        self.retry_after_seconds = retry_after_seconds
        suffix = f" (code {code})" if code is not None else ""
        classification = f" [{error_name}]" if error_name is not None else ""
        retry = (
            f" Retry after {retry_after_seconds} seconds."
            if retry_after_seconds is not None
            else ""
        )
        super().__init__(f"TDLib rejected a request during {phase}{suffix}{classification}.{retry}")


class TelegramTdlibClosedError(TelegramTdlibNativeError):
    """The caller used a client that has already entered its close sequence."""


class TelegramTdlibCloseTimeout(TelegramTdlibNativeError):
    """TDLib didn't emit its terminal authorization update before the deadline."""


class TdlibJsonAbi(Protocol):
    """The narrow JSON C ABI needed by the session runtime."""

    def create_client(self) -> int: ...

    def send(self, handle: int, request: bytes) -> None: ...

    def receive(self, handle: int, timeout_seconds: float) -> bytes | None: ...

    def destroy_client(self, handle: int) -> None: ...


class TdlibJsonServiceAbi(TdlibJsonAbi, Protocol):
    def execute(self, request: bytes) -> bytes | None: ...


class CtypesTdlibJsonAbi:
    """Exact ctypes declarations for TDLib's public td_json_client C ABI."""

    def __init__(self, library: ctypes.CDLL) -> None:
        self._library = library
        self._create = library.td_json_client_create
        self._create.argtypes = []
        self._create.restype = ctypes.c_void_p
        self._send = library.td_json_client_send
        self._send.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self._send.restype = None
        self._receive = library.td_json_client_receive
        self._receive.argtypes = [ctypes.c_void_p, ctypes.c_double]
        self._receive.restype = ctypes.c_void_p
        self._execute = library.td_json_client_execute
        self._execute.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        self._execute.restype = ctypes.c_void_p
        self._destroy = library.td_json_client_destroy
        self._destroy.argtypes = [ctypes.c_void_p]
        self._destroy.restype = None

    @classmethod
    def load(cls, path: Path) -> CtypesTdlibJsonAbi:
        try:
            # The verified absolute payload path is intentional: no `find_library`,
            # `DYLD_LIBRARY_PATH`, Homebrew, or user library lookup participates.
            library = ctypes.CDLL(str(Path(path).resolve()), mode=ctypes.RTLD_LOCAL)
        except OSError as exc:
            raise TelegramTdlibNativeError(
                "The managed TDLib library could not be loaded."
            ) from exc
        try:
            return cls(library)
        except AttributeError as exc:
            raise TelegramTdlibNativeError(
                "The managed TDLib library lacks the JSON C ABI."
            ) from exc

    def create_client(self) -> int:
        handle = self._create()
        if not handle:
            raise TelegramTdlibNativeError("TDLib could not create a client instance.")
        return int(handle)

    def send(self, handle: int, request: bytes) -> None:
        self._send(ctypes.c_void_p(handle), request)

    def receive(self, handle: int, timeout_seconds: float) -> bytes | None:
        pointer = self._receive(ctypes.c_void_p(handle), ctypes.c_double(timeout_seconds))
        # TDLib owns this memory and invalidates it on the next receive/execute
        # on this thread. Copy before returning control to asyncio.
        return ctypes.string_at(pointer) if pointer else None

    def execute(self, request: bytes) -> bytes | None:
        pointer = self._execute(None, request)
        return ctypes.string_at(pointer) if pointer else None

    def destroy_client(self, handle: int) -> None:
        self._destroy(ctypes.c_void_p(handle))


def _encode_request(request: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(dict(request), separators=_JSON_SEPARATORS, ensure_ascii=False).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as exc:
        raise TelegramTdlibNativeError("TDLib request serialization failed.") from exc


def _decode_response(raw: bytes, *, phase: str) -> dict[str, Any]:
    try:
        response = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TelegramTdlibNativeError(
            f"TDLib returned an invalid response during {phase}."
        ) from exc
    if not isinstance(response, dict) or not isinstance(response.get("@type"), str):
        raise TelegramTdlibNativeError(f"TDLib returned an invalid response during {phase}.")
    return response


def _raise_if_error(response: Mapping[str, Any], *, phase: str) -> None:
    if response.get("@type") == "error":
        code = response.get("code")
        error_name, retry_after_seconds = safe_error_metadata(response.get("message"))
        raise TelegramTdlibRequestError(
            code=code if isinstance(code, int) else None,
            phase=phase,
            error_name=error_name,
            retry_after_seconds=retry_after_seconds,
        )


def safe_error_metadata(raw_message: object) -> tuple[str | None, int | None]:
    """Classify only reviewed protocol errors, never retaining provider text."""
    if not isinstance(raw_message, str):
        return None, None
    if raw_message in _SAFE_TDLIB_ERROR_NAMES:
        return raw_message, None
    for pattern, name in (
        (_FLOOD_WAIT, "FLOOD_WAIT"),
        (_SLOWMODE_WAIT, "SLOWMODE_WAIT"),
        (_RETRY_AFTER, "FLOOD_WAIT"),
    ):
        matched = pattern.fullmatch(raw_message)
        if matched is None:
            continue
        seconds = int(matched.group("seconds"))
        if seconds <= _MAX_RETRY_AFTER_SECONDS:
            return name, seconds
    return None, None


def _execute_service_request(
    abi: TdlibJsonServiceAbi, request: Mapping[str, Any]
) -> dict[str, Any]:
    raw = abi.execute(_encode_request(request))
    if raw is None:
        raise TelegramTdlibNativeError("TDLib did not return a service response.")
    response = _decode_response(raw, phase="service initialization")
    _raise_if_error(response, phase="service initialization")
    return response


def load_managed_tdlib(*, runtime_root: Path) -> tuple[TelegramTdlibRuntime, CtypesTdlibJsonAbi]:
    """Verify and load one exact packaged library, including its native version."""
    runtime = verify_tdlib_runtime(runtime_root=runtime_root)
    abi = CtypesTdlibJsonAbi.load(runtime.library_path)
    version = _execute_service_request(abi, {"@type": "getOption", "name": "version"})
    if version.get("@type") != "optionValueString" or version.get("value") != runtime.tdlib_version:
        raise TelegramTdlibRuntimeError(
            "The managed TDLib library version doesn't match its manifest."
        )
    # TDLib logging is process-global. Disable its default stderr/OS stream before
    # creating clients so phone codes, bot tokens, proxy credentials, and message
    # payloads never enter daemon logs through the native library.
    _execute_service_request(
        abi,
        {"@type": "setLogStream", "log_stream": {"@type": "logStreamEmpty"}},
    )
    return runtime, abi


@dataclass
class TelegramTdlibClient:
    """One running TDLib client with a single ordered native receiver."""

    _abi: TdlibJsonAbi
    receive_timeout_seconds: float = 0.1

    def __post_init__(self) -> None:
        if self.receive_timeout_seconds <= 0:
            raise ValueError("receive_timeout_seconds must be positive")
        self._handle: int | None = None
        self._receiver: threading.Thread | None = None
        self._receiver_stop = threading.Event()
        self._updates: asyncio.Queue[dict[str, Any] | object] = asyncio.Queue()
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._state = "new"
        self._fatal_error: TelegramTdlibNativeError | None = None
        self._close_lock = asyncio.Lock()
        self._closed_event = asyncio.Event()
        self._destroyed_event = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        if self._state == "running":
            return
        if self._state != "new":
            raise TelegramTdlibClosedError("TDLib client can't be restarted after close.")
        self._handle = self._abi.create_client()
        self._state = "running"
        self._loop = asyncio.get_running_loop()
        self._receiver = threading.Thread(
            target=self._receive_loop_sync,
            name="telegram-tdlib-receiver",
            daemon=False,
        )
        self._receiver.start()

    async def _request(
        self,
        request: Mapping[str, Any],
        *,
        timeout_seconds: float = 30.0,
        allow_closing: bool = False,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Send an internal typed request and await its TDLib ``@extra`` response."""
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        response = self._begin_request(
            request, allow_closing=allow_closing, correlation_id=correlation_id
        )
        try:
            return await asyncio.wait_for(response, timeout=timeout_seconds)
        except TimeoutError as exc:
            raise TelegramTdlibNativeError("TDLib request timed out awaiting a response.") from exc
        finally:
            self._discard_pending(response)

    def _begin_request(
        self,
        request: Mapping[str, Any],
        *,
        allow_closing: bool = False,
        correlation_id: str | None = None,
    ) -> asyncio.Future[dict[str, Any]]:
        """Queue an internal request without awaiting a pre-initialization response."""
        if self._fatal_error is not None:
            raise self._fatal_error
        if self._state != "running" and not (allow_closing and self._state == "closing"):
            raise TelegramTdlibClosedError("TDLib client is not available for requests.")
        if self._handle is None:
            raise TelegramTdlibClosedError("TDLib client has no native handle.")
        if "@extra" in request:
            raise TelegramTdlibNativeError("TDLib request correlation is supplied separately.")
        extra = self._correlation_id(correlation_id)
        if extra in self._pending:
            raise TelegramTdlibNativeError("TDLib request correlation is already in use.")
        payload = dict(request)
        payload["@extra"] = extra
        loop = asyncio.get_running_loop()
        response: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[extra] = response
        self._abi.send(self._handle, _encode_request(payload))
        return response

    async def next_update(self) -> dict[str, Any]:
        update = await self._updates.get()
        if update is _TERMINAL_UPDATE:
            self._updates.put_nowait(_TERMINAL_UPDATE)
            if self._fatal_error is not None:
                raise self._fatal_error
            raise TelegramTdlibClosedError("TDLib client has closed.")
        assert isinstance(update, dict)
        return update

    async def close(self, *, timeout_seconds: float = 10.0) -> None:
        """Request close, observe TDLib's terminal state, then destroy its client."""
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        async with self._close_lock:
            if self._state == "closed":
                return
            if self._state == "new":
                self._state = "closed"
                return
            self._state = "closing"
            timed_out = False
            close_request: asyncio.Future[dict[str, Any]] | None = None
            try:
                # TDLib's terminal authorization update is the authoritative close
                # receipt. It can arrive before a separate close response, so don't
                # wait on that response and contend with orderly teardown.
                close_request = self._begin_request({"@type": "close"}, allow_closing=True)
                await asyncio.wait_for(self._closed_event.wait(), timeout=timeout_seconds)
            except TimeoutError:
                timed_out = True
            finally:
                if close_request is not None:
                    self._discard_pending(close_request)
                self._receiver_stop.set()
                try:
                    await asyncio.wait_for(
                        self._destroyed_event.wait(),
                        timeout=max(1.0, self.receive_timeout_seconds * 4),
                    )
                except TimeoutError as exc:
                    raise TelegramTdlibNativeError("TDLib receiver didn't retire safely.") from exc
                self._state = "closed"
            if timed_out:
                raise TelegramTdlibCloseTimeout(
                    "TDLib did not reach authorizationStateClosed in time."
                )

    def _receive_loop_sync(self) -> None:
        assert self._handle is not None
        try:
            while not self._receiver_stop.is_set():
                raw = self._abi.receive(self._handle, self.receive_timeout_seconds)
                if raw is None:
                    continue
                response = _decode_response(raw, phase="native receive")
                assert self._loop is not None
                self._loop.call_soon_threadsafe(self._dispatch_response, response)
                if _is_closed_authorization_update(response):
                    break
        except TelegramTdlibNativeError as exc:
            assert self._loop is not None
            self._loop.call_soon_threadsafe(self._dispatch_fatal, exc)
        except Exception:  # pragma: no cover - protects the task boundary
            error = TelegramTdlibNativeError("TDLib native receive failed.")
            assert self._loop is not None
            self._loop.call_soon_threadsafe(self._dispatch_fatal, error)
        finally:
            if self._handle is not None:
                self._abi.destroy_client(self._handle)
                self._handle = None
            assert self._loop is not None
            self._loop.call_soon_threadsafe(self._dispatch_destroyed)

    def _dispatch_response(self, response: dict[str, Any]) -> None:
        extra = response.get("@extra")
        pending = self._pending.get(extra) if isinstance(extra, str) else None
        if pending is not None:
            if not pending.done():
                if response.get("@type") == "error":
                    code = response.get("code")
                    error_name, retry_after_seconds = safe_error_metadata(response.get("message"))
                    pending.set_exception(
                        TelegramTdlibRequestError(
                            code=code if isinstance(code, int) else None,
                            phase="request",
                            error_name=error_name,
                            retry_after_seconds=retry_after_seconds,
                        )
                    )
                else:
                    pending.set_result(response)
            return
        try:
            _raise_if_error(response, phase="native receive")
        except TelegramTdlibNativeError as exc:
            self._dispatch_fatal(exc)
            return
        if _is_closed_authorization_update(response):
            self._closed_event.set()
        self._updates.put_nowait(response)

    def _dispatch_fatal(self, error: TelegramTdlibNativeError) -> None:
        self._fatal_error = error
        for pending in self._pending.values():
            if not pending.done():
                pending.set_exception(error)

    def _dispatch_destroyed(self) -> None:
        for pending in self._pending.values():
            if not pending.done():
                pending.set_exception(TelegramTdlibClosedError("TDLib client has closed."))
        self._pending.clear()
        self._destroyed_event.set()
        self._updates.put_nowait(_TERMINAL_UPDATE)

    def _discard_pending(self, response: asyncio.Future[dict[str, Any]]) -> None:
        for correlation_id, pending in tuple(self._pending.items()):
            if pending is response:
                self._pending.pop(correlation_id, None)
                return

    @staticmethod
    def _correlation_id(value: str | None) -> str:
        if value is None:
            return uuid.uuid4().hex
        if not isinstance(value, str) or not value or len(value) > 160:
            raise TelegramTdlibNativeError(
                "TDLib request correlation must be a non-empty value up to 160 characters."
            )
        return value


def _is_closed_authorization_update(response: Mapping[str, Any]) -> bool:
    return (
        response.get("@type") == "updateAuthorizationState"
        and isinstance(response.get("authorization_state"), Mapping)
        and response["authorization_state"].get("@type") == "authorizationStateClosed"
    )


__all__ = [
    "CtypesTdlibJsonAbi",
    "TdlibJsonAbi",
    "TelegramTdlibClient",
    "TelegramTdlibCloseTimeout",
    "TelegramTdlibClosedError",
    "TelegramTdlibNativeError",
    "TelegramTdlibRequestError",
    "load_managed_tdlib",
    "safe_error_metadata",
]
