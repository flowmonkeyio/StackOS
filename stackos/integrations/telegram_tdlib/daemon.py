"""Daemon composition for the managed Account sessions and shared ingress owner."""

from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository
from stackos.config import Settings
from stackos.db.models import Credential
from stackos.integrations.telegram_tdlib.holds import telegram_native_auth_hold_reason
from stackos.integrations.telegram_tdlib.native import TelegramTdlibClient, load_managed_tdlib
from stackos.integrations.telegram_tdlib.receipts import reconcile_telegram_receipt
from stackos.integrations.telegram_tdlib.runtime import tdlib_runtime_root
from stackos.integrations.telegram_tdlib.service import TelegramTdlibService
from stackos.integrations.telegram_tdlib.sessions import TelegramTdlibSessionRegistry
from stackos.integrations.telegram_tdlib.updates import process_telegram_update
from stackos.logging import get_logger


def build_telegram_runtime(engine: Engine, settings: Settings) -> TelegramTdlibService:
    # Load lazily: an optional provider must not prevent unrelated daemon work.
    # Once requested, both bot and user clients use this one verified ABI.
    abi = None

    def client_factory() -> TelegramTdlibClient:
        nonlocal abi
        if abi is None:
            _, abi = load_managed_tdlib(runtime_root=tdlib_runtime_root(settings.data_dir))
        return TelegramTdlibClient(abi)

    async def authorization_sink(account_ref: str, generation: int, state: dict[str, Any]) -> None:
        with Session(engine) as session:
            repo = AuthRepository(session)
            status = repo.record_telegram_authorization_state(
                credential_ref=account_ref,
                generation=generation,
                state=state,
            )
            if status.status == "verifying":
                await repo.synchronize_telegram_ready(
                    credential_ref=account_ref,
                    generation=generation,
                    runtime=runtime,
                )
                if repo.get_telegram_session_status(credential_ref=account_ref, runtime=runtime)[
                    "connected"
                ]:
                    ActionRepository(session).release_account_delivery_quiesce(
                        credential_ref=account_ref,
                        expected_reason=telegram_native_auth_hold_reason(account_ref),
                    )

    async def authorization_settled_sink(
        account_ref: str, generation: int, _state: dict[str, Any]
    ) -> None:
        # A local user sign-in may finish after account.start returns (QR or a
        # delayed authorization update).  Retire it only after the service has
        # published Ready to waiting auth calls; explicit connect stays live.
        with Session(engine) as session:
            await AuthRepository(session).finish_telegram_sign_in_if_ready(
                credential_ref=account_ref,
                generation=generation,
                runtime=runtime,
            )

    async def ingress_sink(account_ref: str, generation: int, update: dict[str, Any]) -> None:
        with Session(engine) as session:
            account = session.exec(
                select(Credential).where(Credential.credential_ref == account_ref)
            ).one_or_none()
            if account is None or account.status != "connected":
                return
            if (account.config_json or {}).get("telegram_desired_connected") is not True:
                return
            current = (account.config_json or {}).get("telegram_auth") or {}
            if current.get("generation") != generation:
                return
            process_telegram_update(session, credential_ref=account_ref, update=update)

    async def receipt_sink(account_ref: str, generation: int, update: dict[str, Any]) -> None:
        with Session(engine) as session:
            account = session.exec(
                select(Credential).where(Credential.credential_ref == account_ref)
            ).one_or_none()
            if account is None or account.status != "connected":
                return
            if (account.config_json or {}).get("telegram_desired_connected") is not True:
                return
            if ((account.config_json or {}).get("telegram_auth") or {}).get(
                "generation"
            ) != generation:
                return
            reconcile_telegram_receipt(session, credential_ref=account_ref, update=update)

    runtime = TelegramTdlibService(
        session_registry=TelegramTdlibSessionRegistry(client_factory=client_factory),
        authorization_sink=authorization_sink,
        authorization_settled_sink=authorization_settled_sink,
        ingress_sink=ingress_sink,
        message_receipt_sink=receipt_sink,
    )
    return runtime


async def restore_telegram_accounts(
    engine: Engine,
    settings: Settings,
    runtime: TelegramTdlibService,
) -> None:
    """Restore only Accounts whose last explicit command was connect."""
    with Session(engine) as session:
        account_refs = [
            row.credential_ref
            for row in session.exec(
                select(Credential).where(Credential.provider_key == "telegram")
            ).all()
            if row.status != "revoked"
            and (row.config_json or {}).get("telegram_desired_connected") is True
        ]
    for account_ref in account_refs:
        try:
            with Session(engine) as session:
                await AuthRepository(session).resume_telegram_session(
                    credential_ref=account_ref,
                    runtime=runtime,
                    settings=settings,
                )
        except Exception as exc:
            # Native payloads and setup answers must never enter daemon logs.
            get_logger(__name__).warning(
                "telegram.account.restore_failed",
                credential_ref=account_ref,
                error_type=type(exc).__name__,
            )
