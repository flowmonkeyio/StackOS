"""Host-owned reference and saved-configuration preparation for native calls."""

from dataclasses import replace

from stackos.actions.connectors import ActionConnectorRequest
from stackos.actions.package_inputs.business import prepare_business_request
from stackos.actions.package_inputs.web import prepare_web_input

_BUSINESS = frozenset(
    {
        "apollo",
        "clay",
        "google-ads",
        "google-workspace",
        "meta-ads",
        "microsoft-365",
        "outreach",
        "pipedrive",
        "salesforce",
        "salesloft",
        "taboola",
    }
)
_GOOGLE_REFS = frozenset({"google-analytics", "google-tag-manager"})


def needs_preparation(connector: str) -> bool:
    return connector in _BUSINESS | _GOOGLE_REFS


def prepare_request(connector: str, request: ActionConnectorRequest) -> ActionConnectorRequest:
    if connector in _BUSINESS:
        return prepare_business_request(request)
    if connector not in _GOOGLE_REFS | {"wordpress", "ghost", "shopify"}:
        return request
    credential = request.credential
    config = dict(credential.config_json or {}) if credential else {}
    data = request.input_json
    if connector in _GOOGLE_REFS:
        data = prepare_web_input(connector, data, config)
    aliases = {
        "wordpress": ("wp_url", "site_url", "base_url"),
        "ghost": ("ghost_url", "site_url", "base_url"),
        "shopify": ("store_domain", "shop_domain", "shop"),
    }.get(connector)
    if aliases:
        value = next((config[key] for key in aliases if config.get(key)), None)
        if value is not None:
            config[aliases[0]] = value
    if credential is not None:
        credential = replace(credential, config_json=config)
    return replace(request, input_json=data, credential=credential)
