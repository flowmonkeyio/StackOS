from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest

from stackos.host_mcp.accounts import (
    HostAccountError,
    HostAccounts,
    NativeAccountProfile,
    NativeIdentity,
)
from stackos.host_mcp.adapters import codex_accounts


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
    return (data.decode().split()[-1], "workspace")


@pytest.fixture
def registry(homes, tmp_path, monkeypatch):
    regular, _ = homes
    monkeypatch.setattr(
        codex_accounts, "subprocess_env_for_cli", lambda _: {"CODEX_HOME": str(regular)}
    )
    monkeypatch.setattr(
        codex_accounts, "_config", lambda *_a, **_kw: {"cli_auth_credentials_store": "file"}
    )

    def identity(_binary, env, **_kwargs):
        principal, workspace = fixture_principal(codex_accounts._home(env))
        return NativeIdentity(
            principal=principal, workspace=workspace, email=f"{principal}@example.invalid"
        )

    monkeypatch.setattr(codex_accounts, "native_identity", identity)
    adapter = codex_accounts.CodexAccounts(lambda: "codex")
    store = HostAccounts(tmp_path, {"codex": adapter})
    a = store.adopt("codex")

    def login(profile):
        auth = profile.directory / "auth.json"
        auth.write_bytes(b"original B")
        auth.chmod(0o600)

    monkeypatch.setattr(adapter, "start_login", login)
    enrollment = str(uuid4())
    store.add("codex", enrollment)
    b = store.complete("codex", enrollment)
    return store, a.account_ref, b.account_ref


def test_select_persists_native_login_and_preserves_refreshed_accounts(registry, homes):
    store, a, b = registry
    home, _ = homes
    conversation = home / "sessions" / "C.jsonl"
    conversation.parent.mkdir()
    conversation.write_text("original conversation C")
    config = home / "config.toml"
    config.write_text('model = "operator-choice"')
    saved_a = store.resolve("codex").enrolled_directories
    assert any((path / "auth.json").read_bytes() == b"original A" for path in saved_a)
    (home / "auth.json").write_bytes(b"renewed A")
    assert store.select("codex", b, 0).revision == 1
    assert (home / "auth.json").read_bytes() == b"original B"
    (home / "auth.json").write_bytes(b"renewed B")
    assert store.select("codex", a, 1).revision == 2
    assert (home / "auth.json").read_bytes() == b"renewed A"
    assert store.select("codex", b, 2).revision == 3
    assert (home / "auth.json").read_bytes() == b"renewed B"
    assert conversation.read_text() == "original conversation C"
    assert config.read_text() == 'model = "operator-choice"'


def test_failed_native_swap_retains_usable_login_and_selection(registry, homes, monkeypatch):
    store, a, b = registry
    home, _ = homes
    store.select("codex", a, 0)
    real_replace = Path.replace

    def fail_swap(source, destination):
        if destination == home / "auth.json":
            raise OSError("disposable native store refused replacement")
        return real_replace(source, destination)

    monkeypatch.setattr(Path, "replace", fail_swap)
    with pytest.raises(HostAccountError, match="activation_failed"):
        store.select("codex", b, 1)
    assert (home / "auth.json").read_bytes() == b"original A"
    assert store.resolve("codex").desired.account_ref == a
    assert store.resolve("codex").desired.revision == 1


def test_foreign_native_login_is_not_saved_as_outgoing_account(registry, homes):
    store, a, b = registry
    home, _ = homes
    store.select("codex", a, 0)
    saved_a = store.resolve("codex").profile.directory / "auth.json"
    (home / "auth.json").write_bytes(b"external C")
    with pytest.raises(HostAccountError, match="activation_failed"):
        store.select("codex", b, 1)
    assert saved_a.read_bytes() == b"original A"
    assert (home / "auth.json").read_bytes() == b"external C"
    assert store.resolve("codex").desired.account_ref == a


@pytest.mark.parametrize("after_replace", [False, True])
def test_failed_selection_save_restores_native_login_and_can_retry(
    registry, homes, monkeypatch, after_replace
):
    store, a, b = registry
    home, _ = homes
    store.select("codex", a, 0)
    (home / "auth.json").write_bytes(b"renewed A")
    real_save = store._save
    failed = False

    def fail_once(state):
        nonlocal failed
        if not failed:
            failed = True
            if after_replace:
                real_save(state)
            raise OSError("disposable selection store failure")
        real_save(state)

    monkeypatch.setattr(store, "_save", fail_once)
    with pytest.raises(HostAccountError, match="account_store_unavailable"):
        store.select("codex", b, 1)
    assert (home / "auth.json").read_bytes() == b"renewed A"
    assert store.resolve("codex").desired.account_ref == a
    assert store.resolve("codex").desired.revision == 1
    assert store.select("codex", b, 1).revision == 2
    assert (home / "auth.json").read_bytes() == b"original B"
    store.select("codex", a, 2)
    assert (home / "auth.json").read_bytes() == b"renewed A"


@pytest.mark.parametrize("mode", ["keyring", "auto", "ephemeral"])
def test_unsupported_mode_keeps_native_store(registry, homes, monkeypatch, mode):
    store, _, b = registry
    home, _ = homes
    monkeypatch.setattr(
        codex_accounts, "_config", lambda *_a, **_kw: {"cli_auth_credentials_store": mode}
    )
    with pytest.raises(HostAccountError):
        store.select("codex", b, 0)
    assert (home / "auth.json").read_bytes() == b"original A"


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
