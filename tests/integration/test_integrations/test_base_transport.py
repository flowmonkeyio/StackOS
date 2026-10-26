"""Per-call raw transport keeps existing bodies and audit semantics."""

import asyncio
from unittest.mock import Mock

import httpx
import pytest
from stackos_connectors.errors import IntegrationDownError
from stackos_connectors.shared.base import BaseIntegration

from stackos.actions.connectors import ActionConnectorResult
from stackos.repositories.base import ConflictError

URL = "https://provider.example/test"


def call(project_id, *, audit=None, **kwargs):
    async def run():
        async with httpx.AsyncClient() as http:
            wrapper = BaseIntegration(
                payload=b"",
                http=http,
            )
            if audit is not None:

                async def execute(_request):
                    result = await wrapper.call(op="test", url=URL, **kwargs)
                    return ActionConnectorResult(output_json=result.data)

                connector = Mock(key="dataforseo", execute=execute)
                row = await audit.execute(
                    "dataforseo",
                    "serp.analyze",
                    kwargs.get("request_log_body") or {},
                    secret_payload=b"p",
                    config={"login": "u"},
                    connector_override=connector,
                )
                return row
            return await wrapper.call(op="test", url=URL, **kwargs)

    return asyncio.run(run())


@pytest.mark.parametrize("body", [{"json_body": {}}, {"data_body": {}}, {"files": {}}])
def test_raw_body_conflicts_reject_before_http(project_id, httpx_mock, body):
    with pytest.raises(ValueError, match="cannot be combined"):
        call(project_id, content=b"raw", **body)
    assert not httpx_mock.get_requests()
    # Host budget admission is exercised at ActionRepository in test_dataforseo.


@pytest.mark.parametrize(
    "body,wire",
    [
        ({"json_body": {"a": 1}}, b'{"a":1}'),
        ({"data_body": {"a": "one two"}}, b"a=one+two"),
        ({}, b""),
    ],
)
@pytest.mark.parametrize("response", [{"json": {"ok": True}}, {"text": "text output"}])
def test_existing_body_and_json_text_defaults(project_id, httpx_mock, body, wire, response):
    httpx_mock.add_response(url=URL, **response)
    result = call(project_id, **body)
    assert result.data == next(iter(response.values()))
    assert httpx_mock.get_requests()[0].content == wire


def test_raw_parser_runs_before_cost_metadata_and_audit(project_id, httpx_mock, host_audit):
    audit = host_audit
    httpx_mock.add_response(url=URL, content=b"raw-provider-bytes")
    result = call(
        project_id,
        content=b"raw-request-bytes",
        request_log_body={"count": 1},
        response_parser=lambda response: {"parsed": response.status_code},
        audit=audit,
    )
    assert result.response_json == {"parsed": 200}
    record = audit.record_call.call_args.kwargs
    assert record["request_json"] == {"count": 1}
    assert record["response_json"] == {"parsed": 200}
    assert httpx_mock.get_requests()[0].content == b"raw-request-bytes"


def test_typed_parser_failure_is_audited_without_text_fallback(project_id, httpx_mock, host_audit):
    audit = host_audit
    httpx_mock.add_response(url=URL, content=b"private raw bytes")

    def parse(_response):
        raise IntegrationDownError("Malformed provider response", data={"reason": "parse"})

    with pytest.raises(ConflictError, match="action connector failed") as caught:
        call(project_id, response_parser=parse, audit=audit)
    assert isinstance(caught.value.__cause__, IntegrationDownError)
    assert "Malformed" in caught.value.__cause__.detail
    record = audit.record_call.call_args.kwargs
    assert record["error"]
    assert "private raw bytes" not in str(record)
