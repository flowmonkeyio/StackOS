"""Fail closed on malformed daemon replies without exposing their contents."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from stackos.mcp.bridge.protocol import BridgeProtocolError
from stackos.mcp.bridge.proxy import AgentBridgeProxy


class _Client:
    def __init__(self, text: str, *, status_code: int = 200) -> None:
        self.response = httpx.Response(
            status_code,
            text=text,
            request=httpx.Request("POST", "http://daemon.test/mcp"),
        )
        self.calls: list[dict[str, Any]] = []

    def post(self, _url: str, *, content: str, headers: dict[str, str]) -> httpx.Response:
        del headers
        self.calls.append(json.loads(content))
        return self.response


def _request(request_id: object = 1, *, method: str = "initialize") -> str:
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": {}})


def _proxy(**kwargs: Any) -> AgentBridgeProxy:
    return AgentBridgeProxy(url="http://daemon.test/mcp", headers={}, **kwargs)


@pytest.mark.parametrize("request_id", [0, 1, "initialize", None])
def test_valid_response_preserves_matching_id_and_negotiates_protocol(request_id: object) -> None:
    text = json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2025-11-25"}}
    )
    proxy = _proxy()
    client = _Client(f" \n{text}\n ")

    assert proxy.request_daemon(client, _request(request_id)) == text
    assert proxy.protocol_version == "2025-11-25"
    assert len(client.calls) == 1


@pytest.mark.parametrize("result", [None, {}, [], "value", 0, False])
def test_validation_checks_envelope_not_method_specific_result(result: object) -> None:
    text = json.dumps({"jsonrpc": "2.0", "id": 1, "result": result})

    assert _proxy().request_daemon(_Client(text), _request(method="ping")) == text


def test_valid_json_rpc_error_is_forwarded_unchanged() -> None:
    text = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32601, "message": "Method not found", "data": {"retry": False}},
        }
    )

    assert _proxy().request_daemon(_Client(text), _request()) == text


@pytest.mark.parametrize("status_code", [200, 202, 204])
@pytest.mark.parametrize("text", ["", " \r\n "])
def test_empty_notification_acknowledgement_is_preserved(status_code: int, text: str) -> None:
    body = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
    client = _Client(text, status_code=status_code)

    assert _proxy().request_daemon(client, body) == ""
    assert len(client.calls) == 1


@pytest.mark.parametrize("response_id", [None, 1, "another-request"])
def test_notification_cannot_forward_an_unsolicited_response(response_id: object) -> None:
    body = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
    client = _Client(json.dumps({"jsonrpc": "2.0", "id": response_id, "result": {}}))

    with pytest.raises(BridgeProtocolError) as raised:
        _proxy().request_daemon(client, body)

    assert raised.value.reason == "invalid_envelope"


def test_daemon_notification_cannot_replace_a_request_response() -> None:
    client = _Client(json.dumps({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}))

    with pytest.raises(BridgeProtocolError) as raised:
        _proxy().request_daemon(client, _request())

    assert raised.value.reason == "invalid_envelope"


@pytest.mark.parametrize("status_code", [200, 204])
def test_empty_request_response_is_rejected(status_code: int) -> None:
    client = _Client("", status_code=status_code)

    with pytest.raises(BridgeProtocolError) as raised:
        _proxy().request_daemon(client, _request())

    assert raised.value.reason == "empty_response"
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("<html>secret-response</html>", "invalid_json"),
        ('{"jsonrpc": "2.0", "id": 1, "result":', "invalid_json"),
        ('{"jsonrpc": "2.0", "id": 1, "result": NaN}', "invalid_json"),
        ('{"jsonrpc": "2.0", "id": 1, "result": Infinity}', "invalid_json"),
        ("null", "invalid_envelope"),
        ("[]", "invalid_envelope"),
        ('"secret-response"', "invalid_envelope"),
        ('{"id": 1, "result": {}}', "invalid_envelope"),
        ('{"jsonrpc": "1.0", "id": 1, "result": {}}', "invalid_envelope"),
        ('{"jsonrpc": "2.0", "result": {}}', "invalid_envelope"),
        ('{"jsonrpc": "2.0", "id": 1}', "invalid_envelope"),
        ('{"jsonrpc": "2.0", "id": 1, "result": {}, "error": {}}', "invalid_envelope"),
        ('{"jsonrpc": "2.0", "id": 1, "result": {}, "method": "ping"}', "invalid_envelope"),
        ('{"jsonrpc": "2.0", "id": true, "result": {}}', "invalid_envelope"),
        ('{"jsonrpc": "2.0", "id": [], "result": {}}', "invalid_envelope"),
        ('{"jsonrpc": "2.0", "id": 1e999, "result": {}}', "invalid_envelope"),
        ('{"jsonrpc": "2.0", "id": "1", "result": {}}', "mismatched_id"),
        ('{"jsonrpc": "2.0", "id": "secret-response", "result": {}}', "mismatched_id"),
        ('{"jsonrpc": "2.0", "id": null, "result": {}}', "mismatched_id"),
    ],
)
def test_malformed_response_is_classified_without_body_or_retry(text: str, reason: str) -> None:
    client = _Client(text)
    proxy = _proxy()
    proxy.protocol_version = "2025-11-25"

    with pytest.raises(BridgeProtocolError) as raised:
        proxy.request_daemon(client, _request())

    assert raised.value.reason == reason
    assert str(raised.value) == "Daemon returned an invalid JSON-RPC response."
    assert "secret-response" not in repr(raised.value)
    assert proxy.protocol_version == "2025-11-25"
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "error",
    [
        None,
        [],
        {},
        {"code": -32603},
        {"message": "secret"},
        {"code": True, "message": "secret"},
        {"code": -32603.0, "message": "secret"},
        {"code": -32603, "message": {"secret": True}},
    ],
)
def test_malformed_error_envelope_is_rejected(error: object) -> None:
    client = _Client(json.dumps({"jsonrpc": "2.0", "id": 1, "error": error}))

    with pytest.raises(BridgeProtocolError) as raised:
        _proxy().request_daemon(client, _request())

    assert raised.value.reason == "invalid_envelope"
    assert "secret" not in str(raised.value)


@pytest.mark.parametrize(
    "frame",
    [
        'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\n\n',
        'data: {"jsonrpc":"2.0","id":1,"result":{}}\n\n',
        ": keep-alive\n\nid: 7\nretry: 1000\nevent: message\n"
        'data: {"jsonrpc":"2.0",\ndata: "id":1,"result":{}}\n\n',
        'event: message\r\ndata:{"jsonrpc":"2.0","id":1,"result":{}}\r\n\r\n',
    ],
)
def test_valid_single_event_sse_is_normalized(frame: str) -> None:
    result = _proxy().request_daemon(_Client(frame), _request())

    assert json.loads(result) == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert "\n" not in result
    assert "\r" not in result


@pytest.mark.parametrize("newline", ["\n", "\r", "\r\n"])
def test_multiline_json_is_one_stdio_line_without_changing_string_values(newline: str) -> None:
    envelope = {"jsonrpc": "2.0", "id": 1, "result": {"text": "first\nsecond\rthird"}}
    text = json.dumps(envelope, indent=2).replace("\n", newline)

    result = _proxy().request_daemon(_Client(text), _request())

    assert json.loads(result) == envelope
    assert "\n" not in result
    assert "\r" not in result


def test_single_line_json_keeps_exact_formatting() -> None:
    text = '{ "jsonrpc": "2.0", "id":1, "result": {"text":"first\\nsecond"} }'

    assert _proxy().request_daemon(_Client(text), _request()) == text


@pytest.mark.parametrize(
    "text",
    [
        "event: message\ndata: secret-response\n\n",
        "event: message\n\n",
        'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{}}\n\n',
        'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\n\n'
        'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{}}\n\n',
    ],
)
def test_invalid_or_ambiguous_sse_does_not_escape_validation(text: str) -> None:
    with pytest.raises(BridgeProtocolError):
        _proxy().request_daemon(_Client(text), _request())


@pytest.mark.parametrize("status_code", [401, 403, 404, 500, 503])
def test_http_failures_remain_transport_errors(status_code: int) -> None:
    client = _Client("not JSON", status_code=status_code)

    with pytest.raises(httpx.HTTPStatusError) as raised:
        _proxy().request_daemon(client, _request())

    assert raised.value.response.status_code == status_code
    assert len(client.calls) == 1


def test_protocol_exception_reason_cannot_leak_arbitrary_text() -> None:
    error = BridgeProtocolError("secret-payload")

    assert error.reason == "invalid_envelope"
    assert "secret-payload" not in str(error)
    assert "secret-payload" not in repr(error)


@pytest.mark.parametrize("already_bound", [False, True])
def test_malformed_scope_resolution_preserves_workspace_protection(
    already_bound: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    proxy = _proxy(cwd="/workspace/project", deliberate_workspace_root=True)
    if already_bound:
        proxy.scoped_project_id = 7
        proxy.global_session = False
    client = _Client("secret-response-with-token-and-path")

    proxy._ensure_workspace_scope(client)

    assert proxy.workspace_scope_checked is False
    assert proxy.workspace_scope_error == "workspace.resolve failed; retry startup diagnostics"
    assert proxy.scoped_project_id == (7 if already_bound else None)
    assert proxy.global_session is (False if already_bound else None)
    _, error = proxy._scope_workspace_arguments("workspace.startSession", {"global_session": True})
    assert error is not None
    assert len(client.calls) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert "secret-response-with-token-and-path" not in captured.err
    event = json.loads(captured.err)
    assert event["phase"] == "workspace_scope"
    assert event["original"]["code"] == "invalid_daemon_response"
    assert event["original"]["reason"] == "invalid_json"
    assert event["autostart"] == "not_attempted"
