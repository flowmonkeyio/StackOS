"""Native Account setup and MCP probe retain Account-local public CA trust."""

import json
import ssl

from tests.helpers.imap_tls import certificates

from .conftest import MCPClient


def test_native_imap_account_ca_reaches_mcp_probe(
    mcp_client: MCPClient, seeded_project: dict, monkeypatch
) -> None:
    ca, _leaf, _key = certificates()
    observed: list[ssl.SSLContext] = []

    class Probe:
        def __init__(self, _host, _port, *, ssl_context, timeout):
            observed.append(ssl_context)

        def login(self, _username, _password):
            return "OK", []

        def select(self, _mailbox, *, readonly):
            assert readonly is True
            return "OK", []

        def logout(self):
            return "BYE", []

    monkeypatch.setattr("stackos.integrations.imap.imaplib.IMAP4_SSL", Probe)
    created = mcp_client.test_client.post(
        "/api/v1/auth/accounts/imap",
        json={
            "auth_method_key": "imap-password",
            "display_name": "Synthetic custom CA",
            "attach_project_id": seeded_project["data"]["id"],
            "fields": {
                "host": "mail.example.test",
                "port": 993,
                "tls_mode": "ssl",
                "username": "synthetic-user",
                "password": "synthetic-password",
                "tls_ca_pem": ca,
            },
        },
        headers=mcp_client._headers(),
    )
    created.raise_for_status()
    credential_ref = created.json()["data"]["credential_ref"]
    out = mcp_client.call_tool_structured(
        "account.test",
        {"credential_ref": credential_ref, "response_mode": "raw"},
    )
    assert out["data"]["ok"] is True
    assert len(observed) == 1
    assert ssl.PEM_cert_to_DER_cert(ca) in observed[0].get_ca_certs(binary_form=True)
    assert observed[0].check_hostname is True
    assert observed[0].verify_mode == ssl.CERT_REQUIRED
    assert "synthetic-password" not in json.dumps(out)
