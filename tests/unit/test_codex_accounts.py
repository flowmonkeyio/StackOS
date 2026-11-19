from __future__ import annotations

import os
from contextlib import contextmanager
from threading import Event

import pytest

from stackos.host_mcp.accounts import DesiredAccount, NativeAccountProfile, ResolvedAccountSelection
from stackos.host_mcp.adapters import codex, codex_accounts
from stackos.host_mcp.adapters.codex_accounts import AuthExchange, CodexAccountError


@pytest.fixture
def homes(tmp_path):
    regular, isolated = tmp_path / "regular", tmp_path / "isolated"
    for home in (regular, isolated):
        home.mkdir(mode=0o700)
    for home, data in ((regular, b"original A"), (isolated, b"original B")):
        auth = home / "auth.json"
        auth.write_bytes(data)
        auth.chmod(0o600)
    return regular, isolated


def fixture_principal(home):
    data = (home / "auth.json").read_bytes()
    return ("B" if data.endswith(b"B") else "A", "workspace")


def test_exchange_preserves_native_refresh_and_original_login(homes):
    regular, isolated = homes
    exchange = AuthExchange(regular, fixture_principal)
    with exchange.selected(isolated, ("B", "workspace")):
        assert (regular / "auth.json").read_bytes() == b"original B"
        assert (isolated / "auth.json").read_bytes() == b"original A"
        assert exchange.profile_home(None) == isolated
        assert exchange.profile_home(isolated) == regular
        with AuthExchange(regular, fixture_principal).selected(isolated, ("B", "workspace")):
            assert (regular / "auth.json").read_bytes() == b"original B"
        with (
            pytest.raises(CodexAccountError, match="another account"),
            AuthExchange(regular, fixture_principal).selected(None, ("A", "workspace")),
        ):
            pytest.fail("another account must not change the credential in use")
        updated = regular / "refresh"
        updated.write_bytes(b"refreshed B")
        updated.chmod(0o600)
        os.replace(updated, regular / "auth.json")
    assert (regular / "auth.json").read_bytes() == b"original A"
    assert (isolated / "auth.json").read_bytes() == b"refreshed B"


def test_exchange_preserves_unexpected_foreign_write_for_recovery(homes):
    regular, isolated = homes
    exchange = AuthExchange(regular, fixture_principal)
    with (
        pytest.raises(CodexAccountError, match="credential changed"),
        exchange.selected(isolated, ("B", "workspace")),
    ):
        updated = regular / "login"
        updated.write_bytes(b"new external principal")
        updated.chmod(0o600)
        os.replace(updated, regular / "auth.json")
    assert (regular / "auth.json").read_bytes() == b"new external principal"
    assert (isolated / "auth.json").read_bytes() == b"original A"


def test_withdrawn_selection_keeps_both_native_files(homes):
    regular, isolated = homes
    exchange = AuthExchange(regular, fixture_principal)
    with (
        pytest.raises(CodexAccountError, match="selection changed"),
        exchange.selected(isolated, ("B", "workspace"), allowed=lambda: False),
    ):
        pytest.fail("a stale selection must not launch")
    assert (regular / "auth.json").read_bytes() == b"original A"
    assert (isolated / "auth.json").read_bytes() == b"original B"


def test_interrupted_exchange_recovers_from_journal(homes, monkeypatch):
    regular, isolated = homes
    exchange = AuthExchange(regular, fixture_principal)
    real_sync = codex_accounts._sync_directory

    def interrupt_after_swap(path):
        if path == regular:
            raise OSError("interrupted after native exchange")
        real_sync(path)

    with monkeypatch.context() as patch:
        patch.setattr(codex_accounts, "_sync_directory", interrupt_after_swap)
        with (
            pytest.raises(CodexAccountError, match="private Codex credential transaction"),
            exchange.selected(isolated, ("B", "workspace")),
        ):
            pytest.fail("the failed transaction must not launch")
    assert (regular / "auth.json").read_bytes() == b"original B"
    assert exchange.journal.is_file()
    with AuthExchange(regular, fixture_principal).selected(None, None):
        assert (regular / "auth.json").read_bytes() == b"original A"
        assert (isolated / "auth.json").read_bytes() == b"original B"
        assert not exchange.journal.exists()


@pytest.mark.parametrize("mode", ["keyring", "auto", "ephemeral"])
def test_unsupported_credential_mode_keeps_both_stores(homes, monkeypatch, mode):
    regular, isolated = homes
    monkeypatch.setattr(
        codex_accounts, "subprocess_env_for_cli", lambda _: {"CODEX_HOME": str(regular)}
    )
    monkeypatch.setattr(
        codex_accounts, "_config", lambda *_args, **_kw: {"cli_auth_credentials_store": mode}
    )
    ref = "e8ece2a3-4335-4267-99ec-fab200a72839"
    selection = ResolvedAccountSelection(
        DesiredAccount(account_ref=ref, revision=1),
        NativeAccountProfile(ref, isolated),
        ("B", "workspace"),
    )
    with (
        pytest.raises(CodexAccountError, match="configured Codex credential backend"),
        codex_accounts.selected_launch("codex", regular, selection),
    ):
        pytest.fail("must not force the file backend")
    assert (regular / "auth.json").read_bytes() == b"original A"
    assert (isolated / "auth.json").read_bytes() == b"original B"
    assert not (regular / ".stackos-accounts").exists()


def test_native_identity_requires_native_workspace_and_uses_email_for_display():
    response = {
        "account": {"type": "chatgpt", "email": "same@example.invalid"},
        "userId": "native-user",
    }
    assert codex_accounts._identity(response, {}) == "unavailable"
    selection = {
        "activeSessionId": "chosen",
        "sessions": [
            {
                "sessionId": "chosen",
                "selectedWorkspaceAccountId": "workspace",
                "userId": "native-user",
            },
        ],
    }
    identity = codex_accounts._identity(response, selection)
    assert identity.principal_key == ("native-user", "workspace")
    assert identity.email == "same@example.invalid"


def test_ordinary_reader_prevents_a_managed_account_exchange(homes):
    regular, isolated = homes
    exchange = AuthExchange(regular, fixture_principal)
    with (
        codex_accounts.ordinary_launch("codex", env={"CODEX_HOME": str(regular)}),
        pytest.raises(CodexAccountError, match="in use"),
        exchange.selected(isolated, ("B", "workspace")),
    ):
        pytest.fail("native default and selected executions cannot overlap a credential swap")
    assert (regular / "auth.json").read_bytes() == b"original A"


def test_headless_rejects_competing_api_key_before_creating_a_turn(homes, monkeypatch):
    regular, isolated = homes
    ref = "e8ece2a3-4335-4267-99ec-fab200a72839"
    selection = ResolvedAccountSelection(
        DesiredAccount(account_ref=ref, revision=1),
        NativeAccountProfile(ref, isolated),
        ("B", "workspace"),
    )
    monkeypatch.setattr(codex, "_select_codex_bin", lambda: "codex")
    monkeypatch.setenv("CODEX_HOME", str(regular))
    monkeypatch.setenv("CODEX_API_KEY", "synthetic-key")
    monkeypatch.setattr(
        codex_accounts, "_config", lambda *_a, **_kw: pytest.fail("started native RPC")
    )
    result = codex.run_agent(regular, "Instructions", "Prompt", {}, account_selection=selection)
    assert not result.ok
    assert "competing" in result.error
    assert (regular / "auth.json").read_bytes() == b"original A"
    assert (isolated / "auth.json").read_bytes() == b"original B"


def test_enrollment_starts_native_first_party_signin_in_isolated_home(homes, monkeypatch):
    regular, isolated = homes
    observed = {}
    completed = Event()

    class Session:
        def call(self, method, params):
            observed["call"] = (method, params)
            return {"authUrl": "https://auth.openai.com/authorize", "loginId": "native-login"}

        def wait_for_login(self, login_id, *, timeout):
            observed["login"] = login_id
            return True

    @contextmanager
    def backend(_binary, cwd, env, _control, *, overrides, pass_fds):
        assert len(pass_fds) == 1
        observed["home"] = env["CODEX_HOME"]
        observed["cwd"] = cwd
        observed["mode"] = overrides
        try:
            yield Session()
        finally:
            completed.set()

    monkeypatch.setattr(codex_accounts, "open_app_server", backend)
    monkeypatch.setattr(
        codex_accounts,
        "subprocess_env_for_cli",
        lambda _: {"CODEX_HOME": str(regular)},
    )
    monkeypatch.setattr(
        codex_accounts, "_config", lambda *_a, **_kw: {"cli_auth_credentials_store": "file"}
    )
    opened = []
    monkeypatch.setattr(codex_accounts.webbrowser, "open", lambda url: not opened.append(url))
    adapter = codex_accounts.CodexAccounts(lambda: "codex")
    adapter.start_login(NativeAccountProfile("fixture", isolated))
    assert completed.wait(3)
    assert opened == ["https://auth.openai.com/authorize"]
    assert observed == {
        "home": str(isolated),
        "cwd": isolated,
        "mode": ("-c", 'cli_auth_credentials_store="file"'),
        "call": ("account/login/start", {"type": "chatgpt"}),
        "login": "native-login",
    }
    assert (regular / "auth.json").read_bytes() == b"original A"


def test_login_thread_failure_is_cleanable_by_a_new_adapter(homes, monkeypatch):
    regular, isolated = homes
    monkeypatch.setattr(
        codex_accounts, "subprocess_env_for_cli", lambda _: {"CODEX_HOME": str(regular)}
    )
    monkeypatch.setattr(
        codex_accounts, "_config", lambda *_a, **_kw: {"cli_auth_credentials_store": "file"}
    )

    def interrupt_worker(_thread):
        raise OSError("fixture interrupts before native launch")

    monkeypatch.setattr(codex_accounts.Thread, "start", interrupt_worker)
    adapter = codex_accounts.CodexAccounts(lambda: "codex")
    profile = NativeAccountProfile("fixture", isolated)
    with pytest.raises(OSError, match="interrupts before native launch"):
        adapter.start_login(profile)
    recovered = codex_accounts.CodexAccounts(lambda: "codex")
    assert recovered.discard_login(profile)
    assert not (isolated / ".stackos-native-login").exists()
    assert (regular / "auth.json").read_bytes() == b"original A"
