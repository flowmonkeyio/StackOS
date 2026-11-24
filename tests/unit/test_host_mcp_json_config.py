"""Owned-entry mutation checks use temporary homes, never native host settings."""

import json

import pytest

from stackos.host_mcp import json_config, service
from stackos.host_mcp.adapters import antigravity, retired_gemini

FOREIGN = {"command": "/foreign/server", "args": ["serve"]}
OWNED = {"command": "stackos", "args": ["mcp-bridge", "--runtime", "gemini-cli"]}
ANTIGRAVITY = {"command": "stackos", "args": ["mcp-bridge", "--runtime", "antigravity"]}


@pytest.mark.parametrize(
    "caller",
    [
        "repair_all",
        "remove_all",
        "register",
        "repair_registration",
        "remove",
        "remove_absent",
    ],
)
@pytest.mark.parametrize("change", [False, True])
def test_lifecycle_preserves_entry_replaced_after_ownership_inspection(
    tmp_path, monkeypatch, caller, change
):
    legacy = caller in {"repair_all", "remove_all"}
    path = tmp_path / (".gemini/settings.json" if legacy else ".gemini/config/mcp_config.json")
    path.parent.mkdir(parents=True)
    initial_entry = (
        OWNED
        if legacy
        else {
            "command": "/old/stackos",
            "args": ["mcp-bridge", "--runtime", "antigravity"],
        }
    )
    initial = {"theme": "keep", "mcpServers": {"other": {"command": "sibling"}}}
    if caller not in {"register", "remove_absent"}:
        initial["mcpServers"]["stackos"] = initial_entry
    path.write_text(json.dumps(initial))
    before_mutation = []
    is_upsert = caller in {"register", "repair_registration"}
    operation = "upsert_mcp_server" if is_upsert else "remove_mcp_server"
    writer = getattr(json_config, operation)

    def interleave(target, name, *args, **kwargs):
        current = json.loads(target.read_text())
        # A concurrent unrelated edit must survive a legitimate target mutation.
        current["new_setting"] = "preserve"
        if change:
            current["mcpServers"][name] = FOREIGN
        target.write_text(json.dumps(current))
        before_mutation.append(target.read_bytes())
        return writer(target, name, *args, **kwargs)

    owner = retired_gemini if legacy else antigravity
    monkeypatch.setattr(owner, operation, interleave)
    monkeypatch.setattr(service, "token_preflight", lambda _: None)
    monkeypatch.setattr(antigravity, "token_preflight", lambda _: None)
    monkeypatch.setattr(
        antigravity,
        "resolve_antigravity_bin",
        lambda explicit=None, *, observation=None: "/fixture/agy",
    )
    if legacy:
        monkeypatch.setattr(service, "_definitions", lambda: ())
        result = getattr(service, caller)(home=tmp_path)
    else:
        result = getattr(antigravity, "register" if is_upsert else "remove")(tmp_path)
    assert len(before_mutation) == 1
    current = json.loads(path.read_text())
    assert current["theme"] == "keep" and current["new_setting"] == "preserve"
    assert current["mcpServers"]["other"] == {"command": "sibling"}
    if change:
        assert path.read_bytes() == before_mutation[0], "foreign replacement was mutated"
        assert not result.ok
        info = result.to_info()
        assert "changed" in json.dumps(info).lower()
        assert "/foreign/server" not in json.dumps(info)
    else:
        assert result.ok
        if is_upsert:
            assert antigravity.inspect(tmp_path).managed
        else:
            assert "stackos" not in current["mcpServers"]


@pytest.mark.parametrize("action", ["register", "remove"])
def test_antigravity_refuses_entry_replaced_while_preparing_atomic_write(
    tmp_path, monkeypatch, action
):
    path = antigravity.config_path(tmp_path)
    path.parent.mkdir(parents=True)
    initial = {"mcpServers": {} if action == "register" else {"stackos": ANTIGRAVITY}}
    path.write_text(json.dumps(initial))
    monkeypatch.setattr(antigravity, "token_preflight", lambda _: None)
    monkeypatch.setattr(
        antigravity,
        "resolve_antigravity_bin",
        lambda explicit=None, *, observation=None: "/fixture/agy",
    )
    original_mkstemp = json_config.tempfile.mkstemp
    external = {"outside_change": "keep", "mcpServers": {"stackos": FOREIGN}}

    def prepare(*args, **kwargs):
        result = original_mkstemp(*args, **kwargs)
        path.write_text(json.dumps(external))
        return result

    monkeypatch.setattr(json_config.tempfile, "mkstemp", prepare)
    result = getattr(antigravity, action)(tmp_path)
    assert not result.ok and "changed" in result.message.lower()
    assert json.loads(path.read_text()) == external
    assert not list(path.parent.glob(f".{path.name}.*"))
