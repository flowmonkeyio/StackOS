"""Resolve host business-connector aliases before passing provider-native data.

Call after the host credential resolver has selected and refreshed an Account.
No network, credential lifecycle or repository writes occur here.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

from stackos.actions.connectors import ActionConnectorRequest
from stackos.actions.provider_utils import credential_config, credential_payload, resolve_ref
from stackos.repositories.base import ValidationError

_REFERENCES: dict[str, dict[str, tuple[str, tuple[str, ...]]]] = {
    "google-ads": {
        "customer_ref": ("customer_id", ("customers", "customer_refs")),
        "campaign_budget_ref": ("campaign_budget_id", ("campaign_budgets",)),
        "campaign_ref": ("campaign_id", ("campaigns",)),
        "ad_group_ref": ("ad_group_id", ("ad_groups",)),
    },
    "google-workspace": {
        "user_ref": ("user_id", ("users", "mailboxes")),
        "calendar_ref": ("calendar_id", ("calendars",)),
    },
    "microsoft-365": {
        "user_ref": ("user_id", ("users", "mailboxes")),
        "calendar_ref": ("calendar_id", ("calendars",)),
    },
    "meta-ads": {
        "account_ref": ("account_id", ("accounts", "account_refs")),
        "campaign_ref": ("campaign_id", ("campaigns", "campaigns_refs")),
        "ad_set_ref": ("ad_set_id", ("ad_sets", "ad_sets_refs")),
        "creative_ref": ("creative_id", ("creatives", "creatives_refs")),
        "ad_ref": ("ad_id", ("ads", "ads_refs")),
        "scope_ref": ("scope_id", ("nodes", "nodes_refs")),
        "dataset_ref": ("dataset_id", ("datasets", "datasets_refs")),
    },
    "outreach": {
        "sequence_ref": ("sequence_id", ("sequences",)),
        "prospect_ref": ("prospect_id", ("prospects",)),
        "mailbox_ref": ("mailbox_id", ("mailboxes",)),
    },
    "pipedrive": {
        "owner_ref": ("owner_id", ("owners",)),
        "person_ref": ("person_id", ("persons",)),
        "organization_ref": ("organization_id", ("organizations",)),
        "pipeline_ref": ("pipeline_id", ("pipelines",)),
        "stage_ref": ("stage_id", ("stages",)),
    },
    "salesloft": {
        "cadence_ref": ("cadence_id", ("cadences",)),
        "person_ref": ("person_id", ("persons", "people")),
        "user_ref": ("user_id", ("users",)),
    },
    "taboola": {
        "account_ref": ("account_id", ("accounts", "account_refs")),
        "campaign_ref": ("campaign_id", ("campaigns", "campaign_refs")),
        "item_ref": ("item_id", ("items", "item_refs")),
        "conversion_rule_ref": ("conversion_rule_id", ("conversion_rules",)),
    },
}
_CONFIG_FIELDS = {
    "apollo": ("access_scope",),
    "clay": ("webhook_url",),
    "google-ads": ("api_version",),
    "google-workspace": ("delegated_subject",),
    "meta-ads": ("api_version",),
    "microsoft-365": ("auth_mode",),
    "outreach": ("base_url",),
    "pipedrive": ("api_domain", "base_url", "company_domain"),
    "salesforce": ("instance_url", "api_version"),
    "salesloft": ("base_url",),
    "taboola": (),
}


def prepare_business_request(request: ActionConnectorRequest) -> ActionConnectorRequest:
    """Return a detached host request whose data/config have native provider meaning."""
    connector = str(request.config_json.get("connector") or request.provider_key or "")
    if connector not in _CONFIG_FIELDS:
        raise ValidationError("not a business connector")
    data = deepcopy(request.input_json)
    config = credential_config(request)
    if (
        connector == "google-ads"
        and not data.get("customer_ref")
        and config.get("default_customer_ref")
    ):
        data["customer_ref"] = config["default_customer_ref"]
    if connector == "taboola" and not data.get("account_ref") and config.get("account_ref"):
        data["account_ref"] = config["account_ref"]
    if (
        connector == "google-workspace"
        and request.operation == "gmail.message.send"
        and not data.get("user_ref")
    ):
        # The original Gmail route resolves its implicit "me" through saved aliases.
        data["user_ref"] = "me"
    for source, (target, maps) in _REFERENCES.get(connector, {}).items():
        if source in data:
            if target in data:
                raise ValidationError("native input cannot override a host reference")
            if (
                connector == "taboola"
                and request.operation == "report.fetch"
                and source == "campaign_ref"
            ):
                # Report filters use campaigns then generic refs; mutation routes
                # additionally support campaign_refs through their path-ID helper.
                maps = ("campaigns",)
            data[target] = resolve_ref(request, data.pop(source), *maps)
    if connector == "google-ads" and request.operation == "ad_group_ad.create" and "ad" in data:
        data["ad_group_ad"] = data.pop("ad")
    if connector == "pipedrive" and request.operation == "deals.list":
        for field in (
            "filter_id",
            "status",
            "updated_since",
            "updated_until",
            "sort_by",
            "sort_direction",
            "include_fields",
            "custom_fields",
            "limit",
            "cursor",
        ):
            if field in data:
                data[field] = resolve_ref(request, data[field], "refs")
    if connector == "clay":
        data.pop("table_ref", None)
    if connector == "salesforce":
        if "external_id_policy_ref" in data:
            policy = data.pop("external_id_policy_ref")
            mapping = config.get("external_id_fields")
            value = mapping.get(policy) if isinstance(mapping, dict) else None
            data["external_id_field"] = (
                value.strip() if isinstance(value, str) and value.strip() else policy
            )
        if "query_template_ref" in data:
            template = data.pop("query_template_ref")
            mapping = config.get("query_templates")
            value = mapping.get(template) if isinstance(mapping, dict) else None
            if not isinstance(value, str) or not value.strip():
                raise ValidationError("Salesforce query requires configured query_template_ref")
            data["query"] = value.strip()
    if (
        connector == "google-workspace"
        and request.credential is not None
        and request.credential.credential.auth_method_key == "service-account"
        and not config.get("delegated_subject")
    ):
        # Keep the host's existing account-admission diagnostics before native
        # schema validation; acquisition and delegation belong to the host.
        if request.operation == "gmail.message.send":
            raise ValidationError(
                "Gmail service-account access requires an explicitly delegated Workspace user"
            )
        if request.operation == "calendar.event.create":
            if data.get("calendar_id") == "primary":
                raise ValidationError(
                    "Direct service-account access requires an explicit shared calendar"
                )
            if isinstance(data.get("event"), dict) and data["event"].get("attendees"):
                raise ValidationError(
                    "Service-account attendee invitations require a delegated Workspace user"
                )
    native_config: dict[str, Any] = {
        key: deepcopy(config[key]) for key in _CONFIG_FIELDS[connector] if key in config
    }
    if connector == "google-ads" and request.credential is not None:
        manager = config.get("manager_account_ref") or credential_payload(request).get(
            "manager_account_ref"
        )
        if manager:
            native_config["login_customer_id"] = manager
    credential = request.credential
    if credential is not None:
        native_config["auth_method_key"] = credential.credential.auth_method_key
        credential = replace(credential, config_json=native_config)
    return replace(request, input_json=data, credential=credential)
