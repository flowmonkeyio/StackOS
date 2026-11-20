"""The local REST account consumer selects the ordinary Grok credential file."""

import json
from dataclasses import replace
from uuid import uuid4

from stackos.host_mcp import service
from stackos.host_mcp.accounts import NativeIdentity
from stackos.host_mcp.adapters import grok_accounts


def test_console_swaps_grok_credentials_without_changing_home(mcp_client, tmp_path, monkeypatch):
    home = tmp_path / "native-grok"
    home.mkdir(mode=0o700)
    auth = home / "auth.json"
    config, conversation = home / "config.toml", home / "conversation.jsonl"
    config.write_text("operator settings")
    conversation.write_text("conversation C")

    def write(path, principal, refresh="original"):
        data = json.dumps({"scope": {"principal": principal, "opaque": refresh}}).encode()
        path.write_bytes(data)
        path.chmod(0o600)
        return data

    original_a = write(auth, "a")
    monkeypatch.setattr(grok_accounts, "subprocess_env_for_cli", lambda _: {"GROK_HOME": str(home)})

    def identity(binary, env):
        path = grok_accounts._auth_path(env)
        principal = json.loads(path.read_bytes())["scope"]["principal"]
        return NativeIdentity(principal=principal, email=principal + "@example.invalid")

    # Native protocol/identity decoding has separate subprocess coverage. This
    # consumer fixture substitutes only that read and first-party browser login.
    monkeypatch.setattr(grok_accounts, "native_identity", identity)
    adapter = grok_accounts.GrokAccounts(lambda: "fixture-grok")
    monkeypatch.setattr(
        adapter, "start_login", lambda profile: write(profile.directory / "auth.json", "b")
    )
    definitions = tuple(
        replace(row, account_adapter=adapter) if row.policy.host_key == "grok" else row
        for row in service._definitions()
    )
    monkeypatch.setattr(service, "_definitions", lambda: definitions)
    client = mcp_client.test_client
    headers = {"Authorization": f"Bearer {client.app.state.ui_token}"}

    def call(verb, *, authorized=True, **arguments):
        return client.post(
            f"/api/v1/operations/hostMcp.{verb}/call",
            json={"arguments": {"host_key": "grok", **arguments}},
            headers=headers if authorized else {},
        )

    def data(verb, **arguments):
        response = call(verb, **arguments)
        assert response.status_code == 200, response.text
        return response.json()["data"]

    a = data("adoptAccount")["account_ref"]
    enrollment_ref = str(uuid4())
    data("addAccount", enrollment_ref=enrollment_ref)
    b = data("completeAccountEnrollment", enrollment_ref=enrollment_ref)["account_ref"]
    denied = call("selectAccount", authorized=False, account_ref=b, expected_revision=0)
    assert denied.status_code == 401
    assert denied.json() == {"detail": "missing bearer token"}
    assert auth.read_bytes() == original_a
    refreshed_a = write(auth, "a", "refreshed-A")
    assert data("selectAccount", account_ref=b, expected_revision=0)["revision"] == 1
    assert json.loads(auth.read_bytes())["scope"]["principal"] == "b"
    refreshed_b = write(auth, "b", "refreshed-B")
    assert data("selectAccount", account_ref=a, expected_revision=1)["revision"] == 2
    assert auth.read_bytes() == refreshed_a
    assert data("selectAccount", account_ref=b, expected_revision=2)["revision"] == 3
    assert auth.read_bytes() == refreshed_b
    assert config.read_text() == "operator settings"
    assert conversation.read_text() == "conversation C"
    response = client.post(
        "/api/v1/operations/hostMcp.listAccounts/call", json={"arguments": {}}, headers=headers
    )
    assert response.status_code == 200
    assert "refreshed-A" not in response.text and "refreshed-B" not in response.text
    assert str(home) not in response.text
