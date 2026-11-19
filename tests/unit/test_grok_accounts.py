from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from stackos.host_mcp.accounts import (
    HostAccountError,
    HostAccounts,
    NativeAccountProfile,
    NativeIdentity,
)
from stackos.host_mcp.adapters import grok_accounts as grok


def credential(path, principal, *, refreshed=False):
    # Multiple scopes and opaque unknown fields must survive byte-for-byte.
    data = (
        json.dumps(
            {
                "other": {"principalId": "wrong"},
                "chosen": {
                    "principalId": principal,
                    "principalType": "user",
                    "methodId": "cached_token",
                    "email": principal + "@example.invalid",
                    "teamId": "team",
                    "organizationId": None,
                    "refresh_token": "renewed" if refreshed else "original",
                    "future": [1, 2],
                },
            }
        ).encode()
        + b"\n"
    )
    path.write_bytes(data)
    path.chmod(0o600)
    return data


@pytest.fixture
def registry(tmp_path, monkeypatch):
    home = tmp_path / "ordinary"
    home.mkdir(mode=0o700)
    credential(home / "auth.json", "a")
    binary = tmp_path / "native-grok"
    binary.write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys
for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    if method == "initialize":
        result = {"protocolVersion": 1}
    elif method == "_x.ai/auth/info":
        path = os.environ.get("GROK_AUTH_PATH")
        path = path or str(pathlib.Path(os.environ["GROK_HOME"]) / "auth.json")
        result = (json.loads(pathlib.Path(path).read_text())["chosen"]
                  if pathlib.Path(path).exists() else {"methodId": "grok.com"})
    else:
        raise RuntimeError("unexpected conversation or model request")
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
""")
    binary.chmod(0o700)
    monkeypatch.setattr(grok, "subprocess_env_for_cli", lambda _: {"GROK_HOME": str(home)})
    adapter = grok.GrokAccounts(lambda: str(binary))
    store = HostAccounts(tmp_path, {"grok": adapter})
    a = store.adopt("grok").account_ref

    def login(profile):
        credential(profile.directory / "auth.json", "b")

    monkeypatch.setattr(adapter, "start_login", login)
    enrollment = str(uuid4())
    store.add("grok", enrollment)
    b = store.complete("grok", enrollment).account_ref
    return store, home, a, b, adapter


def test_native_transport_swap_preserves_outgoing_refresh_and_home(registry):
    store, home, a, b, _ = registry
    config, conversation = home / "config.toml", home / "conversation.jsonl"
    config.write_text("operator config")
    conversation.write_text("conversation C")
    refreshed_a = credential(home / "auth.json", "a", refreshed=True)
    assert store.select("grok", b, 0).revision == 1
    assert json.loads((home / "auth.json").read_bytes())["chosen"]["principalId"] == "b"
    refreshed_b = credential(home / "auth.json", "b", refreshed=True)
    assert store.select("grok", a, 1).revision == 2
    assert (home / "auth.json").read_bytes() == refreshed_a
    assert store.select("grok", b, 2).revision == 3
    assert (home / "auth.json").read_bytes() == refreshed_b
    assert config.read_text() == "operator config"
    assert conversation.read_text() == "conversation C"


def test_failed_atomic_swap_keeps_native_auth_and_selection(registry, monkeypatch):
    store, home, a, b, _ = registry
    store.select("grok", a, 0)
    original = (home / "auth.json").read_bytes()
    replace = Path.replace

    def fail(source, destination):
        if destination == home / "auth.json":
            raise OSError("fixture refuses replacement")
        return replace(source, destination)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(HostAccountError, match="activation_failed"):
        store.select("grok", b, 1)
    assert (home / "auth.json").read_bytes() == original
    selection = store.resolve("grok").desired
    assert (selection.account_ref, selection.revision) == (a, 1)
    assert sorted(path.name for path in home.iterdir()) == ["auth.json"]


def test_foreign_native_principal_does_not_overwrite_outgoing(registry):
    store, home, a, b, _ = registry
    store.select("grok", a, 0)
    saved = store.resolve("grok").profile.directory / "auth.json"
    original = saved.read_bytes()
    foreign = credential(home / "auth.json", "foreign")
    with pytest.raises(HostAccountError, match="activation_failed"):
        store.select("grok", b, 1)
    assert saved.read_bytes() == original
    assert (home / "auth.json").read_bytes() == foreign
    assert store.resolve("grok").desired.account_ref == a


@pytest.mark.parametrize("key", ["GROK_AUTH", "XAI_API_KEY", "GROK_CODE_XAI_API_KEY"])
def test_competing_auth_rejected_before_native_invocation(registry, monkeypatch, key):
    store, home, _, b, _ = registry
    original = (home / "auth.json").read_bytes()
    monkeypatch.setattr(
        grok,
        "subprocess_env_for_cli",
        lambda _: {"GROK_HOME": str(home), key: "private competing credential"},
    )
    with pytest.raises(HostAccountError):
        store.select("grok", b, 0)
    assert (home / "auth.json").read_bytes() == original


def test_path_precedence_and_empty_override(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert grok._auth_path({}) == tmp_path / ".grok" / "auth.json"
    assert (
        grok._auth_path({"GROK_HOME": str(tmp_path), "GROK_AUTH_PATH": ""})
        == tmp_path / "auth.json"
    )
    explicit = tmp_path / "custom-auth"
    assert grok._auth_path({"GROK_HOME": "/unused", "GROK_AUTH_PATH": str(explicit)}) == explicit


def test_identity_uses_native_response_not_first_stored_scope(registry):
    _, _, _, _, adapter = registry
    identity = adapter.read_identity(NativeAccountProfile(None, None))
    assert isinstance(identity, NativeIdentity)
    assert json.loads(identity.principal) == ["user", "a"]
    assert json.loads(identity.workspace) == [None, "team"]


def test_identity_rejects_api_key_and_missing_principal(monkeypatch):
    def response(info):
        monkeypatch.setattr(
            grok.native, "read_rpc", lambda *_a, **_k: [{"result": {}}, {"result": info}]
        )
        return grok.native_identity("grok", {})

    assert (
        response({"methodId": "xai.api_key", "email": "x@example.invalid"})
        == "conflicting_credentials"
    )
    assert response({"methodId": "cached_token", "email": "x@example.invalid"}) == "unavailable"
    assert response({"methodId": "cached_token"}) == "missing_email"


def test_enrollment_retains_home_and_uses_isolated_auth(tmp_path, monkeypatch):
    home, profile_dir = tmp_path / "ordinary", tmp_path / "private"
    home.mkdir(mode=0o700)
    profile_dir.mkdir(mode=0o700)
    monkeypatch.setattr(
        grok,
        "subprocess_env_for_cli",
        lambda _: {"GROK_HOME": str(home), "GROK_AUTH_PATH": str(home / "custom")},
    )
    observed = {}

    class Control:
        def spawn(self, command, **kwargs):
            observed.update(command=command, **kwargs)
            return self

        def wait(self, **kwargs):
            return 0

        def stop(self):
            return "stopped"

    class Thread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(grok, "ManagedProcessControl", Control)
    monkeypatch.setattr(grok, "Thread", Thread)
    adapter = grok.GrokAccounts(lambda: "grok")
    profile = NativeAccountProfile("fixture", profile_dir)
    adapter.start_login(profile)
    assert observed["command"] == ["grok", "login", "--oauth"]
    assert observed["env"]["GROK_HOME"] == str(home)
    assert observed["env"]["GROK_AUTH_PATH"] == str(profile_dir / "auth.json")
    assert adapter.discard_login(profile)


def test_service_has_grok_account_binding():
    from stackos.host_mcp import service

    assert isinstance(service._definition_by_key("grok").account_adapter, grok.GrokAccounts)


def test_reselect_retains_refreshed_credentials(registry):
    store, home, a, _, _ = registry
    refreshed = credential(home / "auth.json", "a", refreshed=True)
    store.select("grok", a, 0)
    assert (home / "auth.json").read_bytes() == refreshed
    assert (store.resolve("grok").profile.directory / "auth.json").read_bytes() == refreshed


def test_explicit_auth_path_is_the_only_swapped_file(registry, monkeypatch):
    store, home, _, b, _ = registry
    original = (home / "auth.json").read_bytes()
    explicit = home / "explicit-auth.json"
    credential(explicit, "a", refreshed=True)
    monkeypatch.setattr(
        grok,
        "subprocess_env_for_cli",
        lambda _: {"GROK_HOME": str(home), "GROK_AUTH_PATH": str(explicit)},
    )
    store.select("grok", b, 0)
    assert json.loads(explicit.read_bytes())["chosen"]["principalId"] == "b"
    assert (home / "auth.json").read_bytes() == original


@pytest.mark.parametrize("unsafe", ["symlink", "public", "hardlink"])
def test_unsafe_native_auth_rejected_without_selection(registry, unsafe):
    store, home, _, b, _ = registry
    path = home / "auth.json"
    original = path.read_bytes()
    if unsafe == "symlink":
        target = home / "elsewhere"
        path.rename(target)
        path.symlink_to(target)
    elif unsafe == "hardlink":
        (home / "elsewhere").hardlink_to(path)
    else:
        path.chmod(0o644)
    with pytest.raises(HostAccountError):
        store.select("grok", b, 0)
    assert path.read_bytes() == original


def test_changed_native_credentials_during_identity_check_are_not_saved(registry, monkeypatch):
    store, home, a, b, _ = registry
    store.select("grok", a, 0)
    saved = store.resolve("grok").profile.directory / "auth.json"
    original = saved.read_bytes()
    native_identity = grok.native_identity

    def change(binary, env):
        identity = native_identity(binary, env)
        if "GROK_AUTH_PATH" not in env:
            credential(home / "auth.json", "foreign")
        return identity

    monkeypatch.setattr(grok, "native_identity", change)
    with pytest.raises(HostAccountError, match="activation_failed"):
        store.select("grok", b, 1)
    assert saved.read_bytes() == original
    assert store.resolve("grok").desired.account_ref == a


def test_first_activation_and_internal_rollback_preserve_saved_login(registry):
    _, home, _, _, adapter = registry
    target = home.parent / "first-profile"
    target.mkdir(mode=0o700)
    selected = credential(target / "auth.json", "b")
    profile = NativeAccountProfile("first", target)
    (home / "auth.json").unlink()
    assert adapter.read_identity(NativeAccountProfile(None, None)) == "needs_sign_in"
    adapter.activate(profile, None)
    assert (home / "auth.json").read_bytes() == selected
    refreshed = credential(home / "auth.json", "b", refreshed=True)
    adapter.activate(None, profile)
    assert not (home / "auth.json").exists()
    assert (target / "auth.json").read_bytes() == refreshed


def test_first_activation_rejects_existing_unadopted_login(registry):
    _, home, _, _, adapter = registry
    target = home.parent / "first-profile"
    target.mkdir(mode=0o700)
    credential(target / "auth.json", "b")
    original = (home / "auth.json").read_bytes()
    with pytest.raises(grok.GrokAccountError, match="Adopt"):
        adapter.activate(NativeAccountProfile("first", target), None)
    assert (home / "auth.json").read_bytes() == original
