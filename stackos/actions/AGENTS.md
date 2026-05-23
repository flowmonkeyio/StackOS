# Action Connector Agent Notes

Action connectors are StackOS' provider execution adapters. They must stay
decision-free.

## Expectations

- Read [`../../docs/action-executor.md`](../../docs/action-executor.md),
  [`../../docs/operations.md`](../../docs/operations.md), and the relevant
  provider contract notes in
  [`../../docs/integration-contracts/`](../../docs/integration-contracts/)
  before adding executable network behavior.
- Keep one provider connector per file.
- Validate the exact provider/action input contract before any network call.
- Resolve credentials only through `ActionConnectorRequest.credential`.
- Never return, log, or store secret payload values in agent-visible output.
- Use safe refs from credential config for account, tenant, workspace, manager,
  or field mapping selection.
- Reject provider/documentation-invalid parameter combinations instead of
  translating them silently.
- If a provider operation is async submit-only, expose it only when a matching
  status/read action and output artifact contract exist. Otherwise keep the
  action manifest deferred.
- Add official documentation links in module comments for every provider action
  family that sends network requests.
- Do not make a connector agent-callable by itself. It becomes executable only
  through a plugin action manifest, operation/action visibility, run-plan
  grants, and tests.

## Test Checklist

- Validation rejects missing required fields and invalid provider enums/limits.
- Execution sends the documented method, URL, headers, query params, and body.
- Secret values are absent from action output and audit JSON.
- Credential status and run-plan grants are enforced through the repository
  path, not bypassed inside a connector.
- Budget behavior is honest: use actual provider costs/headers where available,
  or disable StackOS budget enforcement until unit mapping exists.
- Operation visibility and CLI/MCP/REST examples match the registered operation
  spec, not a hand-written duplicate contract.
