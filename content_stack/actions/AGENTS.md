# Action Connector Agent Notes

Action connectors are provider-specific daemon adapters. Keep one provider per
file, for example `meta_ads.py`, `hubspot.py`, or `taboola.py`.

## Expectations

- Do not add generic provider REST adapters for first-party providers.
- Connectors execute explicit action payloads only; they do not choose strategy,
  build campaigns from intent, allocate budgets, or invent workflow steps.
- Resolve credentials only inside the daemon process. Never return secrets,
  authorization headers, API keys, OAuth tokens, refresh tokens, or passwords in
  output or metadata.
- Validate provider-required shape before making requests. If an endpoint is not
  verified against official docs, keep the manifest action deferred.
- Add concise official documentation links in the provider connector docstring.
- Keep shared helpers in `provider_utils.py` or `vendor_utils.py`; provider
  request mapping belongs in the provider file.
- When adding a connector, update the plugin manifest config, availability
  expectations, mocked execution/redaction tests, and integration contract docs.

