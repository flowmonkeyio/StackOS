"""TDLib-backed Account authorization under the existing credential owner.

The native service owns one live TDLib client and update receiver per Account.
This repository owns every durable authorization fact: its generation, safe
challenge projection, encrypted TDLib database key, and canonical Telegram
identity.  Challenge answers and QR links are intentionally never durable.
"""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

import json
import os
import secrets
import stat
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager, suppress
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Literal, Protocol

from sqlalchemy import delete
from sqlmodel import col, select

from stackos.config import Settings
from stackos.db.models import Credential, CredentialAccount, IntegrationCredential
from stackos.integrations.telegram_tdlib.account_config import account_proxy
from stackos.integrations.telegram_tdlib.sessions import (
    TelegramTdlibSessionConfig,
)
from stackos.repositories.base import ConflictError, Envelope, ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository

from .schema import AccountAuthChallengeOut, AccountAuthStatusOut, AuthStartOut
from .telegram_application import TelegramApplicationRepository
from .utils import utcnow

_AUTH_CONFIG_KEY = "telegram_auth"
_DESIRED_CONNECTED_KEY = "telegram_desired_connected"
_DATABASE_KEY = "_tdlib_database_encryption_key"
_CHALLENGE_TTL_SECONDS = 600


def remove_telegram_local_data(*, settings: Settings, credential_ref: str) -> None:
    """Remove one retired Account's TDLib database and files without following links.

    Revocation and auth-identity edits call this after fencing/closing the live
    session. It also works on a retry after an interrupted cleanup.
    No remote Telegram logout is implied by local data removal.
    """
    _validate_telegram_storage_ref(credential_ref)
    native_root = settings.data_dir / "telegram-tdlib"
    if not native_root.exists() and not native_root.is_symlink():
        return
    if settings.data_dir.is_symlink() or native_root.is_symlink():
        raise ConflictError(
            "Telegram native storage has an unsafe symbolic-link boundary",
            data={
                "credential_ref": credential_ref,
                "next_action": (
                    "Repair the local Telegram storage path, then retry this Account action."
                ),
            },
        )
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        parent_fd = os.open(native_root, flags)
        try:
            _remove_telegram_storage_entry(parent_fd, credential_ref, root=True)
        finally:
            os.close(parent_fd)
    except ConflictError:
        raise
    except OSError as exc:
        raise ConflictError(
            "Telegram Account native local data cleanup failed",
            data={
                "credential_ref": credential_ref,
                "next_action": "Repair local storage, then retry this Account action.",
            },
        ) from exc


def _remove_telegram_storage_entry(parent_fd: int, name: str, *, root: bool = False) -> None:
    try:
        entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(entry.st_mode):
        if root:
            raise ConflictError(
                "Telegram Account native storage is a symbolic link",
                data={
                    "next_action": (
                        "Repair the local Telegram storage path, then retry this Account action."
                    )
                },
            )
        os.unlink(name, dir_fd=parent_fd)
        return
    if not stat.S_ISDIR(entry.st_mode):
        os.unlink(name, dir_fd=parent_fd)
        return
    child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        with os.scandir(child_fd) as entries:
            children = [item.name for item in entries]
        for child in children:
            _remove_telegram_storage_entry(child_fd, child)
    finally:
        os.close(child_fd)
    os.rmdir(name, dir_fd=parent_fd)


def _validate_telegram_storage_ref(credential_ref: str) -> None:
    if not credential_ref.startswith("cred_") or any(
        char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for char in credential_ref
    ):
        raise ValidationError("Telegram Account reference cannot select a native storage directory")


class _TelegramRuntime(Protocol):
    async def configure(
        self,
        *,
        account_ref: str,
        generation: int,
        config: TelegramTdlibSessionConfig,
        startup_timeout_seconds: float = 30.0,
        authorization_timeout_seconds: float = 30.0,
    ) -> dict[str, Any]: ...

    async def request(
        self,
        account_ref: str,
        request: Mapping[str, Any],
        *,
        generation: int | None = None,
        correlation_id: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]: ...

    async def request_authorization(
        self,
        *,
        account_ref: str,
        generation: int,
        request: Mapping[str, Any],
        correlation_id: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]: ...

    def bootstrap_receipt(self, *, account_ref: str, generation: int) -> Any: ...

    def state(self, *, account_ref: str, generation: int) -> dict[str, Any] | None: ...

    async def close(self, *, account_ref: str, generation: int) -> None: ...

    def account_transition(self, account_ref: str) -> AbstractAsyncContextManager[None]: ...

    def active_generation(self, *, account_ref: str) -> int | None: ...


def _serialized_telegram_transition(method: Any) -> Any:
    """Re-read durable Account state under its native transition lease."""

    @wraps(method)
    async def wrapped(
        self: TelegramAuthorizationMixin,
        *,
        credential_ref: str,
        runtime: _TelegramRuntime,
        **kwargs: Any,
    ) -> Any:
        async with runtime.account_transition(credential_ref):
            self._s.expire_all()
            return await method(self, credential_ref=credential_ref, runtime=runtime, **kwargs)

    return wrapped


class TelegramAuthorizationMixin:
    """Persist a safe native authorization state machine for provider ``telegram``."""

    async def start_telegram_authorization(
        self,
        *,
        credential_ref: str,
        runtime: _TelegramRuntime,
        settings: Settings,
        authorization_mode: Literal["phone", "qr"] = "phone",
        sign_in_only: bool = False,
        expected_connection_generation: int | None = None,
    ) -> Envelope[AuthStartOut]:
        """Configure TDLib and start the one supported bot or user auth path."""
        if authorization_mode not in {"phone", "qr"}:
            raise ValidationError("Telegram authorization mode must be phone or qr")
        async with runtime.account_transition(credential_ref):
            self._s.expire_all()
            credential, row, method, account_kind, payload = self._telegram_credential(
                credential_ref
            )
            current = self._telegram_state(credential)
            if expected_connection_generation is not None and (
                not self._telegram_desired_connected(credential)
                or (current["generation"] if current is not None else 0)
                != expected_connection_generation
            ):
                status = self.telegram_authorization_status(credential_ref=credential_ref)
                return Envelope(
                    data=AuthStartOut(
                        provider_key="telegram",
                        auth_type=credential.auth_type,
                        auth_method_key=credential.auth_method_key,
                        credential_ref=credential_ref,
                        status=status.status,
                        challenge=status.challenge,
                    )
                )
            self._require_shared_telegram_application(credential)
            if account_kind == "bot" and authorization_mode != "phone":
                raise ValidationError(
                    "QR authorization is only available for Telegram user Accounts"
                )

            # Local sign-in establishes saved TDLib authorization without
            # choosing a durable connection. Only account.session.connect does.
            if sign_in_only:
                if self._telegram_desired_connected(credential):
                    raise ConflictError(
                        "Disconnect the Telegram Account before starting local sign-in"
                    )
                self._set_telegram_desired_connected(
                    credential=credential, desired=False, commit=False
                )
            current = self._telegram_state(credential)
            if (
                current is not None
                and current["state"] == "pending"
                and self._telegram_runtime_is_connecting(
                    runtime=runtime,
                    credential_ref=credential_ref,
                    generation=current["generation"],
                )
            ):
                return Envelope(
                    data=AuthStartOut(
                        provider_key="telegram",
                        auth_type=credential.auth_type,
                        auth_method_key=credential.auth_method_key,
                        credential_ref=credential_ref,
                        status="pending",
                    )
                )
            if self._telegram_verified_identity(credential) is None:
                if runtime.active_generation(account_ref=credential_ref) is not None:
                    raise ConflictError(
                        "Cancel the current Telegram authorization before restarting sign-in"
                    )
                # A changed app or token must never resurrect the former
                # Account identity from TDLib's local database. Fresh initial
                # sign-in reaches the same no-op cleanup path.
                remove_telegram_local_data(settings=settings, credential_ref=credential_ref)
            generation = self._begin_telegram_authorization(credential=credential, row=row)
        try:
            payload = self._telegram_secret_payload(row=row)
            config = self._telegram_session_config(
                credential=credential,
                method=method,
                account_kind=account_kind,
                payload=payload,
                settings=settings,
            )
            state = await runtime.configure(
                account_ref=credential_ref,
                generation=generation,
                config=config,
            )
            self._s.expire_all()
            current = self._telegram_state(credential)
            if current is not None and current["generation"] == generation:
                self._save_proxy_bootstrap(
                    credential=credential,
                    runtime=runtime,
                    generation=generation,
                )
                state_type = state.get("@type")
                if state_type != "authorizationStateReady" and account_kind == "bot":
                    bot_token = payload.get("bot_token")
                    if not isinstance(bot_token, str) or not bot_token:
                        raise ConflictError("Telegram bot Account is missing its token")
                    state = await runtime.request_authorization(
                        account_ref=credential_ref,
                        generation=generation,
                        request={"@type": "checkAuthenticationBotToken", "token": bot_token},
                        correlation_id=f"telegram-auth:{credential_ref}:{generation}:bot-token",
                    )
                elif state_type != "authorizationStateReady" and authorization_mode == "qr":
                    state = await runtime.request_authorization(
                        account_ref=credential_ref,
                        generation=generation,
                        request={"@type": "requestQrCodeAuthentication", "other_user_ids": []},
                        correlation_id=f"telegram-auth:{credential_ref}:{generation}:qr",
                    )
            self._s.expire_all()
        except Exception:
            self._record_telegram_repair(
                credential=credential,
                generation=generation,
                repair_hint=(
                    "Telegram authorization could not be started. Review the Account setup."
                ),
            )
            with suppress(Exception):
                await runtime.close(account_ref=credential_ref, generation=generation)
            raise

        current = self._telegram_state(credential)
        if current is None or current["generation"] != generation:
            # Disconnect/cancel may have fenced startup before configure opened
            # its client. Retire only the stale generation; never touch a later
            # explicit Connect that may already own this Account.
            with suppress(Exception):
                await runtime.close(account_ref=credential_ref, generation=generation)
            status = self.telegram_authorization_status(credential_ref=credential_ref)
        else:
            status = self.record_telegram_authorization_state(
                credential_ref=credential_ref,
                generation=generation,
                state=state,
                include_qr_link=True,
            )
            if status.status == "verifying":
                await self.synchronize_telegram_ready(
                    credential_ref=credential_ref,
                    generation=generation,
                    runtime=runtime,
                )
                status = self.telegram_authorization_status(credential_ref=credential_ref)
            if sign_in_only:
                status = await self.finish_telegram_sign_in_if_ready(
                    credential_ref=credential_ref,
                    generation=generation,
                    runtime=runtime,
                )
        return Envelope(
            data=AuthStartOut(
                provider_key="telegram",
                auth_type=credential.auth_type,
                auth_method_key=credential.auth_method_key,
                credential_ref=credential_ref,
                status=status.status,
                challenge=status.challenge,
            )
        )

    @_serialized_telegram_transition
    async def submit_telegram_authorization(
        self,
        *,
        credential_ref: str,
        generation: int,
        answer: Mapping[str, Any],
        runtime: _TelegramRuntime,
    ) -> Envelope[AccountAuthStatusOut]:
        """Send one write-only answer for the current native challenge only."""
        credential, _row, _method, _account_kind, _payload = self._telegram_credential(
            credential_ref
        )
        current = self._telegram_state(credential)
        if current is None or current["generation"] != generation:
            raise ConflictError("Telegram authorization generation is stale")
        if current["state"] != "challenge" or current["challenge_kind"] is None:
            raise ConflictError("Telegram Account has no answerable authorization challenge")
        expires_at = current["expires_at"]
        if expires_at is not None and utcnow() >= expires_at:
            self._record_telegram_repair(
                credential=credential,
                generation=generation,
                repair_hint="Telegram authorization challenge expired. Start authorization again.",
            )
            raise ConflictError("Telegram authorization challenge expired")
        request = self._challenge_request(kind=current["challenge_kind"], answer=answer)
        state = await runtime.request_authorization(
            account_ref=credential_ref,
            generation=generation,
            request=request,
            correlation_id=f"telegram-auth:{credential_ref}:{generation}:{current['challenge_kind']}",
        )
        self._s.expire_all()
        status = self.record_telegram_authorization_state(
            credential_ref=credential_ref,
            generation=generation,
            state=state,
            include_qr_link=False,
        )
        if status.status == "verifying":
            await self.synchronize_telegram_ready(
                credential_ref=credential_ref,
                generation=generation,
                runtime=runtime,
            )
            status = self.telegram_authorization_status(credential_ref=credential_ref)
        if not self._telegram_desired_connected(credential):
            status = await self.finish_telegram_sign_in_if_ready(
                credential_ref=credential_ref,
                generation=generation,
                runtime=runtime,
            )
        return Envelope(data=status)

    @_serialized_telegram_transition
    async def finish_telegram_sign_in_if_ready(
        self,
        *,
        credential_ref: str,
        generation: int,
        runtime: _TelegramRuntime,
    ) -> AccountAuthStatusOut:
        """Close a completed local login while retaining its encrypted TDLib DB."""
        credential, _row, _method, _kind = self._telegram_account_record(credential_ref)
        current = self._telegram_state(credential)
        if (
            current is None
            or current["generation"] != generation
            or current["state"] != "connected"
            or self._telegram_desired_connected(credential)
        ):
            return self.telegram_authorization_status(credential_ref=credential_ref)
        self._write_telegram_state(
            credential=credential,
            generation=generation + 1,
            state="disconnected",
            challenge=None,
            repair_hint=None,
        )
        self._s.commit()
        await runtime.close(account_ref=credential_ref, generation=generation)
        return self.telegram_authorization_status(credential_ref=credential_ref)

    async def resume_telegram_session(
        self,
        *,
        credential_ref: str,
        runtime: _TelegramRuntime,
        settings: Settings,
        expected_connection_generation: int | None = None,
    ) -> Envelope[AccountAuthStatusOut]:
        """Restore one persisted TDLib session without replaying an auth command.

        Daemon startup calls this only for Accounts explicitly left connected. TDLib's
        encrypted database determines whether it restores Ready or an existing
        challenge; this operation only persists that resulting safe state.
        """
        async with runtime.account_transition(credential_ref):
            self._s.expire_all()
            credential, row, method, _account_kind, _payload = self._telegram_credential(
                credential_ref
            )
            current = self._telegram_state(credential)
            if expected_connection_generation is not None and (
                not self._telegram_desired_connected(credential)
                or (current["generation"] if current is not None else 0)
                != expected_connection_generation
            ):
                return Envelope(
                    data=self.telegram_authorization_status(credential_ref=credential_ref)
                )
            self._require_shared_telegram_application(credential)
            if self._telegram_verified_identity(credential) is None:
                remove_telegram_local_data(settings=settings, credential_ref=credential_ref)
            generation = self._begin_telegram_authorization(credential=credential, row=row)
        try:
            payload = self._telegram_secret_payload(row=row)
            config = self._telegram_session_config(
                credential=credential,
                method=method,
                account_kind=_account_kind,
                payload=payload,
                settings=settings,
            )
            state = await runtime.configure(
                account_ref=credential_ref,
                generation=generation,
                config=config,
            )
            self._s.expire_all()
            self._save_proxy_bootstrap(
                credential=credential,
                runtime=runtime,
                generation=generation,
            )
        except Exception:
            self._record_telegram_repair(
                credential=credential,
                generation=generation,
                repair_hint="Telegram session could not be restored. Review the Account setup.",
            )
            with suppress(Exception):
                await runtime.close(account_ref=credential_ref, generation=generation)
            raise
        current = self._telegram_state(credential)
        if current is None or current["generation"] != generation:
            with suppress(Exception):
                await runtime.close(account_ref=credential_ref, generation=generation)
            return Envelope(data=self.telegram_authorization_status(credential_ref=credential_ref))
        status = self.record_telegram_authorization_state(
            credential_ref=credential_ref,
            generation=generation,
            state=state,
        )
        if status.status == "verifying":
            await self.synchronize_telegram_ready(
                credential_ref=credential_ref,
                generation=generation,
                runtime=runtime,
            )
            status = self.telegram_authorization_status(credential_ref=credential_ref)
        return Envelope(data=status)

    async def connect_telegram_session(
        self,
        *,
        credential_ref: str,
        runtime: _TelegramRuntime,
        settings: Settings,
    ) -> Envelope[AccountAuthStatusOut]:
        """Honor one explicit Account connection request for either Account kind.

        A bot can complete its first authorization with its saved daemon-held
        token.  A user must complete its first login through local Accounts;
        agent connection only opens the encrypted TDLib session and returns a
        safe status.  Existing databases resume without replaying login input.
        """
        async with runtime.account_transition(credential_ref):
            self._s.expire_all()
            credential, _row, _method, account_kind, _payload = self._telegram_credential(
                credential_ref
            )
            self._require_shared_telegram_application(credential)
            current = self._telegram_state(credential)
            if not self._telegram_desired_connected(credential) and (
                runtime.active_generation(account_ref=credential_ref) is not None
                or (
                    current is not None
                    and self._telegram_runtime_is_connecting(
                        runtime=runtime,
                        credential_ref=credential_ref,
                        generation=current["generation"],
                    )
                )
            ):
                raise ConflictError(
                    "Finish or cancel local Telegram sign-in before connecting the Account"
                )
            if (
                self._telegram_desired_connected(credential)
                and current is not None
                and (
                    (
                        current["state"] == "pending"
                        and self._telegram_runtime_is_connecting(
                            runtime=runtime,
                            credential_ref=credential_ref,
                            generation=current["generation"],
                        )
                    )
                    or (
                        current["state"] in {"challenge", "verifying", "connected"}
                        and self._telegram_runtime_is_live(
                            runtime=runtime,
                            credential_ref=credential_ref,
                            generation=current["generation"],
                        )
                    )
                )
            ):
                return Envelope(
                    data=self.telegram_authorization_status(credential_ref=credential_ref)
                )

            self._set_telegram_desired_connected(credential=credential, desired=True)
            # Carry the accepted intent across the next transition lease. A
            # later Disconnect advances this generation even if another Connect
            # subsequently sets desired_connected back to True.
            connection_generation = current["generation"] if current is not None else 0
        # A bot has all login input in daemon-held storage.  Starting the bot
        # authorization checks the restored TDLib state before sending a token.
        if account_kind == "bot":
            await self.start_telegram_authorization(
                credential_ref=credential_ref,
                runtime=runtime,
                settings=settings,
                expected_connection_generation=connection_generation,
            )
            return Envelope(data=self.telegram_authorization_status(credential_ref=credential_ref))
        return await self.resume_telegram_session(
            credential_ref=credential_ref,
            runtime=runtime,
            settings=settings,
            expected_connection_generation=connection_generation,
        )

    @_serialized_telegram_transition
    async def disconnect_telegram_session(
        self,
        *,
        credential_ref: str,
        runtime: _TelegramRuntime,
    ) -> Envelope[AccountAuthStatusOut]:
        """Persist an operator stop, fence late updates, then retire TDLib."""
        credential, _row, _method, _kind = self._telegram_account_record(credential_ref)
        current = self._telegram_state(credential)
        if not self._telegram_desired_connected(credential) and (
            current is None or current["state"] == "disconnected"
        ):
            return Envelope(data=self.telegram_authorization_status(credential_ref=credential_ref))
        old_generation = current["generation"] if current is not None else None
        self._set_telegram_desired_connected(credential=credential, desired=False, commit=False)
        self._write_telegram_state(
            credential=credential,
            generation=(old_generation or 0) + 1,
            state="disconnected",
            challenge=None,
            repair_hint=None,
        )
        self._s.commit()
        if old_generation is not None:
            # A late callback sees the new generation and cannot re-enable
            # ingress or delivery.  The native close still runs on the old one.
            await runtime.close(account_ref=credential_ref, generation=old_generation)
        return Envelope(data=self.telegram_authorization_status(credential_ref=credential_ref))

    def get_telegram_session_status(
        self,
        *,
        credential_ref: str,
        runtime: _TelegramRuntime | None = None,
    ) -> dict[str, Any]:
        """Return only agent-safe connection state, never login challenges."""
        credential, _row, _method, _kind = self._telegram_account_record(credential_ref)
        desired = self._telegram_desired_connected(credential)
        current = self._telegram_state(credential)
        if (credential.config_json or {}).get("telegram_application_conflict") is True:
            return {
                "desired_connected": False,
                "connected": False,
                "status": "repair-required",
                "next_action": "Recreate this Account under the shared Telegram application.",
            }
        live = current is not None and self._telegram_runtime_is_live(
            runtime=runtime,
            credential_ref=credential_ref,
            generation=current["generation"],
        )
        connected = bool(desired and live and current and current["state"] == "connected")
        if not desired:
            status = "disconnected"
            next_action = "Call account.session.connect when this Account is needed."
        elif current is not None and current["state"] == "repair-required":
            status = "repair-required"
            next_action = "Review the Telegram Account setup in local Accounts."
        elif connected:
            status = "connected"
            next_action = None
        elif current is not None and current["state"] == "challenge":
            status = "authorization_required"
            next_action = "Complete Telegram user authorization in local Accounts."
        else:
            status = "connecting"
            next_action = (
                "Check the Telegram Account status and complete local authorization if needed."
            )
        return {
            "desired_connected": desired,
            "connected": connected,
            "status": status,
            "next_action": next_action,
        }

    @staticmethod
    def _telegram_desired_connected(credential: Credential) -> bool:
        return (credential.config_json or {}).get(_DESIRED_CONNECTED_KEY) is True

    @staticmethod
    def _require_shared_telegram_application(credential: Credential) -> None:
        if (credential.config_json or {}).get("telegram_application_conflict") is True:
            raise ConflictError(
                "Telegram Account used a different application identity",
                data={
                    "credential_ref": credential.credential_ref,
                    "next_action": "Recreate this Account under the shared Telegram application.",
                },
            )

    def _set_telegram_desired_connected(
        self, *, credential: Credential, desired: bool, commit: bool = True
    ) -> None:
        config = dict(credential.config_json or {})
        if config.get(_DESIRED_CONNECTED_KEY) is desired:
            return
        config[_DESIRED_CONNECTED_KEY] = desired
        credential.config_json = self._safe_config(config)
        credential.updated_at = utcnow()
        self._s.add(credential)
        if commit:
            self._s.commit()

    def _invalidate_telegram_saved_authorization(self, credential: Credential) -> None:
        """Fence the old app/token identity before replacing its native database."""
        current = self._telegram_state(credential)
        generation = (current["generation"] if current is not None else 0) + 1
        self._set_telegram_desired_connected(credential=credential, desired=False, commit=False)
        self._write_telegram_state(
            credential=credential,
            generation=generation,
            state="pending",
            challenge=None,
            repair_hint=None,
        )
        config = dict(credential.config_json or {})
        config.pop("tdlib_proxy_id", None)
        credential.config_json = self._safe_config(config)
        if credential.id is not None:
            self._s.exec(
                delete(CredentialAccount).where(
                    col(CredentialAccount.credential_id) == credential.id
                )
            )

    @staticmethod
    def _telegram_runtime_is_live(
        *, runtime: _TelegramRuntime | None, credential_ref: str, generation: int
    ) -> bool:
        if runtime is None:
            return False
        state = getattr(runtime, "state", None)
        if not callable(state):
            # Minimal injected test runtimes predate the native state query.
            return True
        try:
            return state(account_ref=credential_ref, generation=generation) is not None
        except Exception:
            return False

    @staticmethod
    def _telegram_runtime_is_connecting(
        *, runtime: _TelegramRuntime, credential_ref: str, generation: int
    ) -> bool:
        check = getattr(runtime, "is_connecting", None)
        return bool(callable(check) and check(account_ref=credential_ref, generation=generation))

    async def synchronize_telegram_ready(
        self,
        *,
        credential_ref: str,
        generation: int,
        runtime: _TelegramRuntime,
    ) -> None:
        """Confirm canonical Telegram identity for one current Ready generation."""
        credential, _row, _method, account_kind, payload = self._telegram_credential(credential_ref)
        current = self._telegram_state(credential)
        if current is None or current["generation"] != generation:
            return
        if current["state"] != "verifying":
            return
        try:
            await self._synchronize_telegram_identity(
                credential=credential,
                runtime=runtime,
                generation=generation,
                account_kind=account_kind,
                expected_bot_id=_telegram_bot_subject(payload) if account_kind == "bot" else None,
            )
            self._write_telegram_state(
                credential=credential,
                generation=generation,
                state="connected",
                challenge=None,
                repair_hint=None,
            )
            self.record_usage_event(
                credential=credential,
                provider_key="telegram",
                operation="account.authorization.ready",
                status="ok",
                metadata_json={"generation": generation},
            )
            self._s.commit()
        except Exception:
            self._record_telegram_repair(
                credential=credential,
                generation=generation,
                repair_hint=(
                    "Telegram Account identity could not be confirmed. Review the Account setup."
                ),
            )
            with suppress(Exception):
                await runtime.close(account_ref=credential_ref, generation=generation)
            raise

    @_serialized_telegram_transition
    async def cancel_telegram_authorization(
        self,
        *,
        credential_ref: str,
        generation: int,
        runtime: _TelegramRuntime,
    ) -> Envelope[AccountAuthStatusOut]:
        """Fence the active generation before closing its native session."""
        credential, _row, _method, _kind, _payload = self._telegram_credential(credential_ref)
        current = self._telegram_state(credential)
        if current is None or current["generation"] != generation:
            raise ConflictError("Telegram authorization generation is stale")
        next_generation = generation + 1
        self._set_telegram_desired_connected(credential=credential, desired=False, commit=False)
        self._write_telegram_state(
            credential=credential,
            generation=next_generation,
            state="pending",
            challenge=None,
            repair_hint=None,
        )
        self._s.commit()
        # Closing is best effort after the durable generation fence. A stale
        # receiver can no longer mutate this Account's newly pending state.
        with suppress(Exception):
            await runtime.close(account_ref=credential_ref, generation=generation)
        return Envelope(data=self.telegram_authorization_status(credential_ref=credential_ref))

    def telegram_authorization_status(self, *, credential_ref: str) -> AccountAuthStatusOut:
        """Project only safe, durable native authorization state to local admin."""
        credential, _row, _method, _kind = self._telegram_account_record(credential_ref)
        current = self._telegram_state(credential)
        if current is None:
            return AccountAuthStatusOut(
                credential_ref=credential_ref,
                provider_key="telegram",
                status=credential.status,
            )
        challenge = self._challenge_out(current, include_qr_link=False)
        return AccountAuthStatusOut(
            credential_ref=credential_ref,
            provider_key="telegram",
            status=current["state"],
            generation=current["generation"],
            challenge=challenge,
            updated_at=current["updated_at"],
            repair_hint=current["repair_hint"],
        )

    def record_telegram_authorization_state(
        self,
        *,
        credential_ref: str,
        generation: int,
        state: Mapping[str, Any],
        include_qr_link: bool = False,
    ) -> AccountAuthStatusOut:
        """Persist a TDLib update only when its generation is still current.

        Server-owned update sinks call this method with a fresh database Session.
        It deliberately accepts raw TDLib input but persists only a narrow,
        documented challenge projection.
        """
        credential, _row, _method, _kind = self._telegram_account_record(credential_ref)
        current = self._telegram_state(credential)
        if current is None or current["generation"] != generation:
            return self.telegram_authorization_status(credential_ref=credential_ref)
        projection = self._authorization_projection(state, include_qr_link=include_qr_link)
        self._write_telegram_state(
            credential=credential,
            generation=generation,
            state=projection["status"],
            challenge=projection["challenge"],
            repair_hint=projection["repair_hint"],
        )
        self.record_usage_event(
            credential=credential,
            provider_key="telegram",
            operation="account.authorization.update",
            status=projection["status"],
            metadata_json={
                "generation": generation,
                "authorization_state": projection["state_type"],
                "challenge_kind": projection["challenge"]["kind"]
                if projection["challenge"] is not None
                else None,
            },
        )
        self._s.commit()
        status = self.telegram_authorization_status(credential_ref=credential_ref)
        if include_qr_link and projection["qr_link"] is not None and status.challenge is not None:
            status.challenge.qr_link = projection["qr_link"]
        return status

    async def test_telegram_authorization(
        self,
        *,
        credential_ref: str,
        runtime: _TelegramRuntime,
        settings: Settings,
        project_id: int | None,
    ) -> Envelope[Any]:
        """Probe saved TDLib authorization, then leave an offline Account offline."""
        async with runtime.account_transition(credential_ref):
            self._s.expire_all()
            return await self._test_telegram_authorization_locked(
                credential_ref=credential_ref,
                runtime=runtime,
                settings=settings,
                project_id=project_id,
            )

    async def _test_telegram_authorization_locked(
        self,
        *,
        credential_ref: str,
        runtime: _TelegramRuntime,
        settings: Settings,
        project_id: int | None,
    ) -> Envelope[Any]:
        from .schema import AuthTestOut

        credential, _row, method, account_kind, payload = self._telegram_credential(credential_ref)
        current = self._telegram_state(credential)
        saved_identity = self._telegram_verified_identity(credential)
        desired_connected = self._telegram_desired_connected(credential)
        live = bool(
            current is not None
            and current["state"] == "connected"
            and self._telegram_runtime_is_live(
                runtime=runtime,
                credential_ref=credential_ref,
                generation=current["generation"],
            )
        )
        active_generation = runtime.active_generation(account_ref=credential_ref)
        close_generation: int | None = None
        probe_generation: int | None = None
        if current is not None and current["state"] == "repair-required":
            out = AuthTestOut(
                credential_ref=credential_ref,
                provider_key="telegram",
                ok=False,
                status="failed",
                summary="Telegram saved authorization needs repair.",
                checked_at=utcnow().isoformat(),
                next_action="Reauthorize this Telegram Account in local Accounts.",
            )
        elif current is None or saved_identity is None:
            out = AuthTestOut(
                credential_ref=credential_ref,
                provider_key="telegram",
                ok=False,
                status="pending",
                summary="Telegram Account has not completed sign-in.",
                checked_at=utcnow().isoformat(),
                next_action="Complete Telegram sign-in in local Accounts.",
            )
        elif current["state"] in {"pending", "challenge", "verifying"}:
            out = AuthTestOut(
                credential_ref=credential_ref,
                provider_key="telegram",
                ok=False,
                status="pending",
                summary="Telegram Account sign-in is still in progress.",
                checked_at=utcnow().isoformat(),
                retryable=True,
                next_action="Complete Telegram sign-in in local Accounts, then test again.",
            )
        elif active_generation is not None and not live:
            # Disconnect/cancel persists its generation fence before retiring
            # the old client. Never replace that still-live client with a test.
            out = AuthTestOut(
                credential_ref=credential_ref,
                provider_key="telegram",
                ok=False,
                status="pending",
                summary="Telegram Account session is changing.",
                checked_at=utcnow().isoformat(),
                retryable=True,
                next_action="Retry the Account test after the session change finishes.",
            )
        elif desired_connected and not live:
            # A Test must not replace an explicitly connected session that may
            # be starting or recovering under account.session.connect.
            out = AuthTestOut(
                credential_ref=credential_ref,
                provider_key="telegram",
                ok=False,
                status="failed",
                summary="Telegram Account session is not currently available.",
                checked_at=utcnow().isoformat(),
                retryable=True,
                next_action="Connect this Telegram Account, then test again.",
            )
        else:
            try:
                ready = True
                if live:
                    probe_generation = current["generation"]
                    if not desired_connected:
                        close_generation = probe_generation
                else:
                    # The encrypted TDLib database and daemon-held key are the
                    # canonical saved session. A throwaway generation cannot
                    # consume or persist a new login challenge through the
                    # normal authorization update sink.
                    # A temporary probe must never share the next durable
                    # generation with a concurrent explicit Connect. Keep
                    # nonces inside SQLite's signed 64-bit integer range.
                    probe_generation = secrets.randbits(61) | (1 << 61)
                    config = self._telegram_session_config(
                        credential=credential,
                        method=method,
                        account_kind=account_kind,
                        payload=payload,
                        settings=settings,
                    )
                    close_generation = probe_generation
                    state = await runtime.configure(
                        account_ref=credential_ref,
                        generation=probe_generation,
                        config=config,
                    )
                    if state.get("@type") != "authorizationStateReady":
                        ready = False
                        self._record_telegram_repair(
                            credential=credential,
                            generation=current["generation"],
                            repair_hint=(
                                "Telegram saved session is no longer authorized. "
                                "Reauthorize this Account."
                            ),
                        )
                        out = AuthTestOut(
                            credential_ref=credential_ref,
                            provider_key="telegram",
                            ok=False,
                            status="failed",
                            summary="Telegram saved session is no longer authorized.",
                            checked_at=utcnow().isoformat(),
                            next_action="Reauthorize this Telegram Account in local Accounts.",
                        )
                if ready:
                    user = await runtime.request(
                        credential_ref,
                        {"@type": "getMe"},
                        generation=probe_generation,
                        correlation_id=f"telegram-test:{credential_ref}:{probe_generation}:get-me",
                    )
                    self._synchronize_telegram_identity_from_user(
                        credential=credential,
                        user=user,
                        account_kind=account_kind,
                        expected_bot_id=_telegram_bot_subject(payload)
                        if account_kind == "bot"
                        else None,
                    )
                    receipt = runtime.bootstrap_receipt(
                        account_ref=credential_ref,
                        generation=probe_generation,
                    )
                    out = AuthTestOut(
                        credential_ref=credential_ref,
                        provider_key="telegram",
                        ok=True,
                        status="ok",
                        summary="Telegram saved session is authorized and responsive.",
                        checked_at=utcnow().isoformat(),
                        metadata={
                            "account_kind": account_kind,
                            "provider_account_id": str(user["id"]),
                            "username": _telegram_username(user),
                            "proxy_configured": bool(getattr(receipt, "proxy_enabled", False)),
                        },
                    )
            except ConflictError:
                self._record_telegram_repair(
                    credential=credential,
                    generation=current["generation"],
                    repair_hint=(
                        "Telegram saved session identity changed or its local data is unavailable. "
                        "Reauthorize this Account."
                    ),
                )
                out = AuthTestOut(
                    credential_ref=credential_ref,
                    provider_key="telegram",
                    ok=False,
                    status="failed",
                    summary="Telegram saved session could not be verified.",
                    checked_at=utcnow().isoformat(),
                    next_action="Reauthorize this Telegram Account in local Accounts.",
                )
            except Exception:
                out = AuthTestOut(
                    credential_ref=credential_ref,
                    provider_key="telegram",
                    ok=False,
                    status="failed",
                    summary="Telegram native session could not complete its safe account probe.",
                    checked_at=utcnow().isoformat(),
                    retryable=True,
                    next_action="Check Telegram connectivity and retry this Account test.",
                )
            finally:
                if close_generation is not None:
                    try:
                        if live:
                            await self.finish_telegram_sign_in_if_ready(
                                credential_ref=credential_ref,
                                generation=close_generation,
                                runtime=runtime,
                            )
                        else:
                            await runtime.close(
                                account_ref=credential_ref, generation=close_generation
                            )
                    except Exception:
                        if self._telegram_runtime_is_live(
                            runtime=runtime,
                            credential_ref=credential_ref,
                            generation=close_generation,
                        ):
                            out = AuthTestOut(
                                credential_ref=credential_ref,
                                provider_key="telegram",
                                ok=False,
                                status="failed",
                                summary=(
                                    "Telegram Account test could not close its temporary session."
                                ),
                                checked_at=utcnow().isoformat(),
                                retryable=True,
                                next_action="Restart the local Telegram runtime, then test again.",
                            )
        checked_at = utcnow()
        out.checked_at = checked_at.isoformat()
        credential.last_tested_at = checked_at
        self.record_usage_event(
            credential=credential,
            provider_key="telegram",
            operation="account.test",
            status=out.status,
            metadata_json={"ok": out.ok, "metadata": out.metadata, "result": out.model_dump()},
            project_id=project_id,
        )
        self._s.add(credential)
        self._s.commit()
        return Envelope(data=out, project_id=project_id)

    def _telegram_verified_identity(self, credential: Credential) -> str | None:
        if credential.id is None:
            return None
        account = self._s.exec(
            select(CredentialAccount).where(CredentialAccount.credential_id == credential.id)
        ).first()
        if account is None or not account.provider_account_id:
            return None
        return account.provider_account_id

    def _telegram_credential(
        self, credential_ref: str
    ) -> tuple[Credential, IntegrationCredential, Any, Literal["bot", "user"], dict[str, str]]:
        credential, row, method, account_kind = self._telegram_account_record(credential_ref)
        payload = self._telegram_secret_payload(row=row)
        return credential, row, method, account_kind, payload

    def _telegram_account_record(
        self, credential_ref: str
    ) -> tuple[Credential, IntegrationCredential, Any, Literal["bot", "user"]]:
        credential, row = self._global_credential(credential_ref)
        if credential.provider_key != "telegram":
            raise ValidationError("Account is not a Telegram native Account")
        provider = self._get_provider("telegram")
        assert provider is not None
        method = self._get_auth_method(provider, credential.auth_method_key)
        assert method is not None
        config = method.config or {}
        if config.get("native_authorization") is not True or config.get("account_kind") not in {
            "bot",
            "user",
        }:
            raise ValidationError("Telegram auth method does not support native authorization")
        return credential, row, method, config["account_kind"]

    def _telegram_secret_payload(self, *, row: IntegrationCredential) -> dict[str, str]:
        assert row.id is not None
        raw = IntegrationCredentialRepository(self._s).get_decrypted(row.id)
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConflictError("Telegram Account secret payload is unavailable") from exc
        if not isinstance(decoded, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in decoded.items()
        ):
            raise ConflictError("Telegram Account secret payload is invalid")
        return dict(decoded)

    def _begin_telegram_authorization(
        self, *, credential: Credential, row: IntegrationCredential
    ) -> int:
        current = self._telegram_state(credential)
        # A second explicit connect while the same bootstrap is still pending
        # joins its generation; the native service has one receiver per Account.
        generation = (
            current["generation"]
            if current is not None
            and current["state"] == "pending"
            and self._telegram_desired_connected(credential)
            else (current["generation"] if current is not None else 0) + 1
        )
        payload = self._telegram_secret_payload(row=row)
        if _DATABASE_KEY not in payload:
            payload[_DATABASE_KEY] = secrets.token_urlsafe(48)
            assert row.id is not None
            IntegrationCredentialRepository(self._s).set(
                credential_ref=credential.credential_ref,
                provider_key="telegram",
                integration_credential_id=row.id,
                secret_payload=json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(),
                commit=False,
            )
        self._write_telegram_state(
            credential=credential,
            generation=generation,
            state="pending",
            challenge=None,
            repair_hint=None,
        )
        self._s.commit()
        return generation

    def _telegram_session_config(
        self,
        *,
        credential: Credential,
        method: Any,
        account_kind: Literal["bot", "user"],
        payload: Mapping[str, str],
        settings: Settings,
    ) -> TelegramTdlibSessionConfig:
        safe_config = dict(credential.config_json or {})
        if safe_config.get("telegram_application_conflict") is True:
            raise ConflictError(
                "Telegram Account used a different application identity",
                data={
                    "credential_ref": credential.credential_ref,
                    "next_action": "Recreate this Account under the shared Telegram application.",
                },
            )
        application = TelegramApplicationRepository(self._s).get()
        key = payload.get(_DATABASE_KEY)
        if not isinstance(key, str) or not key:
            raise ConflictError("Telegram Account database key is unavailable")
        root = self._telegram_account_directory(
            settings=settings, credential_ref=credential.credential_ref
        )
        persisted_proxy_id = safe_config.get("tdlib_proxy_id")
        if not isinstance(persisted_proxy_id, int) or isinstance(persisted_proxy_id, bool):
            persisted_proxy_id = None
        return TelegramTdlibSessionConfig(
            account_kind=account_kind,
            application=application,
            database_directory=root / "database",
            files_directory=root / "files",
            database_encryption_key=key,
            proxy=account_proxy(safe_config, dict(payload)),
            persisted_proxy_id=persisted_proxy_id,
        )

    def _telegram_account_directory(self, *, settings: Settings, credential_ref: str) -> Path:
        _validate_telegram_storage_ref(credential_ref)
        if settings.data_dir.is_symlink() or (settings.data_dir / "telegram-tdlib").is_symlink():
            raise ConflictError("Telegram native storage has an unsafe symbolic-link boundary")
        root = settings.data_dir / "telegram-tdlib" / credential_ref
        for directory in (root, root / "database", root / "files"):
            if directory.is_symlink():
                raise ConflictError("Telegram native storage has an unsafe symbolic-link boundary")
            directory.mkdir(parents=True, exist_ok=True)
            if directory.is_symlink():
                raise ConflictError("Telegram native storage has an unsafe symbolic-link boundary")
            os.chmod(directory, 0o700)
        return root

    def _save_proxy_bootstrap(
        self,
        *,
        credential: Credential,
        runtime: _TelegramRuntime,
        generation: int,
    ) -> None:
        current = self._telegram_state(credential)
        if current is None or current["generation"] != generation:
            return
        receipt = runtime.bootstrap_receipt(
            account_ref=credential.credential_ref, generation=generation
        )
        config = dict(credential.config_json or {})
        proxy_id = getattr(receipt, "proxy_id", None)
        if isinstance(proxy_id, int) and not isinstance(proxy_id, bool) and proxy_id > 0:
            config["tdlib_proxy_id"] = proxy_id
        else:
            config.pop("tdlib_proxy_id", None)
        credential.config_json = self._safe_config(config)
        self._s.add(credential)
        self._s.commit()

    def _telegram_state(self, credential: Credential) -> dict[str, Any] | None:
        raw = (credential.config_json or {}).get(_AUTH_CONFIG_KEY)
        if not isinstance(raw, Mapping):
            return None
        generation = raw.get("generation")
        state = raw.get("state")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation <= 0:
            return None
        if state not in {
            "pending",
            "challenge",
            "verifying",
            "connected",
            "disconnected",
            "repair-required",
        }:
            return None
        updated_at = _parse_time(raw.get("updated_at"))
        if updated_at is None:
            return None
        expires_at = _parse_time(raw.get("challenge_expires_at"))
        challenge_kind = raw.get("challenge_kind") if state == "challenge" else None
        if challenge_kind not in _CHALLENGE_FIELDS:
            challenge_kind = None
        metadata = raw.get("challenge_metadata")
        return {
            "generation": generation,
            "state": state,
            "updated_at": updated_at,
            "expires_at": expires_at,
            "challenge_kind": challenge_kind,
            "challenge_metadata": _safe_challenge_metadata(metadata),
            "repair_hint": _safe_repair_hint(raw.get("repair_hint")),
        }

    def _write_telegram_state(
        self,
        *,
        credential: Credential,
        generation: int,
        state: Literal[
            "pending", "challenge", "verifying", "connected", "disconnected", "repair-required"
        ],
        challenge: dict[str, Any] | None,
        repair_hint: str | None,
    ) -> None:
        config = dict(credential.config_json or {})
        current: dict[str, Any] = {
            "generation": generation,
            "state": state,
            "updated_at": utcnow().isoformat(),
        }
        if challenge is not None:
            current.update(
                {
                    "challenge_kind": challenge["kind"],
                    "challenge_expires_at": challenge["expires_at"].isoformat(),
                    "challenge_metadata": challenge["metadata"],
                }
            )
        if repair_hint is not None:
            current["repair_hint"] = repair_hint
        config[_AUTH_CONFIG_KEY] = current
        credential.config_json = self._safe_config(config)
        credential.status = "connected" if state == "connected" else "pending"
        if state == "disconnected":
            credential.status = "disconnected"
        credential.updated_at = utcnow()
        self._s.add(credential)

    def _record_telegram_repair(
        self, *, credential: Credential, generation: int, repair_hint: str
    ) -> None:
        # A native transition may have advanced in another request while this
        # request awaited TDLib. Repair must inspect fresh durable state before
        # deciding whether its generation still owns the Account.
        self._s.refresh(credential)
        current = self._telegram_state(credential)
        if current is None or current["generation"] != generation:
            return
        self._write_telegram_state(
            credential=credential,
            generation=generation,
            state="repair-required",
            challenge=None,
            repair_hint=repair_hint,
        )
        self._s.commit()

    def _authorization_projection(
        self, state: Mapping[str, Any], *, include_qr_link: bool
    ) -> dict[str, Any]:
        state_type = state.get("@type")
        if not isinstance(state_type, str):
            return {
                "status": "repair-required",
                "challenge": None,
                "repair_hint": "Telegram returned an invalid authorization state.",
                "state_type": "invalid",
                "qr_link": None,
            }
        if state_type == "authorizationStateReady":
            return {
                "status": "verifying",
                "challenge": None,
                "repair_hint": None,
                "state_type": state_type,
                "qr_link": None,
            }
        if state_type in {"authorizationStateClosing", "authorizationStateClosed"}:
            return {
                "status": "repair-required",
                "challenge": None,
                "repair_hint": "Telegram native session closed. Start authorization again.",
                "state_type": state_type,
                "qr_link": None,
            }
        kind = _STATE_CHALLENGES.get(state_type)
        if kind is None:
            return {
                "status": "repair-required",
                "challenge": None,
                "repair_hint": "Telegram requires an unsupported authorization step.",
                "state_type": state_type,
                "qr_link": None,
            }
        metadata = _authorization_metadata(state)
        timeout = metadata.get("timeout_seconds")
        lifetime = timeout if isinstance(timeout, int) and timeout > 0 else _CHALLENGE_TTL_SECONDS
        challenge = {
            "kind": kind,
            "expires_at": utcnow() + timedelta(seconds=min(lifetime, _CHALLENGE_TTL_SECONDS)),
            "metadata": metadata,
        }
        qr_link = state.get("link") if kind == "qr" and include_qr_link else None
        return {
            "status": "challenge",
            "challenge": challenge,
            "repair_hint": None,
            "state_type": state_type,
            "qr_link": (
                qr_link if isinstance(qr_link, str) and qr_link.startswith("tg://") else None
            ),
        }

    def _challenge_out(
        self, current: Mapping[str, Any], *, include_qr_link: bool
    ) -> AccountAuthChallengeOut | None:
        kind = current.get("challenge_kind")
        if current.get("state") != "challenge" or kind not in _CHALLENGE_FIELDS:
            return None
        return AccountAuthChallengeOut(
            generation=current["generation"],
            kind=kind,
            expires_at=current.get("expires_at"),
            fields=list(_CHALLENGE_FIELDS[kind]),
            metadata=dict(current.get("challenge_metadata") or {}),
        )

    def _challenge_request(self, *, kind: str, answer: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(answer, Mapping):
            raise ValidationError("Telegram authorization answer must be an object")
        if kind == "phone_number":
            return {
                "@type": "setAuthenticationPhoneNumber",
                "phone_number": _required_answer(answer, "phone_number"),
                "settings": None,
            }
        if kind == "code":
            return {"@type": "checkAuthenticationCode", "code": _required_answer(answer, "code")}
        if kind == "password":
            return {
                "@type": "checkAuthenticationPassword",
                "password": _required_answer(answer, "password"),
            }
        if kind == "email_address":
            return {
                "@type": "setAuthenticationEmailAddress",
                "email_address": _required_answer(answer, "email_address"),
            }
        if kind == "email_code":
            return {
                "@type": "checkAuthenticationEmailCode",
                "code": {
                    "@type": "emailAddressAuthenticationCode",
                    "code": _required_answer(answer, "code"),
                },
            }
        if kind == "registration":
            return {
                "@type": "registerUser",
                "first_name": _required_answer(answer, "first_name"),
                "last_name": _optional_answer(answer, "last_name"),
            }
        raise ValidationError("Telegram authorization challenge cannot accept an answer")

    async def _synchronize_telegram_identity(
        self,
        *,
        credential: Credential,
        runtime: _TelegramRuntime,
        generation: int,
        account_kind: Literal["bot", "user"],
        expected_bot_id: int | None,
    ) -> None:
        user = await runtime.request(
            credential.credential_ref,
            {"@type": "getMe"},
            generation=generation,
            correlation_id=f"telegram-auth:{credential.credential_ref}:{generation}:get-me",
        )
        self._synchronize_telegram_identity_from_user(
            credential=credential,
            user=user,
            account_kind=account_kind,
            expected_bot_id=expected_bot_id,
        )
        self._s.commit()

    def _synchronize_telegram_identity_from_user(
        self,
        *,
        credential: Credential,
        user: Mapping[str, Any],
        account_kind: Literal["bot", "user"],
        expected_bot_id: int | None = None,
    ) -> None:
        if credential.id is None:
            raise ConflictError("Telegram Account identity is unavailable")
        user_id = user.get("id")
        if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
            raise ConflictError("Telegram native account identity is unavailable")
        if account_kind == "bot" and user_id != expected_bot_id:
            raise ConflictError("Telegram bot token does not match the authenticated identity")
        self._assert_telegram_identity_available(
            credential=credential,
            account_kind=account_kind,
            provider_account_id=str(user_id),
        )
        account = self._s.exec(
            select(CredentialAccount).where(CredentialAccount.credential_id == credential.id)
        ).first()
        if account is not None and account.provider_account_id not in {None, str(user_id)}:
            raise ConflictError("Telegram saved session does not match this Account identity")
        if account is None:
            account = CredentialAccount(credential_id=credential.id)
        account.provider_account_id = str(user_id)
        account.display_name = _telegram_display_name(user)
        account.metadata_json = {
            "account_kind": account_kind,
            "username": _telegram_username(user),
            "is_verified": bool(user.get("is_verified")),
        }
        account.updated_at = utcnow()
        self._s.add(account)

    def _assert_telegram_identity_available(
        self,
        *,
        credential: Credential,
        account_kind: Literal["bot", "user"],
        provider_account_id: str,
    ) -> None:
        rows = self._s.exec(
            select(CredentialAccount).where(
                col(CredentialAccount.provider_account_id) == provider_account_id
            )
        ).all()
        for account in rows:
            if account.credential_id == credential.id:
                continue
            other = self._s.get(Credential, account.credential_id)
            if (
                other is None
                or other.provider_key != "telegram"
                or other.revoked_at is not None
                or other.auth_method_key != credential.auth_method_key
            ):
                continue
            raise ConflictError(
                "Telegram physical Account is already connected",
                data={
                    "credential_ref": credential.credential_ref,
                    "existing_credential_ref": other.credential_ref,
                    "account_kind": account_kind,
                },
            )


_STATE_CHALLENGES = {
    "authorizationStateWaitPhoneNumber": "phone_number",
    "authorizationStateWaitCode": "code",
    "authorizationStateWaitPassword": "password",
    "authorizationStateWaitEmailAddress": "email_address",
    "authorizationStateWaitEmailCode": "email_code",
    "authorizationStateWaitOtherDeviceConfirmation": "qr",
    "authorizationStateWaitRegistration": "registration",
}
_CHALLENGE_FIELDS: dict[str, tuple[str, ...]] = {
    "phone_number": ("phone_number",),
    "code": ("code",),
    "password": ("password",),
    "email_address": ("email_address",),
    "email_code": ("code",),
    "qr": (),
    "registration": ("first_name", "last_name"),
}


def _telegram_bot_subject(payload: Mapping[str, str]) -> int:
    """Return the safe numeric bot identifier claimed by a validated token.

    The token remains daemon-held.  The numeric prefix is used solely to reject
    a restored TDLib database that is ready for a different bot after a token
    rotation or failed logout.
    """
    token = payload.get("bot_token")
    subject = token.split(":", 1)[0] if isinstance(token, str) else ""
    if not subject.isascii() or not subject.isdecimal():
        raise ConflictError("Telegram bot token subject is invalid")
    value = int(subject)
    if value <= 0:
        raise ConflictError("Telegram bot token subject is invalid")
    return value


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _safe_challenge_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    metadata: dict[str, Any] = {}
    timeout = value.get("timeout_seconds")
    if isinstance(timeout, int) and not isinstance(timeout, bool) and timeout >= 0:
        metadata["timeout_seconds"] = timeout
    delivery_type = value.get("delivery_type")
    if isinstance(delivery_type, str) and delivery_type in {
        "authenticationCodeTypeTelegramMessage",
        "authenticationCodeTypeSms",
        "authenticationCodeTypeCall",
        "authenticationCodeTypeFlashCall",
        "authenticationCodeTypeMissedCall",
    }:
        metadata["delivery_type"] = delivery_type
    for key in ("allow_apple_id", "allow_google_id"):
        if isinstance(value.get(key), bool):
            metadata[key] = value[key]
    return metadata


def _authorization_metadata(state: Mapping[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for value in (state.get("code_info"), state.get("email_address_authentication")):
        if not isinstance(value, Mapping):
            continue
        timeout = value.get("timeout")
        if isinstance(timeout, int) and not isinstance(timeout, bool) and timeout >= 0:
            metadata["timeout_seconds"] = timeout
        delivery = value.get("type")
        if isinstance(delivery, Mapping) and isinstance(delivery.get("@type"), str):
            metadata["delivery_type"] = delivery["@type"]
    for key in ("allow_apple_id", "allow_google_id"):
        if isinstance(state.get(key), bool):
            metadata[key] = state[key]
    return _safe_challenge_metadata(metadata)


def _safe_repair_hint(value: Any) -> str | None:
    return value if isinstance(value, str) and len(value) <= 240 else None


def _required_answer(answer: Mapping[str, Any], key: str) -> str:
    value = answer.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationError(f"Telegram authorization answer requires {key}")
    return value


def _optional_answer(answer: Mapping[str, Any], key: str) -> str:
    value = answer.get(key, "")
    if not isinstance(value, str):
        raise ValidationError(f"Telegram authorization answer {key} must be text")
    return value


def _telegram_username(user: Mapping[str, Any]) -> str | None:
    usernames = user.get("usernames")
    if not isinstance(usernames, Mapping):
        return None
    active = usernames.get("active_usernames")
    if not isinstance(active, list):
        return None
    for username in active:
        if isinstance(username, str) and username:
            return username
    return None


def _telegram_display_name(user: Mapping[str, Any]) -> str | None:
    username = _telegram_username(user)
    if username is not None:
        return f"@{username}"
    names = [user.get("first_name"), user.get("last_name")]
    display_name = " ".join(
        name.strip() for name in names if isinstance(name, str) and name.strip()
    )
    return display_name or None


__all__ = ["TelegramAuthorizationMixin"]
