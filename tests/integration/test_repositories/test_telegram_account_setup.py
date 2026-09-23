"""Account setup contract for TDLib-backed Telegram identities."""

from __future__ import annotations

import json

import pytest
from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.auth_providers.repository.telegram_application import TelegramApplicationRepository
from stackos.db.models import (
    Credential,
    CredentialUsageEvent,
    IntegrationCredential,
    ProjectCredential,
    TelegramApplication,
)
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository

_PROVIDER_KEY = "telegram"


def _secret_payload(session: Session, credential_ref: str) -> dict[str, object]:
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).one()
    assert credential.integration_credential_id is not None
    integration = session.get(IntegrationCredential, credential.integration_credential_id)
    assert integration is not None
    decrypted = IntegrationCredentialRepository(session).get_decrypted(integration.id)
    return json.loads(decrypted.decode())


def _bot_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "api_id": 12345,
        "api_hash": "test-api-hash",
        "bot_token": "123456:test-bot-token",
        "proxy_enabled": True,
        "proxy_type": "socks5",
        "proxy_host": "proxy.example.test",
        "proxy_port": 1080,
        "proxy_http_only": False,
        "proxy_username": "test-proxy-user",
        "proxy_password": "test-proxy-password",
    }
    fields.update(overrides)
    return fields


def test_tdlib_telegram_manifest_declares_bot_and_user_setup_methods(session: Session) -> None:
    """The production manifest, rather than only a test fixture, owns this schema."""
    providers = AuthRepository(session).list_providers(provider_key=_PROVIDER_KEY)

    assert len(providers) == 1
    methods = {method.key: method for method in providers[0].auth_methods}
    assert set(methods) == {"tdlib-bot-token", "tdlib-user-session"}
    assert {
        field.key: (field.secret, field.required, field.type)
        for field in methods["tdlib-bot-token"].fields
    } == {
        "bot_token": (True, True, "secret"),
        "proxy_enabled": (False, False, "boolean"),
        "proxy_type": (False, False, "select"),
        "proxy_host": (False, False, "text"),
        "proxy_port": (False, False, "number"),
        "proxy_http_only": (False, False, "boolean"),
        "proxy_username": (True, False, "text"),
        "proxy_password": (True, False, "text"),
        "proxy_secret": (True, False, "text"),
    }
    assert "bot_token" not in {field.key for field in methods["tdlib-user-session"].fields}
    assert "api_id" not in {field.key for field in methods["tdlib-user-session"].fields}
    assert "api_hash" not in {field.key for field in methods["tdlib-user-session"].fields}


def test_tdlib_accounts_require_shared_application_credentials_and_keep_bot_token_bot_only(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)
    application_repo = TelegramApplicationRepository(session)
    assert application_repo.configured() is False

    with pytest.raises(ValidationError, match="missing bot_token"):
        repo.store_credential(
            provider_key=_PROVIDER_KEY,
            auth_method_key="tdlib-bot-token",
            display_name="Invalid first bot",
            fields={"api_id": 12345, "api_hash": "test-api-hash"},
        )
    assert application_repo.configured() is False
    with pytest.raises(ValidationError, match="proxy"):
        repo.store_credential(
            provider_key=_PROVIDER_KEY,
            auth_method_key="tdlib-bot-token",
            display_name="Invalid first proxy",
            fields=_bot_fields(proxy_host="https://bad-host.example.test"),
        )
    assert application_repo.configured() is False

    with pytest.raises(ValidationError, match="API ID"):
        repo.store_credential(
            provider_key=_PROVIDER_KEY,
            auth_method_key="tdlib-user-session",
            display_name="Missing app id",
            fields={"api_hash": "another-test-api-hash"},
        )
    assert application_repo.configured() is False

    bot = repo.store_credential(
        provider_key=_PROVIDER_KEY,
        auth_method_key="tdlib-bot-token",
        display_name="Support bot",
        fields=_bot_fields(proxy_enabled=False),
        attach_project_id=project_id,
    ).data
    user = repo.store_credential(
        provider_key=_PROVIDER_KEY,
        auth_method_key="tdlib-user-session",
        display_name="Operator user",
        fields={"proxy_enabled": False},
        attach_project_id=project_id,
    ).data

    assert bot.auth_method_key == "tdlib-bot-token"
    assert user.auth_method_key == "tdlib-user-session"
    assert bot.status == user.status == "pending"
    assert _secret_payload(session, bot.credential_ref) == {"bot_token": "123456:test-bot-token"}
    assert _secret_payload(session, user.credential_ref) == {}
    assert application_repo.get().api_id == 12345
    assert application_repo.get().api_hash == "test-api-hash"

    with pytest.raises(ConflictError, match="already configured"):
        repo.store_credential(
            provider_key=_PROVIDER_KEY,
            auth_method_key="tdlib-user-session",
            display_name="Second app",
            fields={"api_hash": "another-test-api-hash"},
        )
    with pytest.raises(ValidationError, match="not declared"):
        repo.store_credential(
            provider_key=_PROVIDER_KEY,
            auth_method_key="tdlib-user-session",
            display_name="Invalid user token",
            fields={"bot_token": "must-not-be-accepted-for-user"},
        )


def test_first_application_conflict_rolls_back_staged_account_and_same_session_can_retry(
    session: Session,
    project_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = AuthRepository(session)
    application_repo = TelegramApplicationRepository(session)
    baseline = {
        model: len(session.exec(select(model)).all())
        for model in (Credential, IntegrationCredential, ProjectCredential, CredentialUsageEvent)
    }
    original_create = TelegramApplicationRepository.create
    attempts = 0

    def conflict_once(self: TelegramApplicationRepository, application: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            # The Account and encrypted backing have already been flushed.
            assert session.exec(select(Credential)).all()
            assert session.exec(select(IntegrationCredential)).all()
            assert session.exec(select(ProjectCredential)).all()
            assert session.exec(select(CredentialUsageEvent)).all()
            raise ConflictError("application changed while creating Account")
        original_create(self, application)  # type: ignore[arg-type]

    monkeypatch.setattr(TelegramApplicationRepository, "create", conflict_once)
    with pytest.raises(ConflictError, match="setup changed") as error:
        repo.store_credential(
            provider_key=_PROVIDER_KEY,
            auth_method_key="tdlib-bot-token",
            display_name="Retryable first bot",
            fields=_bot_fields(proxy_enabled=False),
            attach_project_id=project_id,
        )
    assert error.value.data["next_action"] == (
        "Refresh local Accounts and retry Telegram Account setup."
    )
    assert application_repo.configured() is False
    for model, count in baseline.items():
        assert len(session.exec(select(model)).all()) == count

    created = repo.store_credential(
        provider_key=_PROVIDER_KEY,
        auth_method_key="tdlib-bot-token",
        display_name="Retryable first bot",
        fields=_bot_fields(proxy_enabled=False),
        attach_project_id=project_id,
    ).data
    assert attempts == 2
    assert created.project_ids == [project_id]
    assert application_repo.get().api_id == 12345


def test_last_telegram_revoke_releases_application_for_corrected_first_setup(
    session: Session,
) -> None:
    repo = AuthRepository(session)
    first = repo.store_credential(
        provider_key=_PROVIDER_KEY,
        auth_method_key="tdlib-bot-token",
        display_name="First bot with bad app",
        fields=_bot_fields(proxy_enabled=False),
    ).data
    second = repo.store_credential(
        provider_key=_PROVIDER_KEY,
        auth_method_key="tdlib-user-session",
        display_name="User sharing bad app",
        fields={},
    ).data
    second_row = session.exec(
        select(Credential).where(Credential.credential_ref == second.credential_ref)
    ).one()
    assert second_row.integration_credential_id is not None
    IntegrationCredentialRepository(session).set(
        credential_ref=second.credential_ref,
        provider_key="telegram",
        integration_credential_id=second_row.integration_credential_id,
        secret_payload=json.dumps(
            {"_tdlib_database_encryption_key": "preserved-user-key"}
        ).encode(),
    )
    assert TelegramApplicationRepository(session).get().api_hash == "test-api-hash"

    repo.revoke(credential_ref=first.credential_ref)
    assert session.get(TelegramApplication, 1) is not None
    assert repo.get_account(credential_ref=second.credential_ref).status == "pending"
    assert _secret_payload(session, second.credential_ref) == {
        "_tdlib_database_encryption_key": "preserved-user-key"
    }

    repo.revoke(credential_ref=second.credential_ref)
    assert session.get(TelegramApplication, 1) is None
    corrected = repo.store_credential(
        provider_key=_PROVIDER_KEY,
        auth_method_key="tdlib-bot-token",
        display_name="Corrected first bot",
        fields=_bot_fields(api_id=67890, api_hash="corrected-app-hash", proxy_enabled=False),
    ).data
    assert corrected.status == "pending"
    application = TelegramApplicationRepository(session).get()
    assert application.api_id == 67890
    assert application.api_hash == "corrected-app-hash"


def test_existing_application_removed_during_create_rolls_back_staged_account(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = AuthRepository(session)
    repo.store_credential(
        provider_key=_PROVIDER_KEY,
        auth_method_key="tdlib-user-session",
        display_name="Existing Telegram user",
        fields={"api_id": 12345, "api_hash": "test-api-hash"},
    )
    baseline = {
        model: len(session.exec(select(model)).all())
        for model in (Credential, IntegrationCredential, CredentialUsageEvent)
    }
    original_configured = TelegramApplicationRepository.configured
    calls = 0

    def disappears_between_checks(self: TelegramApplicationRepository) -> bool:
        nonlocal calls
        calls += 1
        if calls == 2:
            # Emulate the final Account being revoked after initial readiness.
            assert len(session.exec(select(Credential)).all()) > baseline[Credential]
            return False
        return original_configured(self)

    monkeypatch.setattr(TelegramApplicationRepository, "configured", disappears_between_checks)
    with pytest.raises(ConflictError, match="setup changed") as error:
        repo.store_credential(
            provider_key=_PROVIDER_KEY,
            auth_method_key="tdlib-bot-token",
            display_name="Bot racing last revoke",
            fields={"bot_token": "123456:second-bot-token"},
        )
    assert error.value.data["retryable"] is True
    for model, count in baseline.items():
        assert len(session.exec(select(model)).all()) == count
    assert TelegramApplicationRepository(session).configured() is True

    created = repo.store_credential(
        provider_key=_PROVIDER_KEY,
        auth_method_key="tdlib-bot-token",
        display_name="Bot racing last revoke",
        fields={"bot_token": "123456:second-bot-token"},
    ).data
    assert created.status == "pending"


def test_tdlib_account_get_update_and_list_never_return_proxy_or_application_secrets(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        provider_key=_PROVIDER_KEY,
        auth_method_key="tdlib-bot-token",
        display_name="Private proxy bot",
        fields=_bot_fields(),
        attach_project_id=project_id,
    ).data

    edit = repo.get_credential_edit_state(credential_ref=stored.credential_ref)
    assert edit.values == {
        "proxy_enabled": True,
        "proxy_type": "socks5",
        "proxy_host": "proxy.example.test",
        "proxy_port": 1080,
        "proxy_http_only": False,
    }
    assert edit.secret_present == {
        "bot_token": True,
        "proxy_username": True,
        "proxy_password": True,
        "proxy_secret": False,
    }

    repo.update_credential(
        credential_ref=stored.credential_ref,
        fields={"proxy_host": "rotated-proxy.example.test"},
        display_name=None,
    )
    assert _secret_payload(session, stored.credential_ref) == {
        "bot_token": "123456:test-bot-token",
        "proxy_username": "test-proxy-user",
        "proxy_password": "test-proxy-password",
    }
    rendered = json.dumps(
        {
            "created": stored.model_dump(mode="json"),
            "edit": repo.get_credential_edit_state(credential_ref=stored.credential_ref).model_dump(
                mode="json"
            ),
            "listed": repo.status(project_id=project_id, provider_key=_PROVIDER_KEY).model_dump(
                mode="json"
            ),
        }
    )
    for secret in (
        "test-api-hash",
        "123456:test-bot-token",
        "test-proxy-user",
        "test-proxy-password",
    ):
        assert secret not in rendered


def test_disabling_tdlib_proxy_clears_saved_proxy_configuration_and_auth(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        provider_key=_PROVIDER_KEY,
        auth_method_key="tdlib-bot-token",
        display_name="Proxy can be removed",
        fields=_bot_fields(),
        attach_project_id=project_id,
    ).data

    repo.update_credential(
        credential_ref=stored.credential_ref,
        fields={"proxy_enabled": False},
        display_name=None,
    )

    edit = repo.get_credential_edit_state(credential_ref=stored.credential_ref)
    assert edit.values == {"proxy_enabled": False}
    assert edit.secret_present == {
        "bot_token": True,
        "proxy_username": False,
        "proxy_password": False,
        "proxy_secret": False,
    }
    assert _secret_payload(session, stored.credential_ref) == {
        "bot_token": "123456:test-bot-token",
    }


def test_switching_tdlib_proxy_type_clears_incompatible_proxy_credentials(
    session: Session,
    project_id: int,
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        provider_key=_PROVIDER_KEY,
        auth_method_key="tdlib-bot-token",
        display_name="Proxy type can change",
        fields=_bot_fields(),
        attach_project_id=project_id,
    ).data

    repo.update_credential(
        credential_ref=stored.credential_ref,
        fields={"proxy_type": "mtproto", "proxy_secret": "0123456789abcdef0123456789abcdef"},
        display_name=None,
    )

    edit = repo.get_credential_edit_state(credential_ref=stored.credential_ref)
    assert edit.values == {
        "proxy_enabled": True,
        "proxy_type": "mtproto",
        "proxy_host": "proxy.example.test",
        "proxy_port": 1080,
    }
    assert edit.secret_present == {
        "bot_token": True,
        "proxy_username": False,
        "proxy_password": False,
        "proxy_secret": True,
    }
    assert _secret_payload(session, stored.credential_ref) == {
        "bot_token": "123456:test-bot-token",
        "proxy_secret": "0123456789abcdef0123456789abcdef",
    }

    repo.update_credential(
        credential_ref=stored.credential_ref,
        fields={
            "proxy_type": "http",
            "proxy_http_only": True,
            "proxy_username": "http-user",
            "proxy_password": "http-password",
        },
        display_name=None,
    )
    assert _secret_payload(session, stored.credential_ref) == {
        "bot_token": "123456:test-bot-token",
        "proxy_username": "http-user",
        "proxy_password": "http-password",
    }


@pytest.mark.parametrize(
    "changed_fields",
    [
        {"proxy_host": "https://proxy.example.test"},
        {"proxy_host": "http:"},
        {"proxy_port": 0},
        {"proxy_port": 65536},
        {"proxy_port": 1080.5},
        {"proxy_port": True},
        {"proxy_type": "mtproto", "proxy_secret": "not-hex"},
        {"proxy_type": "mtproto", "proxy_secret": "a1b2"},
        {"proxy_type": "mtproto", "proxy_secret": "01" * 16, "proxy_password": "invalid"},
        {"proxy_type": "http", "proxy_secret": "01" * 16},
        {"proxy_http_only": True},
        {"api_id": 0},
        {"api_id": 1.5},
    ],
)
def test_invalid_proxy_update_preserves_the_saved_account(
    session: Session, changed_fields: dict[str, object]
) -> None:
    repo = AuthRepository(session)
    stored = repo.store_credential(
        provider_key=_PROVIDER_KEY,
        auth_method_key="tdlib-bot-token",
        display_name="Validated transport",
        fields=_bot_fields(),
    ).data
    before = repo.get_credential_edit_state(credential_ref=stored.credential_ref)
    before_secrets = _secret_payload(session, stored.credential_ref)
    with pytest.raises(ValidationError):
        repo.update_credential(
            credential_ref=stored.credential_ref, fields=changed_fields, display_name=None
        )
    assert repo.get_credential_edit_state(credential_ref=stored.credential_ref) == before
    assert _secret_payload(session, stored.credential_ref) == before_secrets
