import re
from pathlib import Path

import yaml

CONTRACT_PATH = Path("docs/integration-contracts/hubspot.md")
RUNBOOK_PATH = Path("docs/oauth-provider-setup.md")
CURRENT_CONNECTORS_PATH = Path("docs/integration-contracts/current-connectors.md")
GTM_MANIFEST_PATH = Path("plugins/gtm/plugin.yaml")

EXECUTABLE_ACTION_REFS = {
    "hubspot.crm.contacts.properties.list",
    "hubspot.crm.companies.properties.list",
    "hubspot.crm.deals.properties.list",
    "hubspot.crm.owners.list",
    "hubspot.crm.deals.pipelines.list",
    "hubspot.crm.contact_company.labels.list",
    "hubspot.crm.contact_deal.labels.list",
    "hubspot.crm.company_deal.labels.list",
    "hubspot.crm.contacts.search",
    "hubspot.crm.contacts.batch_upsert",
    "hubspot.crm.companies.search",
    "hubspot.crm.companies.batch_upsert",
    "hubspot.crm.deals.list",
    "hubspot.crm.deals.search",
    "hubspot.crm.deals.batch_upsert",
    "hubspot.crm.leads.properties.list",
    "hubspot.crm.leads.search",
    "hubspot.crm.leads.batch_upsert",
    "hubspot.crm.contact_company.associate",
    "hubspot.crm.contact_company.dissociate",
    "hubspot.crm.contact_deal.associate",
    "hubspot.crm.contact_deal.dissociate",
    "hubspot.crm.company_deal.associate",
    "hubspot.crm.company_deal.dissociate",
    "hubspot.crm.notes.create",
    "hubspot.crm.tasks.create",
    "hubspot.crm.calls.create",
    "hubspot.crm.meetings.create",
    "hubspot.sales.products.properties.list",
    "hubspot.sales.line_items.properties.list",
    "hubspot.sales.quotes.properties.list",
    "hubspot.sales.goal_targets.properties.list",
    "hubspot.sales.products.search",
    "hubspot.sales.products.batch_upsert",
    "hubspot.sales.line_items.search",
    "hubspot.sales.line_items.batch_upsert",
    "hubspot.sales.line_items.associate_deal",
    "hubspot.sales.line_items.dissociate_deal",
    "hubspot.sales.quotes.search",
    "hubspot.sales.goal_targets.list",
    "hubspot.marketing.forms.list",
    "hubspot.marketing.forms.submissions.list",
    "hubspot.marketing.segments.list",
    "hubspot.marketing.segments.memberships.list",
    "hubspot.marketing.segments.memberships.add",
    "hubspot.marketing.segments.memberships.remove",
    "hubspot.marketing.campaigns.list",
    "hubspot.marketing.campaigns.get",
    "hubspot.marketing.campaigns.create",
    "hubspot.marketing.campaigns.update",
    "hubspot.marketing.emails.list",
    "hubspot.marketing.emails.get",
    "hubspot.marketing.emails.create",
    "hubspot.marketing.emails.update",
    "hubspot.marketing.subscription_types.list",
    "hubspot.marketing.contact_preferences.get",
    "hubspot.marketing.contact_preferences.update",
    "hubspot.marketing.events.list",
    "hubspot.marketing.events.upsert",
    "hubspot.marketing.behavioral_events.definitions.list",
    "hubspot.marketing.behavioral_events.send",
    "hubspot.bulk.exports.create",
    "hubspot.bulk.exports.status",
    "hubspot.bulk.exports.result",
    "hubspot.transactional.single_email.send",
}

DEFERRED_ACTION_REFS = {
    "hubspot.crm.custom_objects.search",
    "hubspot.crm.custom_objects.batch_upsert",
    "hubspot.sales.sequences.list",
    "hubspot.sales.sequences.enroll",
    "hubspot.sales.sequences.unenroll",
    "hubspot.sales.forecasts.list",
    "hubspot.sales.deal_splits.list",
    "hubspot.sales.deal_splits.upsert",
    "hubspot.marketing.contacts.status.update",
    "hubspot.marketing.emails.publish",
    "hubspot.marketing.emails.bulk_send",
    "hubspot.bulk.imports.create",
    "hubspot.webhooks.subscriptions.configure",
    "hubspot.automation.workflow_actions.register",
}


def _contract_text() -> str:
    return CONTRACT_PATH.read_text(encoding="utf-8")


def _hubspot_provider() -> dict[str, object]:
    manifest = yaml.safe_load(GTM_MANIFEST_PATH.read_text(encoding="utf-8"))
    return next(provider for provider in manifest["providers"] if provider["key"] == "hubspot")


def test_hubspot_contract_freezes_action_inventory_and_rejects_universal_records() -> None:
    contract = _contract_text()
    refs = set(re.findall(r"`(hubspot\.[a-z0-9_.]+)`", contract))

    assert refs >= EXECUTABLE_ACTION_REFS
    assert refs >= DEFERRED_ACTION_REFS
    assert not any(ref.startswith("hubspot.records.") for ref in refs)
    assert not any(ref.startswith("hubspot.crm.records.") for ref in refs)
    assert "one connector owns shared HTTP" in contract


def test_hubspot_contract_freezes_auth_bundles_callback_and_readiness_groups() -> None:
    contract = _contract_text()

    assert "https://auth.stackos.flowmonkey.io/api/v1/auth/oauth/callback" in contract
    assert "https://api.hubspot.com/oauth/2026-03/*" in contract
    assert "plural `scopes` array and `hub_id`" in contract
    for bundle in (
        "CRM Core",
        "Sales",
        "Marketing",
        "Bulk",
        "Webhooks",
        "Custom Workflow Automation",
        "Transactional Communications",
    ):
        assert f"| {bundle} |" in contract


def test_hubspot_contract_deferred_rows_name_execution_reason_and_no_connector() -> None:
    contract = _contract_text()
    table_lines = contract.splitlines()

    for action_ref in DEFERRED_ACTION_REFS:
        matching = [line for line in table_lines if f"`{action_ref}`" in line]
        assert matching, action_ref
        assert any("deferred" in line for line in matching), action_ref
    assert "`deferred` rows have no connector" in contract


def test_hubspot_contract_records_setup_and_safe_reference_invariants() -> None:
    contract = _contract_text()

    for required in (
        "credential label: `HubSpot OAuth app`",
        "verified_at=2026-07-22",
        "provider-object:*",
        "project, provider, verified HubSpot `hub_id`, object type",
        "ActionConnectorError",
        "never starts a run plan",
        "No app registration, live HubSpot mutation, DNS/deployment change, or release",
    ):
        assert required in contract


def test_hubspot_contract_freezes_signed_ingress_and_manual_app_admin_boundaries() -> None:
    contract = _contract_text()

    for required in (
        "POST /api/v1/ingress/hubspot/{project_id}/{profile_key}",
        "HubSpot v3 HMAC plus five-minute timestamp",
        "provider-documented v3 signature",
        "only exact connection allowlists create an agent request",
        "untrusted `Host` headers",
        "signed URI.",
        "Legacy v1/v2 ingress",
        "never creates, selects, starts, or executes a run plan",
        "hubspot.webhooks.subscriptions.configure",
        "hubspot.automation.workflow_actions.register",
        "deferred-app-configuration",
    ):
        assert required in contract


def test_hubspot_contract_freezes_transactional_communication_boundary() -> None:
    contract = _contract_text()

    for required in (
        "transactional_email_entitlement_confirmed",
        "`crm.objects.contacts.read`, `transactional-email`",
        "content.template_ref",
        "consent_or_relationship_confirmed",
        "marketing_contact_state",
        "empty `contactProperties` map",
        "derives HubSpot",
        "Bulk marketing blasts remain",
        "It never returns the recipient",
        "email or raw HubSpot contact",
    ):
        assert required in contract


def test_hubspot_operator_runbook_matches_manifest_oauth_scope_bundles() -> None:
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
    contract = _contract_text()
    provider = _hubspot_provider()
    config = provider["config"]

    required_scopes = set(config["scopes"])
    optional_scopes = {
        scope for bundle in config["scope_bundles"].values() for scope in bundle["optional_scopes"]
    }
    for scope in required_scopes | optional_scopes:
        assert f'"{scope}"' in runbook
        assert f"`{scope}`" in contract

    oauth_method = next(
        method
        for method in provider["auth_methods"]
        if method["key"] == "oauth2_authorization_code"
    )
    assert oauth_method["interactive"] is True
    assert '"distribution": "private"' in runbook
    assert '"type": "oauth"' in runbook
    assert "https://api.hubspot.com/oauth/2026-03/token" in runbook
    assert "live at `auth.stackos.flowmonkey.io`" in runbook
    assert "Mocked and local verification must never be reported" in runbook


def test_hubspot_docs_remove_stale_manual_only_classification() -> None:
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
    auth_docs = Path("docs/auth-providers.md").read_text(encoding="utf-8")
    current_connectors = CURRENT_CONNECTORS_PATH.read_text(encoding="utf-8")

    stale_claims = (
        "HubSpot is intentionally outside this runbook",
        "HubSpot remains manual-token/private-app compatible",
        "Existing manual OAuth token only; excluded from this delivery",
        "HubSpot | Manual OAuth token only",
    )
    combined = "\n".join((runbook, auth_docs, current_connectors))
    for claim in stale_claims:
        assert claim not in combined

    assert "| HubSpot | Authorization code with capability-scoped optional consent" in runbook
    assert "| `hubspot` | Provider-specific `gtm.hubspot.*`" in current_connectors


def test_hubspot_contract_maps_only_focused_gtm_workflows() -> None:
    contract = _contract_text()

    for workflow_key in (
        "gtm.crm-hygiene-pass",
        "gtm.pipeline-risk-review",
        "gtm.marketing-program-lifecycle",
        "gtm.crm-export-handoff",
        "gtm.customer-follow-up",
    ):
        assert f"`{workflow_key}`" in contract
    assert "does not expose one\nHubSpot mega-workflow" in contract
    assert "Webhooks/Automation has no workflow template" in contract
