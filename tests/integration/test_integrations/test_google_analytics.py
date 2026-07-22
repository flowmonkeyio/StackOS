"""Google Analytics 4 wrapper tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from stackos.integrations.google_analytics import GoogleAnalyticsIntegration
from stackos.repositories.base import ValidationError


def _access_payload() -> bytes:
    return json.dumps({"access_token": "ga-token"}).encode("utf-8")


def test_google_analytics_wrapper_does_not_refresh_tokens_locally(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    async def go() -> None:
        async with httpx.AsyncClient() as client:
            integration = GoogleAnalyticsIntegration(
                payload=json.dumps(
                    {
                        "client_id": "cid",
                        "client_secret": "secret",
                        "refresh_token": "refresh",
                    }
                ).encode("utf-8"),
                project_id=project_id,
                http=client,
                qps_override=1000.0,
            )
            await integration.test_credentials()

    with pytest.raises(ValidationError, match="access_token"):
        asyncio.run(go())

    assert httpx_mock.get_requests() == []


def test_google_analytics_wrapper_maps_data_api_endpoints(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="GET",
        url="https://analyticsdata.googleapis.com/v1beta/properties/0/metadata",
        json={"name": "properties/0/metadata", "metrics": [{"apiName": "sessions"}]},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://analyticsdata.googleapis.com/v1beta/properties/1234:runReport",
        json={"rows": [{"metricValues": [{"value": "9"}]}]},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://analyticsdata.googleapis.com/v1beta/properties/1234:runRealtimeReport",
        json={"rows": [{"metricValues": [{"value": "2"}]}]},
    )

    async def go() -> list[Any]:
        async with httpx.AsyncClient() as client:
            integration = GoogleAnalyticsIntegration(
                payload=_access_payload(),
                project_id=project_id,
                http=client,
                qps_override=1000.0,
            )
            metadata = await integration.metadata_get(property_ref="0")
            report = await integration.run_report(
                property_ref="1234",
                request_body={
                    "dateRanges": [{"startDate": "2026-06-01", "endDate": "2026-06-07"}],
                    "metrics": [{"name": "sessions"}],
                },
            )
            realtime = await integration.run_realtime_report(
                property_ref="properties/1234",
                request_body={"metrics": [{"name": "activeUsers"}]},
            )
            return [metadata, report, realtime]

    metadata, report, realtime = asyncio.run(go())
    requests = httpx_mock.get_requests()
    report_body = json.loads(requests[1].content.decode("utf-8"))
    realtime_body = json.loads(requests[2].content.decode("utf-8"))

    assert metadata.data["metrics"][0]["apiName"] == "sessions"
    assert report.data["rows"][0]["metricValues"][0]["value"] == "9"
    assert realtime.data["rows"][0]["metricValues"][0]["value"] == "2"
    assert requests[0].headers["Authorization"] == "Bearer ga-token"
    assert report_body["metrics"] == [{"name": "sessions"}]
    assert realtime_body["metrics"] == [{"name": "activeUsers"}]
