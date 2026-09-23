"""One encrypted Telegram application identity for all daemon-owned TDLib sessions."""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from stackos.crypto.aes_gcm import decrypt, encrypt
from stackos.db.models import TelegramApplication
from stackos.integrations.telegram_tdlib.sessions import TelegramApplicationCredentials
from stackos.repositories.base import ConflictError, ValidationError

_APPLICATION_KIND = "telegram-application"


class TelegramApplicationRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def configured(self) -> bool:
        return self._s.get(TelegramApplication, 1) is not None

    def get(self) -> TelegramApplicationCredentials:
        row = self._s.get(TelegramApplication, 1)
        if row is None:
            raise ConflictError(
                "Telegram application credentials are not configured",
                data={
                    "provider_key": "telegram",
                    "next_action": (
                        "Set the Telegram application API ID and hash in local Accounts."
                    ),
                },
            )
        raw = decrypt(
            row.encrypted_payload,
            nonce=row.nonce,
            project_id=None,
            kind=_APPLICATION_KIND,
        )
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConflictError("Telegram application credentials are invalid") from exc
        if not isinstance(value, dict):
            raise ConflictError("Telegram application credentials are invalid")
        api_id = value.get("api_id")
        api_hash = value.get("api_hash")
        if (
            isinstance(api_id, bool)
            or not isinstance(api_id, int)
            or not 0 < api_id < 2**31
            or not isinstance(api_hash, str)
            or not api_hash
        ):
            raise ConflictError("Telegram application credentials are invalid")
        return TelegramApplicationCredentials(api_id=api_id, api_hash=api_hash)

    def prepare_first_setup(
        self, fields: dict[str, Any]
    ) -> tuple[dict[str, Any], TelegramApplicationCredentials | None]:
        """Validate first-use application input without modifying the database."""
        account_fields = dict(fields)
        api_id = account_fields.pop("api_id", None)
        api_hash = account_fields.pop("api_hash", None)
        if self.configured():
            if api_id is not None or api_hash is not None:
                raise ConflictError(
                    "Telegram application credentials are already configured",
                    data={
                        "provider_key": "telegram",
                        "next_action": (
                            "Omit the application API ID and hash to reuse the configured "
                            "application. To replace a wrong pair, explicitly revoke all "
                            "Telegram Accounts first, then create a new Account with the "
                            "correct pair."
                        ),
                    },
                )
            return account_fields, None
        if isinstance(api_id, str) and api_id.isdecimal():
            api_id = int(api_id)
        if isinstance(api_id, bool) or not isinstance(api_id, int) or not 0 < api_id < 2**31:
            raise ValidationError(
                "Telegram application API ID must be a positive 32-bit integer",
                data={"provider_key": "telegram", "field": "api_id"},
            )
        if not isinstance(api_hash, str) or not api_hash.strip():
            raise ValidationError(
                "Telegram application API hash is required",
                data={"provider_key": "telegram", "field": "api_hash"},
            )
        return account_fields, TelegramApplicationCredentials(api_id=api_id, api_hash=api_hash)

    def create(self, application: TelegramApplicationCredentials) -> None:
        """Stage the singleton in the caller's validated Account transaction."""
        if self.configured():
            raise ConflictError("Telegram application credentials are already configured")
        plaintext = json.dumps(
            {"api_id": application.api_id, "api_hash": application.api_hash},
            separators=(",", ":"),
        ).encode()
        encrypted_payload, nonce = encrypt(plaintext, project_id=None, kind=_APPLICATION_KIND)
        self._s.add(TelegramApplication(encrypted_payload=encrypted_payload, nonce=nonce))


__all__ = ["TelegramApplicationRepository"]
