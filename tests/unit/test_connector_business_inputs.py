from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest

from stackos.actions.connectors import ActionConnectorRequest
from stackos.actions.package_inputs.business import prepare_business_request
from stackos.auth_providers.repository.schema import ResolvedCredential
from stackos.db.models import Credential, IntegrationCredential
from stackos.repositories.base import ValidationError


def request(connector, operation, data, config, fields=None):
    return ActionConnectorRequest(
        project_id=1,
        plugin_slug="fixture",
        action_key=connector + ".fixture",
        action_ref="fixture",
        provider_key=connector,
        operation=operation,
        input_json=data,
        config_json={"connector": connector},
        credential=ResolvedCredential(
            credential=Credential(
                credential_ref="cred_fixture",
                provider_key=connector,
                auth_method_key="oauth2_token",
            ),
            integration=IntegrationCredential(encrypted_payload=b"unused", nonce=b"0" * 12),
            secret_payload=json.dumps(fields or {"access_token": "synthetic"}).encode(),
            config_json=config,
        ),
    )


@pytest.mark.parametrize(
    "connector,field,mapping,native",
    [
        ("google-ads", "customer_ref", "customers", "customer_id"),
        ("google-workspace", "calendar_ref", "calendars", "calendar_id"),
        ("microsoft-365", "user_ref", "users", "user_id"),
        ("meta-ads", "ad_set_ref", "ad_sets", "ad_set_id"),
        ("outreach", "mailbox_ref", "mailboxes", "mailbox_id"),
        ("pipedrive", "organization_ref", "organizations", "organization_id"),
        ("salesloft", "person_ref", "people", "person_id"),
        ("taboola", "campaign_ref", "campaign_refs", "campaign_id"),
    ],
)
def test_saved_aliases_stay_host_owned_and_inputs_are_detached(connector, field, mapping, native):
    value = request(connector, "fixture", {field: "saved"}, {mapping: {"saved": "native123"}})
    prepared = prepare_business_request(value)
    assert prepared.input_json == {native: "native123"}
    assert value.input_json == {field: "saved"}
    assert mapping not in prepared.credential.config_json


def test_google_ads_public_ad_spelling_and_manager_header_are_prepared():
    value = request(
        "google-ads",
        "ad_group_ad.create",
        {"customer_ref": "customer", "ad_group_ref": "group", "ad": {"status": "ENABLED"}},
        {"customers": {"customer": "123"}, "ad_groups": {"group": "456"}},
        fields={"access_token": "synthetic", "manager_account_ref": "999-888-7777"},
    )
    result = prepare_business_request(value)
    assert result.input_json == {
        "customer_id": "123",
        "ad_group_id": "456",
        "ad_group_ad": {"status": "ENABLED"},
    }
    assert result.credential.config_json["login_customer_id"] == "999-888-7777"


def test_salesforce_template_and_external_id_policy_are_resolved_in_host():
    query = request(
        "salesforce",
        "opportunities.query",
        {"query_template_ref": "pipeline"},
        {
            "query_templates": {"pipeline": " SELECT Id FROM Opportunity "},
            "instance_url": "https://acme.salesforce.com",
        },
    )
    assert prepare_business_request(query).input_json == {"query": "SELECT Id FROM Opportunity"}
    upsert = request(
        "salesforce",
        "account.upsert_by_external_id",
        {"external_id_policy_ref": "crm"},
        {"external_id_fields": {"crm": "External_Id__c"}},
    )
    assert prepare_business_request(upsert).input_json == {"external_id_field": "External_Id__c"}
    with pytest.raises(ValidationError, match="configured"):
        prepare_business_request(
            request("salesforce", "opportunities.query", {"query_template_ref": "missing"}, {})
        )


def test_clay_table_selection_does_not_cross_provider_boundary():
    value = request(
        "clay",
        "table.webhook.submit",
        {"table_ref": "saved", "rows": [{"x": 1}]},
        {"webhook_url": "https://hooks.example.test/table", "workspace_ref": "workspace"},
    )
    prepared = prepare_business_request(value)
    assert prepared.input_json == {"rows": [{"x": 1}]}
    assert prepared.credential.config_json == {
        "webhook_url": "https://hooks.example.test/table",
        "auth_method_key": "oauth2_token",
    }


def test_reference_collision_rejected_and_generic_pipedrive_ref_behavior_retained():
    with pytest.raises(ValidationError, match="override"):
        prepare_business_request(
            request("pipedrive", "deals.list", {"person_ref": "saved", "person_id": "override"}, {})
        )
    value = request(
        "pipedrive", "deals.list", {"filter_id": "saved-filter"}, {"refs": {"saved-filter": 42}}
    )
    assert prepare_business_request(value).input_json == {"filter_id": 42}


# Exact wire fixtures captured from the pre-extraction source, then frozen.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,module,action,operation,data,config,expected_wire",
    [
        (
            "google-ads",
            "google_ads",
            "google.report.search",
            "report.search",
            {"customer_ref": "saved", "query": "SELECT campaign.id FROM campaign"},
            {"customers": {"saved": 123}, "manager_account_ref": 999},
            {
                "method": "POST",
                "url": "https://googleads.googleapis.com/v24/customers/123/googleAds:search",
                "body": '{"query":"SELECT campaign.id FROM campaign"}',
                "headers": {
                    "host": "googleads.googleapis.com",
                    "authorization": "Bearer synthetic",
                    "developer-token": "synthetic-dev",
                    "content-type": "application/json",
                    "login-customer-id": "999",
                    "content-length": "44",
                },
            },
        ),
        (
            "google-workspace",
            "google_workspace",
            "google-workspace.calendar.event.create",
            "calendar.event.create",
            {"calendar_ref": "saved", "event": {"start": {}, "end": {}}},
            {"calendars": {"saved": 123}},
            {
                "method": "POST",
                "url": "https://www.googleapis.com/calendar/v3/calendars/123/events",
                "body": '{"start":{},"end":{}}',
                "headers": {
                    "host": "www.googleapis.com",
                    "authorization": "Bearer synthetic",
                    "content-type": "application/json",
                    "content-length": "21",
                },
            },
        ),
        (
            "microsoft-365",
            "microsoft_graph",
            "microsoft-365.graph.mail.send",
            "graph.mail.send",
            {"user_ref": "saved", "message": {}},
            {"users": {"saved": 123}},
            {
                "method": "POST",
                "url": "https://graph.microsoft.com/v1.0/users/123/sendMail",
                "body": '{"message":{},"saveToSentItems":true}',
                "headers": {
                    "host": "graph.microsoft.com",
                    "authorization": "Bearer synthetic",
                    "content-type": "application/json",
                    "content-length": "37",
                },
            },
        ),
        (
            "meta-ads",
            "meta_ads",
            "meta.campaign.pause",
            "campaign.pause",
            {"campaign_ref": "saved"},
            {"campaigns": {"saved": 123}},
            {
                "method": "POST",
                "url": "https://graph.facebook.com/v25.0/123",
                "body": "status=PAUSED",
                "headers": {
                    "host": "graph.facebook.com",
                    "authorization": "Bearer synthetic",
                    "content-length": "13",
                    "content-type": "application/x-www-form-urlencoded",
                },
            },
        ),
        (
            "outreach",
            "outreach",
            "outreach.sequence_state.create",
            "sequence_state.create",
            {"sequence_ref": "saved", "prospect_ref": "saved", "mailbox_ref": "saved"},
            {"sequences": {"saved": 123}, "prospects": {"saved": 456}, "mailboxes": {"saved": 789}},
            {
                "method": "POST",
                "url": "https://api.outreach.io/api/v2/sequenceStates",
                "body": (
                    '{"data":{"type":"sequenceState","relationships":'
                    '{"sequence":{"data":{"type":"sequence","id":"123"}},'
                    '"prospect":{"data":{"type":"prospect","id":"456"}},'
                    '"mailbox":{"data":{"type":"mailbox","id":"789"}}}}}'
                ),
                "headers": {
                    "host": "api.outreach.io",
                    "authorization": "Bearer synthetic",
                    "accept": "application/vnd.api+json",
                    "content-type": "application/vnd.api+json",
                    "content-length": "202",
                },
            },
        ),
        (
            "pipedrive",
            "pipedrive",
            "pipedrive.deals.list",
            "deals.list",
            {"person_ref": "saved"},
            {"persons": {"saved": 123}, "api_domain": "https://acme.pipedrive.com"},
            {
                "method": "GET",
                "url": "https://acme.pipedrive.com/api/v2/deals?person_id=123",
                "body": "",
                "headers": {"host": "acme.pipedrive.com", "authorization": "Bearer synthetic"},
            },
        ),
        (
            "salesloft",
            "salesloft",
            "salesloft.cadence_membership.create",
            "cadence_membership.create",
            {"cadence_ref": "saved", "person_ref": "saved", "user_ref": "saved"},
            {"cadences": {"saved": 123}, "people": {"saved": 456}, "users": {"saved": 789}},
            {
                "method": "POST",
                "url": "https://api.salesloft.com/v2/cadence_memberships",
                "body": '{"cadence_id":123,"person_id":456,"user_id":789}',
                "headers": {
                    "host": "api.salesloft.com",
                    "authorization": "Bearer synthetic",
                    "content-type": "application/json",
                    "content-length": "48",
                },
            },
        ),
        (
            "taboola",
            "taboola",
            "taboola.campaign.pause",
            "campaign.pause",
            {"account_ref": "saved", "campaign_ref": "saved"},
            {"accounts": {"saved": 123}, "campaigns": {"saved": 456}},
            {
                "method": "PUT",
                "url": "https://backstage.taboola.com/backstage/api/1.0/123/campaigns/456",
                "body": '{"is_active":false}',
                "headers": {
                    "host": "backstage.taboola.com",
                    "authorization": "Bearer synthetic",
                    "content-type": "application/json",
                    "content-length": "19",
                },
            },
        ),
        (
            "google-workspace",
            "google_workspace",
            "google-workspace.gmail.message.send",
            "gmail.message.send",
            {"message": {"raw": "SGVsbG8"}},
            {"users": {"me": "mapped-user"}},
            {
                "method": "POST",
                "url": "https://gmail.googleapis.com/gmail/v1/users/mapped-user/messages/send",
                "body": '{"raw":"SGVsbG8"}',
                "headers": {
                    "host": "gmail.googleapis.com",
                    "authorization": "Bearer synthetic",
                    "content-type": "application/json",
                    "content-length": "17",
                },
            },
        ),
        (
            "google-workspace",
            "google_workspace",
            "google-workspace.gmail.message.send",
            "gmail.message.send",
            {"message": {"raw": "SGVsbG8"}},
            {"mailboxes": {"me": "mapped-mailbox"}},
            {
                "method": "POST",
                "url": "https://gmail.googleapis.com/gmail/v1/users/mapped-mailbox/messages/send",
                "body": '{"raw":"SGVsbG8"}',
                "headers": {
                    "host": "gmail.googleapis.com",
                    "authorization": "Bearer synthetic",
                    "content-type": "application/json",
                    "content-length": "17",
                },
            },
        ),
        (
            "google-workspace",
            "google_workspace",
            "google-workspace.gmail.message.send",
            "gmail.message.send",
            {"message": {"raw": "SGVsbG8"}},
            {"refs": {"me": "mapped-generic"}},
            {
                "method": "POST",
                "url": "https://gmail.googleapis.com/gmail/v1/users/mapped-generic/messages/send",
                "body": '{"raw":"SGVsbG8"}',
                "headers": {
                    "host": "gmail.googleapis.com",
                    "authorization": "Bearer synthetic",
                    "content-type": "application/json",
                    "content-length": "17",
                },
            },
        ),
        (
            "google-workspace",
            "google_workspace",
            "google-workspace.gmail.message.send",
            "gmail.message.send",
            {"message": {"raw": "SGVsbG8"}, "user_ref": ""},
            {"users": {"me": "mapped-user"}},
            {
                "method": "POST",
                "url": "https://gmail.googleapis.com/gmail/v1/users/mapped-user/messages/send",
                "body": '{"raw":"SGVsbG8"}',
                "headers": {
                    "host": "gmail.googleapis.com",
                    "authorization": "Bearer synthetic",
                    "content-type": "application/json",
                    "content-length": "17",
                },
            },
        ),
        (
            "google-workspace",
            "google_workspace",
            "google-workspace.gmail.message.send",
            "gmail.message.send",
            {"message": {"raw": "SGVsbG8"}, "user_ref": "explicit"},
            {"users": {"me": "wrong", "explicit": "selected"}},
            {
                "method": "POST",
                "url": "https://gmail.googleapis.com/gmail/v1/users/selected/messages/send",
                "body": '{"raw":"SGVsbG8"}',
                "headers": {
                    "host": "gmail.googleapis.com",
                    "authorization": "Bearer synthetic",
                    "content-type": "application/json",
                    "content-length": "17",
                },
            },
        ),
        (
            "google-workspace",
            "google_workspace",
            "google-workspace.gmail.message.send",
            "gmail.message.send",
            {"message": {"raw": "SGVsbG8"}},
            {},
            {
                "method": "POST",
                "url": "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                "body": '{"raw":"SGVsbG8"}',
                "headers": {
                    "host": "gmail.googleapis.com",
                    "authorization": "Bearer synthetic",
                    "content-type": "application/json",
                    "content-length": "17",
                },
            },
        ),
        (
            "taboola",
            "taboola",
            "taboola.report.fetch",
            "report.fetch",
            {
                "account_ref": "account",
                "campaign_ref": "campaign",
                "dimension": "day",
                "start_date": "2026-01-01",
                "end_date": "2026-01-02",
            },
            {"campaign_refs": {"campaign": 123}, "refs": {"campaign": 456}},
            {
                "method": "GET",
                "url": "https://backstage.taboola.com/backstage/api/1.0/account/reports/campaign-summary/dimensions/day?start_date=2026-01-01&end_date=2026-01-02&campaign=456",
                "body": "",
                "headers": {
                    "host": "backstage.taboola.com",
                    "authorization": "Bearer synthetic",
                    "content-type": "application/json",
                },
            },
        ),
        (
            "taboola",
            "taboola",
            "taboola.report.fetch",
            "report.fetch",
            {
                "account_ref": "account",
                "campaign_ref": "campaign",
                "dimension": "day",
                "start_date": "2026-01-01",
                "end_date": "2026-01-02",
            },
            {
                "campaigns": {"campaign": 789},
                "campaign_refs": {"campaign": 123},
                "refs": {"campaign": 456},
            },
            {
                "method": "GET",
                "url": "https://backstage.taboola.com/backstage/api/1.0/account/reports/campaign-summary/dimensions/day?start_date=2026-01-01&end_date=2026-01-02&campaign=789",
                "body": "",
                "headers": {
                    "host": "backstage.taboola.com",
                    "authorization": "Bearer synthetic",
                    "content-type": "application/json",
                },
            },
        ),
        (
            "taboola",
            "taboola",
            "taboola.report.fetch",
            "report.fetch",
            {
                "account_ref": "account",
                "campaign_ref": "campaign",
                "dimension": "day",
                "start_date": "2026-01-01",
                "end_date": "2026-01-02",
            },
            {"campaign_refs": {"campaign": 123}},
            {
                "method": "GET",
                "url": "https://backstage.taboola.com/backstage/api/1.0/account/reports/campaign-summary/dimensions/day?start_date=2026-01-01&end_date=2026-01-02&campaign=campaign",
                "body": "",
                "headers": {
                    "host": "backstage.taboola.com",
                    "authorization": "Bearer synthetic",
                    "content-type": "application/json",
                },
            },
        ),
        (
            "taboola",
            "taboola",
            "taboola.campaign.pause",
            "campaign.pause",
            {"account_ref": "account", "campaign_ref": "campaign"},
            {"campaign_refs": {"campaign": 123}, "refs": {"campaign": 456}},
            {
                "method": "PUT",
                "url": "https://backstage.taboola.com/backstage/api/1.0/account/campaigns/123",
                "body": '{"is_active":false}',
                "headers": {
                    "host": "backstage.taboola.com",
                    "authorization": "Bearer synthetic",
                    "content-type": "application/json",
                    "content-length": "19",
                },
            },
        ),
    ],
)
async def test_aliases_preserve_original_wire_through_catalog_and_bridge(
    monkeypatch,
    provider,
    module,
    action,
    operation,
    data,
    config,
    expected_wire,
):
    from stackos_connectors import CallOptions, ConnectorClient
    from stackos_connectors.catalog import load_registry

    from stackos.actions.package_bridge import PackageActionConnector

    host_request = request(
        provider,
        operation,
        data,
        {**config, "auth_method_key": "oauth2_token"},
        fields={"access_token": "synthetic", "developer_token": "synthetic-dev"},
    )
    host_request = replace(host_request, action_key=action)
    if provider in {"taboola", "google-ads"}:
        method = "client_credentials" if provider == "taboola" else "oauth2_authorization_code"
        host_request.credential.credential.auth_method_key = method
        host_request = replace(
            host_request,
            credential=replace(
                host_request.credential,
                config_json={**config, "auth_method_key": method},
            ),
        )
    calls = []

    def respond(sent):
        calls.append(sent)
        assert sent.method == expected_wire["method"]
        assert sent.url == expected_wire["url"]
        assert sent.content == expected_wire["body"].encode()
        if provider == "google-ads":
            assert (
                sent.headers["login-customer-id"]
                == expected_wire["headers"]["login-customer-id"]
                == "999"
            )
        if provider == "salesloft":
            assert json.loads(sent.content) == {"cadence_id": 123, "person_id": 456, "user_id": 789}
        return httpx.Response(200, json={"id": "accepted"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        bridge = PackageActionConnector(
            provider,
            client=ConnectorClient(
                registry=load_registry("connectors/" + provider.replace("-", "_") + "/catalog.json")
            ),
            options=CallOptions(http=http),
        )
        await bridge.execute(host_request)
    assert len(calls) == 1
