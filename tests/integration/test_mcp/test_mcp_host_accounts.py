"""Harness account inventory and changes share the private REST boundary."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from pydantic import ValidationError

from stackos.host_mcp import accounts, service
from stackos.host_mcp.accounts import HostAccountError, HostAccounts, NativeIdentity
from stackos.host_mcp.adapters import codex_accounts
from stackos.host_mcp.result import HostMcpResult
from stackos.operations import build_operation_registry

from .test_mcp_bridge_agent_path import _initialize, _scoped_bridge, _toolbox_call

ACCOUNT_OPERATIONS = {
    "hostMcp.listAccounts": {},
    "hostMcp.adoptAccount": {"host_key": "codex"},
    "hostMcp.addAccount": {
        "host_key": "codex",
        "enrollment_ref": "01866872-5b2a-4eb9-913a-00317acaf362",
    },
    "hostMcp.completeAccountEnrollment": {
        "host_key": "codex",
        "enrollment_ref": "01866872-5b2a-4eb9-913a-00317acaf362",
    },
    "hostMcp.cancelAccountEnrollment": {
        "host_key": "codex",
        "enrollment_ref": "01866872-5b2a-4eb9-913a-00317acaf362",
    },
    "hostMcp.selectAccount": {
        "host_key": "codex",
        "account_ref": "a6f3f6f5-4c8c-4330-aff7-fd43342f3c24",
        "expected_revision": 0,
    },
    "hostMcp.refreshAccount": {
        "host_key": "codex",
        "account_ref": "a6f3f6f5-4c8c-4330-aff7-fd43342f3c24",
    },
}

A = NativeIdentity(
    principal="native-principal-a", workspace="native-workspace-a", email="a@test.example"
)
B = NativeIdentity(
    principal="native-principal-b", workspace="native-workspace-b", email="b@test.example"
)


class FakeNativeAccounts:
    def capture(self, profile):
        self.identities[profile.ref] = self.default

    def activate(self, profile, outgoing):
        pass

    def __init__(self):
        self.default = A
        self.identities = {}
        self.started = []
        self.reads = []
        self.discarded = []
        self.retire = True
        self.start_error = False

    def read_identity(self, profile):
        self.reads.append(profile)
        if profile.directory is None:
            return self.default
        return self.identities.get(profile.ref, "needs_sign_in")

    def start_login(self, profile):
        assert profile.directory.is_dir()
        self.started.append(profile)
        if self.start_error:
            raise RuntimeError("private-native-error")

    def discard_login(self, profile):
        self.discarded.append(profile)
        return self.retire


@pytest.fixture
def adapter(monkeypatch):
    native = FakeNativeAccounts()
    definitions = tuple(
        replace(definition, account_adapter=native)
        if definition.policy.host_key == "codex"
        else definition
        for definition in service._definitions()
    )
    monkeypatch.setattr(service, "_definitions", lambda: definitions)
    return native


def _begin(store, adapter):
    ref = str(uuid4())
    assert store.add("codex", ref).status == "pending"
    return ref, adapter.started[-1]


@pytest.fixture
def account_console(mcp_client):
    client = mcp_client.test_client
    headers = {"Authorization": f"Bearer {client.app.state.ui_token}"}

    def call(verb, **arguments):
        return client.post(
            f"/api/v1/operations/hostMcp.{verb}/call",
            json={"arguments": arguments},
            headers=headers,
        )

    return call


def _account_data(response):
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _enroll_in_console(console, adapter, identity):
    arguments = {"host_key": "codex", "enrollment_ref": str(uuid4())}
    assert _account_data(console("addAccount", **arguments))["status"] == "pending"
    profile = adapter.started[-1]
    adapter.identities[profile.ref] = identity
    receipt = _account_data(console("completeAccountEnrollment", **arguments))
    return arguments, receipt, profile


def test_console_select_activates_real_disposable_codex_store(
    account_console, tmp_path, monkeypatch
):
    home = tmp_path / "native-codex"
    home.mkdir(mode=0o700)
    auth = home / "auth.json"
    auth.write_text("original A")
    auth.chmod(0o600)
    conversation = home / "conversation-C.jsonl"
    conversation.write_text("original conversation")
    config = home / "config.toml"
    config.write_text("original settings")
    monkeypatch.setattr(
        codex_accounts, "subprocess_env_for_cli", lambda _: {"CODEX_HOME": str(home)}
    )
    monkeypatch.setattr(
        codex_accounts, "_config", lambda *_a, **_k: {"cli_auth_credentials_store": "file"}
    )

    def identity(_binary, env, **_kwargs):
        data = (Path(env["CODEX_HOME"]) / "auth.json").read_text()
        return A if data.endswith("A") else B

    monkeypatch.setattr(codex_accounts, "native_identity", identity)
    native = codex_accounts.CodexAccounts(lambda: "disposable-codex")

    def login(profile):
        path = profile.directory / "auth.json"
        path.write_text("original B")
        path.chmod(0o600)

    monkeypatch.setattr(native, "start_login", login)
    definitions = tuple(
        replace(row, account_adapter=native) if row.policy.host_key == "codex" else row
        for row in service._definitions()
    )
    monkeypatch.setattr(service, "_definitions", lambda: definitions)
    host = {"host_key": "codex"}
    a = _account_data(account_console("adoptAccount", **host))["account_ref"]
    enrollment = {**host, "enrollment_ref": str(uuid4())}
    _account_data(account_console("addAccount", **enrollment))
    b = _account_data(account_console("completeAccountEnrollment", **enrollment))["account_ref"]
    auth.write_text("renewed A")
    _account_data(account_console("selectAccount", **host, account_ref=b, expected_revision=0))
    assert auth.read_text() == "original B"
    auth.write_text("renewed B")
    _account_data(account_console("selectAccount", **host, account_ref=a, expected_revision=1))
    assert auth.read_text() == "renewed A"
    _account_data(account_console("selectAccount", **host, account_ref=b, expected_revision=2))
    assert auth.read_text() == "renewed B"
    assert conversation.read_text() == "original conversation"
    assert config.read_text() == "original settings"


def test_console_selection_uses_managed_worker_boundary(account_console, adapter, monkeypatch):
    host = {"host_key": "codex"}
    account = _account_data(account_console("adoptAccount", **host))["account_ref"]
    calls = []

    def select(store, host_key, account_ref, expected_revision):
        calls.append((host_key, account_ref, expected_revision))
        return store.select(host_key, account_ref, expected_revision)

    monkeypatch.setattr(service, "select_account", select)
    response = account_console("selectAccount", **host, account_ref=account, expected_revision=0)
    assert _account_data(response) == {"account_ref": account, "revision": 1}
    assert calls == [("codex", account, 0)]


@pytest.mark.parametrize(
    "code",
    [
        "account_switch_busy",
        "account_switch_unresumable_worker",
        "account_switch_retirement_unconfirmed",
    ],
)
def test_console_worker_switch_conflict_retains_selection(
    account_console, adapter, monkeypatch, code
):
    host = {"host_key": "codex"}
    account = _account_data(account_console("adoptAccount", **host))["account_ref"]

    def conflict(*_args):
        raise HostAccountError(code)

    monkeypatch.setattr(service, "select_account", conflict)
    response = account_console("selectAccount", **host, account_ref=account, expected_revision=0)
    assert response.status_code == 409
    assert response.json()["data"]["code"] == code
    state = account_console("listAccounts").json()["hosts"]["codex"]
    assert state["desired"] == {"account_ref": None, "revision": 0}


@pytest.mark.parametrize("recover_selected", [False, True])
def test_console_recovers_expired_enrollment_profile(
    account_console, adapter, mcp_settings, recover_selected
):
    host = {"host_key": "codex"}
    a = _account_data(account_console("adoptAccount", **host))
    _, enrolled, expired_profile = _enroll_in_console(account_console, adapter, B)
    b_ref = enrolled["account_ref"]
    current_ref = b_ref if recover_selected else a["account_ref"]
    _account_data(
        account_console("selectAccount", **host, account_ref=current_ref, expected_revision=0)
    )
    adapter.identities[expired_profile.ref] = "expired"
    if recover_selected:
        expired = _account_data(account_console("refreshAccount", **host, account_ref=b_ref))
        assert expired["identity_status"] == "expired"

    arguments, recovered, replacement = _enroll_in_console(account_console, adapter, B)
    assert recovered["status"] == "enrolled" and recovered["account_ref"] == b_ref
    assert recovered["identity_status"] == "verified" and not recovered["cleanup_pending"]
    assert recovered == _account_data(account_console("completeAccountEnrollment", **arguments))
    assert recovered == _account_data(account_console("cancelAccountEnrollment", **arguments))
    assert adapter.discarded == []
    assert expired_profile.directory.is_dir()

    inventory = account_console("listAccounts").json()["hosts"]["codex"]
    revision = 2 if recover_selected else 1
    assert inventory["desired"] == {"account_ref": current_ref, "revision": revision}
    if recover_selected:
        stale = account_console("selectAccount", **host, account_ref=b_ref, expected_revision=1)
        assert stale.status_code == 409 and stale.json()["data"]["code"] == "stale_selection"
    selected = _account_data(
        account_console("selectAccount", **host, account_ref=b_ref, expected_revision=revision)
    )
    assert selected == {"account_ref": b_ref, "revision": 2}
    assert service.host_accounts(mcp_settings.data_dir).resolve("codex").profile == replacement


@pytest.mark.parametrize("retained_profile", ["expired", "missing", "healthy"])
def test_console_adopts_working_default_for_existing_account(
    account_console, adapter, mcp_settings, retained_profile
):
    _, enrolled, profile = _enroll_in_console(account_console, adapter, B)
    account_ref = enrolled["account_ref"]
    if retained_profile == "expired":
        adapter.identities[profile.ref] = "expired"
    elif retained_profile == "missing":
        profile.directory.rmdir()
    adapter.default = B

    host = {"host_key": "codex"}
    adopted = _account_data(account_console("adoptAccount", **host))
    assert (adopted["account_ref"], adopted["identity_status"]) == (account_ref, "verified")
    assert adopted == _account_data(account_console("adoptAccount", **host))
    inventory = account_console("listAccounts").json()["hosts"]["codex"]
    assert inventory["accounts"] == [adopted]
    assert inventory["desired"] == {"account_ref": None, "revision": 0}
    _account_data(
        account_console("selectAccount", **host, account_ref=account_ref, expected_revision=0)
    )
    resolved = service.host_accounts(mcp_settings.data_dir).resolve("codex")
    if retained_profile == "healthy":
        assert resolved.profile == profile
    else:
        assert resolved.profile.directory.is_dir()
        assert resolved.profile.ref != profile.ref
    assert adapter.discarded == []


@pytest.mark.parametrize(
    ("default", "response_status", "identity_status"),
    [(B, 409, "identity_changed"), ("expired", 422, "expired")],
)
def test_console_records_failed_default_verification(
    account_console, adapter, default, response_status, identity_status
):
    host = {"host_key": "codex"}
    account = _account_data(account_console("adoptAccount", **host))
    selected = _account_data(
        account_console(
            "selectAccount", **host, account_ref=account["account_ref"], expected_revision=0
        )
    )
    adapter.default = default
    response = account_console("adoptAccount", **host)
    assert response.status_code == response_status, response.text
    inventory = account_console("listAccounts").json()["hosts"]["codex"]
    assert inventory["desired"] == selected
    assert inventory["accounts"] == [{**account, "identity_status": identity_status}]


def test_console_rediscovers_and_retries_partial_cleanup(account_console, adapter, monkeypatch):
    arguments = {"host_key": "codex", "enrollment_ref": str(uuid4())}
    _account_data(account_console("addAccount", **arguments))
    initial = adapter.started[-1]
    retired = []

    def discard(profile):
        retired.append(profile)
        if len(retired) == 1:
            profile.directory.rmdir()
            return False
        assert not profile.directory.exists()
        return True

    monkeypatch.setattr(adapter, "discard_login", discard)
    cancelled = _account_data(account_console("cancelAccountEnrollment", **arguments))
    assert cancelled["status"] == "cancelled" and cancelled["cleanup_pending"]

    next_ref = str(uuid4())
    _account_data(account_console("addAccount", host_key="codex", enrollment_ref=next_ref))
    inventory = account_console("listAccounts").json()["hosts"]["codex"]
    assert inventory["pending_enrollment"]["enrollment_ref"] == next_ref
    assert inventory["pending_cleanups"] == [cancelled]
    retry = _account_data(
        account_console(
            "cancelAccountEnrollment",
            host_key="codex",
            enrollment_ref=inventory["pending_cleanups"][0]["enrollment_ref"],
        )
    )
    assert retry == {**cancelled, "cleanup_pending": False}
    assert retired == [initial, initial]
    assert _account_data(account_console("cancelAccountEnrollment", **arguments)) == retry
    inventory = account_console("listAccounts").json()["hosts"]["codex"]
    assert inventory["pending_cleanups"] == []
    assert inventory["pending_enrollment"]["enrollment_ref"] == next_ref


def test_harness_account_registry_is_rest_console_only():
    registry = build_operation_registry()
    for name in ACCOUNT_OPERATIONS:
        spec = registry.get(name, surface="rest")
        assert spec.read_only is (name == "hostMcp.listAccounts")
        assert spec.grant_policy == (
            "local-admin-console-read" if spec.read_only else "local-admin-console-write"
        )
        assert spec.surfaces.rest.browser_safe
        assert spec.surfaces.rest.path == f"/api/v1/operations/{name}/call"
        assert not spec.surfaces.mcp.enabled and not spec.surfaces.cli.enabled


def test_foreign_callers_cannot_access_harness_accounts(
    mcp_client, mcp_settings, seeded_project, adapter
):
    mcp_client.call_tool_structured(
        "workspace.connect",
        {
            "project_id": seeded_project["data"]["id"],
            "repo_fingerprint": "path:host-accounts",
            "last_known_root": "/tmp/host-accounts",
        },
    )
    proxy, bridge_client = _scoped_bridge(
        mcp_client, cwd="/tmp/host-accounts", repo_fingerprint="path:host-accounts"
    )
    _initialize(proxy, bridge_client)
    for name, arguments in ACCOUNT_OPERATIONS.items():
        response = mcp_client.test_client.post(
            f"/api/v1/operations/{name}/call", json={"arguments": arguments}
        )
        assert response.status_code == 401, response.text
        assert response.json() == {"detail": "missing bearer token"}
        denied = _toolbox_call(proxy, bridge_client, name, arguments)
        assert denied["result"]["isError"], denied
        assert name in str(denied), denied
    assert not (mcp_settings.data_dir / "host-accounts").exists()
    assert not adapter.reads and not adapter.started and not adapter.discarded


def test_console_enrolls_and_selects_through_the_registered_host_adapter(
    mcp_client, mcp_settings, adapter, monkeypatch
):
    client = mcp_client.test_client
    headers = {"Authorization": f"Bearer {client.app.state.ui_token}"}
    host = {"host_key": "codex"}

    def call(verb, arguments=None, *, extra_headers=None):
        return client.post(
            f"/api/v1/operations/hostMcp.{verb}/call",
            json={"arguments": arguments or {}},
            headers={**headers, **(extra_headers or {})},
        )

    def data(verb, arguments=None):
        response = call(verb, arguments)
        assert response.status_code == 200, response.text
        return response.json()["data"]

    empty = call("listAccounts").json()["hosts"]
    for host_key in ("codex", "claude-code"):
        assert empty[host_key] == {
            "accounts": [],
            "desired": {"account_ref": None, "revision": 0},
            "pending_enrollment": None,
            "pending_cleanups": [],
        }
    initial = service.host_accounts(mcp_settings.data_dir).resolve("codex")
    assert initial.profile is None
    assert not (mcp_settings.data_dir / "host-accounts").exists()

    enrollment = {**host, "enrollment_ref": str(uuid4())}
    for extra in (
        {"profile_path": "/private/foreign-profile"},
        {"project_id": 1},
        {"enrollment_ref": "../../foreign-profile"},
        {"run_token": "foreign-run"},
    ):
        assert call("addAccount", {**enrollment, **extra}).is_error
    assert call("adoptAccount", host, extra_headers={"X-StackOS-Client-Surface": "cli"}).is_error
    assert not (mcp_settings.data_dir / "host-accounts").exists()
    assert not adapter.reads and not adapter.started

    a = data("adoptAccount", host)
    assert a == data("adoptAccount", host)
    assert a["email"] == A.email
    assert adapter.reads[0].directory is None
    assert adapter.reads[1].directory.is_dir()
    assert adapter.reads[2].directory is None
    assert not adapter.started and not adapter.discarded
    assert data(
        "selectAccount", {**host, "account_ref": a["account_ref"], "expected_revision": 0}
    ) == {
        "account_ref": a["account_ref"],
        "revision": 1,
    }

    assert data("addAccount", enrollment) == data("addAccount", enrollment)
    assert len(adapter.started) == 1
    assert call("addAccount", {**host, "enrollment_ref": str(uuid4())}).status_code == 409
    assert len(adapter.started) == 1
    pending = data("completeAccountEnrollment", enrollment)
    assert pending["status"] == "pending"
    assert pending["identity_status"] == "needs_sign_in"
    assert pending["account_ref"] is None
    profile = adapter.started[0]
    adapter.identities[profile.ref] = "missing_email"
    assert data("completeAccountEnrollment", enrollment)["identity_status"] == "missing_email"
    with pytest.raises(ValidationError):
        NativeIdentity(principal=B.principal, workspace=B.workspace, email="invalid email")
    with pytest.raises(ValidationError):
        NativeIdentity(principal=" ", workspace=B.workspace, email=B.email)
    assert len(call("listAccounts").json()["hosts"]["codex"]["accounts"]) == 1

    adapter.identities[profile.ref] = B
    enrolled = data("completeAccountEnrollment", enrollment)
    assert enrolled["status"] == "enrolled"
    assert enrolled == data("completeAccountEnrollment", enrollment)
    assert enrolled == data("cancelAccountEnrollment", enrollment)
    b = {**host, "account_ref": enrolled["account_ref"]}
    assert call("selectAccount", {**b, "expected_revision": 0}).status_code == 409
    assert data("selectAccount", {**b, "expected_revision": 1}) == {
        "account_ref": b["account_ref"],
        "revision": 2,
    }
    assert data("selectAccount", {**b, "expected_revision": 2})["revision"] == 2
    resolved = service.host_accounts(mcp_settings.data_dir).resolve("codex")
    assert resolved.profile == profile
    assert resolved.principal_key == B.principal_key
    assert resolved.desired.revision == 2
    assert not adapter.discarded
    store = mcp_settings.data_dir / "host-accounts" / "accounts.json"
    saved = json.loads(store.read_text())["hosts"]["codex"]["accounts"][b["account_ref"]]
    assert saved == {
        "profile_ref": profile.ref,
        "native_default": False,
        "identity": B.model_dump(),
        "identity_status": "verified",
    }

    inventory = call("listAccounts").text
    assert A.email in inventory and B.email in inventory
    for private_value in (A.principal, B.workspace, profile.ref, str(profile.directory)):
        assert private_value not in inventory
    policy = next(item.policy for item in service._definitions() if item.policy.host_key == "codex")
    row = service.normalize_result(
        HostMcpResult(
            host_key="codex",
            surface="cli",
            status="registered_current",
            message="Ready",
            ok=True,
            available=True,
        ),
        policy,
    )
    monkeypatch.setattr(service, "inspect_all", lambda: service.HostMcpAggregate(True, [row]))
    status = call("status")
    assert status.status_code == 200, status.text
    assert status.json()["items"][0]["host_key"] == "codex"
    for private_value in (A.email, B.email, profile.ref, a["account_ref"], b["account_ref"]):
        assert private_value not in status.text


def test_native_identity_keys_refresh_and_restart(tmp_path):
    adapter = FakeNativeAccounts()
    store = HostAccounts(tmp_path, {"codex": adapter})
    a = store.adopt("codex")
    store.select("codex", a.account_ref, 0)
    default_profile = store.resolve("codex").profile
    assert default_profile is not None and default_profile.directory.is_dir()
    assert default_profile.ref != a.account_ref

    duplicate, profile = _begin(store, adapter)
    adapter.identities[profile.ref] = A.model_copy(update={"email": "renamed@test.example"})
    result = store.complete("codex", duplicate)
    assert (result.status, result.account_ref, result.cleanup_pending) == (
        "duplicate",
        a.account_ref,
        False,
    )
    assert adapter.discarded == [profile]
    assert store.add("codex", duplicate) == result
    assert store.resolve("codex").profile == default_profile
    assert store.resolve("codex").desired.revision == 1

    ref, profile = _begin(store, adapter)
    # The same email and principal in another workspace are a distinct identity.
    other_workspace = A.model_copy(update={"workspace": "another-workspace"})
    adapter.identities[profile.ref] = other_workspace
    restarted = HostAccounts(tmp_path, {"codex": adapter})
    assert restarted.inventory().hosts["codex"].pending_enrollment.enrollment_ref == ref
    b = restarted.complete("codex", ref)
    assert b.status == "enrolled" and b.account_ref != a.account_ref
    restarted.select("codex", b.account_ref, 1)
    assert restarted.resolve("codex").principal_key == other_workspace.principal_key

    adapter.identities[profile.ref] = "expired"
    assert store.refresh("codex", b.account_ref).identity_status == "expired"
    with pytest.raises(HostAccountError, match="identity_unverified") as rejected:
        store.select("codex", b.account_ref, 2)
    assert rejected.value.status == "expired"
    assert store.resolve("codex").desired.account_ref == b.account_ref
    assert store.resolve("codex").desired.revision == 2

    adapter.identities[profile.ref] = B
    changed = store.refresh("codex", b.account_ref)
    assert (changed.identity_status, changed.email) == ("identity_changed", A.email)
    assert store.resolve("codex").principal_key == other_workspace.principal_key
    adapter.identities[profile.ref] = other_workspace.model_copy(
        update={"email": "new@test.example"}
    )
    assert restarted.refresh("codex", b.account_ref).email == "new@test.example"
    assert restarted.select("codex", a.account_ref, 2).revision == 3
    assert restarted.resolve("codex").profile == default_profile

    adapter.default = B
    with pytest.raises(HostAccountError, match="default_profile_changed"):
        store.adopt("codex")
    assert restarted.resolve("codex").principal_key == A.principal_key


def test_enrollment_cancel_expiry_and_native_failure_are_terminal(tmp_path, monkeypatch):
    now = datetime(2026, 1, 1, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz == UTC
            return now

    monkeypatch.setattr(accounts, "datetime", Clock)
    adapter = FakeNativeAccounts()
    store = HostAccounts(tmp_path, {"codex": adapter})
    a = store.adopt("codex")
    store.select("codex", a.account_ref, 0)

    ref, profile = _begin(store, adapter)
    adapter.retire = False
    cancelled = store.cancel("codex", ref)
    assert cancelled.status == "cancelled" and cancelled.cleanup_pending
    adapter.identities[profile.ref] = B
    assert store.complete("codex", ref).status == "cancelled"
    adapter.retire = True
    assert not store.cancel("codex", ref).cleanup_pending
    assert adapter.discarded == [profile, profile]

    # The durable deadline covers native login that finishes too late.
    ref, profile = _begin(store, adapter)
    now += timedelta(minutes=15)
    adapter.identities[profile.ref] = B
    assert store.complete("codex", ref).status == "expired"
    assert adapter.discarded[-1] == profile

    ref, profile = _begin(store, adapter)

    def late_identity(_profile):
        nonlocal now
        now += timedelta(minutes=15)
        return B

    with monkeypatch.context() as patch:
        patch.setattr(adapter, "read_identity", late_identity)
        late = store.complete("codex", ref)
        assert late.status == "expired" and late.account_ref is None
    assert adapter.discarded[-1] == profile

    ref, profile = _begin(store, adapter)
    adapter.identities[profile.ref] = A
    read_identity = adapter.read_identity

    def late_retained_identity(context):
        nonlocal now
        if context.ref != profile.ref:
            now += timedelta(minutes=15)
        return read_identity(context)

    with monkeypatch.context() as patch:
        patch.setattr(adapter, "read_identity", late_retained_identity)
        late = store.complete("codex", ref)
        assert late.status == "expired" and late.account_ref is None
    assert adapter.discarded[-1] == profile

    adapter.start_error = True
    failed = store.add("codex", str(uuid4()))
    assert failed.status == "failed" and failed.identity_status == "unavailable"
    assert "private-native-error" not in failed.model_dump_json()
    assert store.inventory().hosts["codex"].accounts == [a]
    assert store.resolve("codex").desired.revision == 1


def test_competing_same_generation_switches_commit_once(tmp_path):
    adapter = FakeNativeAccounts()
    store = HostAccounts(tmp_path, {"codex": adapter})
    a = store.adopt("codex")
    ref, profile = _begin(store, adapter)
    adapter.identities[profile.ref] = B
    b = store.complete("codex", ref)
    synchronized = Barrier(2)

    def switch(account_ref):
        separate_service = HostAccounts(tmp_path, {"codex": adapter})
        synchronized.wait(timeout=5)
        try:
            return separate_service.select("codex", account_ref, 0)
        except HostAccountError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(switch, [a.account_ref, b.account_ref]))
    assert results.count("stale_selection") == 1
    desired = store.resolve("codex").desired
    assert desired.revision == 1
    assert desired in results
    assert adapter.reads[-1].ref == store.resolve("codex").profile.ref


def test_isolated_profile_and_state_reject_symlinks_and_public_modes(tmp_path):
    adapter = FakeNativeAccounts()
    store = HostAccounts(tmp_path, {"codex": adapter})
    ref, profile = _begin(store, adapter)
    outside = tmp_path / "foreign"
    outside.mkdir(mode=0o700)
    profile.directory.rmdir()
    profile.directory.symlink_to(outside, target_is_directory=True)
    with pytest.raises(HostAccountError, match="unsafe_profile"):
        store.complete("codex", ref)
    assert not adapter.reads
    assert store.cancel("codex", ref).cleanup_pending
    assert not adapter.discarded
    assert list(outside.iterdir()) == []

    profile.directory.unlink()
    profile.directory.symlink_to(outside / "missing", target_is_directory=True)
    assert store.cancel("codex", ref).cleanup_pending
    assert not adapter.discarded

    profile.directory.unlink()
    profile.directory.mkdir(mode=0o755)
    assert store.cancel("codex", ref).cleanup_pending
    assert not adapter.discarded

    state = tmp_path / "host-accounts" / "accounts.json"
    assert state.stat().st_mode & 0o777 == 0o600
    copied = outside / "accounts.json"
    state.rename(copied)
    state.symlink_to(copied)
    original = copied.read_bytes()
    with pytest.raises(HostAccountError, match="account_store_unavailable"):
        store.adopt("codex")
    assert copied.read_bytes() == original


def test_invalid_stored_profile_ref_never_reaches_native_adapter(tmp_path):
    adapter = FakeNativeAccounts()
    store = HostAccounts(tmp_path, {"codex": adapter})
    adopted = store.adopt("codex")
    store.select("codex", adopted.account_ref, 0)
    path = Path(tmp_path, "host-accounts", "accounts.json")
    state = json.loads(path.read_text())
    row = state["hosts"]["codex"]["accounts"][adopted.account_ref]
    row["profile_ref"] = "../../sibling-profile"
    path.write_text(json.dumps(state))
    with pytest.raises(HostAccountError, match="account_store_unavailable"):
        store.resolve("codex")
    assert len(adapter.reads) == 3
