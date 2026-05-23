# StackOS Integration Testing

Use the built-in mock provider before live vendor credentials exist.

The mock provider is intentionally fake at the vendor edge, but real inside
StackOS:

```text
REST / CLI / MCP
-> operation registry
-> run-plan grant check
-> action schema and connector validation
-> daemon-side credential resolution
-> connector execution
-> redacted action-call and credential-usage audit
```

## Built-In Mock Provider

- Plugin: `utils`
- Provider: `mock-provider`
- Auth method: `api_key`
- Action: `utils.mock.echo`
- Connector: `mock-provider`

Store any non-empty fake API key through the normal auth setup route, then pass
only the returned `credential_ref` to `action.execute`.

Example action payload:

```json
{
  "project_id": 1,
  "run_token": "run-plan-token",
  "action_ref": "utils.mock.echo",
  "credential_ref": "cred_...",
  "input_json": {
    "message": "hello",
    "echo": {
      "campaign": "local-test"
    },
    "cost_cents": 7
  }
}
```

Supported `scenario` values:

- `success`
- `partial_success`
- `provider_error`
- `invalid_credentials`
- `rate_limit`
- `timeout`

Failure scenarios still go through the connector path and should produce
redacted failed action-call audit rows. They are useful for validating agent and
operator behavior around retries, rate limits, credential failure, and provider
errors without live API accounts.

## Entrypoint Parity

The same mock-provider run is proven through all three callable entrypoints:

| Entrypoint | Test | What it proves |
| --- | --- | --- |
| REST | `tests/integration/test_routes/test_operations_routes.py::test_operation_rest_mock_provider_vertical_slice` | Generic REST operation calls enter the shared registry and produce linked, redacted action-call audit. |
| CLI | `tests/integration/test_routes/test_cli_mock_provider.py::test_cli_mock_provider_vertical_slice_uses_shared_operation_registry` | CLI aliases call the same REST operation adapter and do not bypass grants, auth, redaction, or audit. |
| MCP | `tests/integration/test_mcp/test_mcp_actions.py::test_action_execute_mock_provider_vertical_slice_through_mcp` | MCP tools generated from the operation specs hit the same action registry and run-plan boundary. |

All three checks assert the same invariants:

- `provider_key` and `connector_key` are both `mock-provider`.
- The action call is linked to the run, run plan, and active step.
- The caller passes only an opaque `credential_ref`.
- Secret-like values returned by the fake connector are redacted from output,
  metadata, and audit rows.
- The action cost and structured output are recorded through the normal
  action-call ledger.

## Rule

Do not bypass StackOS internals during local integration testing. Mock only the
external vendor edge. Auth refs, credential storage, run-plan grants, action
validation, connector lookup, redaction, and audit must stay enabled.
