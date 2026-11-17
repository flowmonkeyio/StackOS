# StackOS Integration Testing

Use the built-in mock provider before live vendor credentials exist.

The mock provider is intentionally fake at the vendor edge, but real inside
StackOS:

```text
REST / CLI / MCP
-> operation registry
-> direct-action policy or run-plan grant check
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
only the returned `credential_ref` to `action.run` for one explicit call or
`action.execute` inside a granted run-plan step.

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
redacted failed action-call audit rows. Provider HTTP failures use
`ActionConnectorError`, so the operation error envelope and audit
`response_json` preserve safe fields such as `provider_status_code`,
`provider_error.code`, `provider_error.message`, and retry metadata. They are
useful for validating agent and operator behavior around retries, rate limits,
credential failure, and provider errors without live API accounts.

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

## Telegram Local Slice

Telegram communication setup has an account-owned TDLib slice because it spans
credential setup, explicit agent session control, local user authorization,
communication-profile setup, native update ingress, agent requests, run plans,
and provider actions:

```bash
uv run pytest \
  tests/unit/test_telegram_tdlib_sessions.py \
  tests/unit/test_telegram_tdlib_service.py \
  tests/integration/test_repositories/test_telegram_account_lifecycle.py \
  tests/integration/test_repositories/test_telegram_account_session_operations.py \
  tests/integration/test_repositories/test_telegram_native_authorization.py \
  tests/integration/test_routes/test_auth_provider_routes.py \
  tests/integration/test_mcp/test_mcp_auth.py \
  tests/integration/test_mcp/test_mcp_communications.py \
  tests/integration/test_repositories/test_telegram_actions.py \
  -q
```

This does not require a live Telegram account. It uses fake Account material and
a fake TDLib runtime at the provider edge while preserving the real StackOS
credential boundary, project attachment check, one-session lifecycle, durable
desired-connected state, authorization generation fence, native update
normalization, action grants, and redacted action/resource audit. It proves that
only an explicit connect restores a session after daemon boot and that explicit
disconnect stops ingress, preserves saved user authorization, and holds later
delivery. It does not prove a real
Telegram login, proxy, destination rights, or provider send; those require the
operator-owned live verification gate.

## Slack Local Slice

Slack communication setup has mocked provider coverage for the Web API action
edge and signed HTTP ingress:

```bash
uv run pytest \
  tests/integration/test_repositories/test_slack_bot_actions.py \
  tests/integration/test_routes/test_slack_ingress_routes.py \
  -q
```

This does not require a live Slack workspace. It uses fake bot token/signing
secret values and mocked Slack Web API responses, while still exercising the
real StackOS credential boundary, communication-profile binding, raw-body HMAC
verification, Block Kit interaction state, native message reaction actions,
message delete actions, generic agent requests, and redacted action-call/resource
audit.

## Retained Consumers And Lifecycle Fixtures

For a migration change, inspect affected retained readers and current-head
expectations as well as the migration itself. Tests that initialize or upgrade
to head must reconcile their expected revision with the migration owner;
historical upgrade/downgrade cases retain their deliberately pinned revisions
and data assertions. Update
only expectations invalidated by the change, preserving released migration
fixes and unrelated historical coverage.

For a shared UI producer or component change, trace its actual emitted payload
and event contract through affected retained callers, including screens outside
the edited path. Exercise the applicable selection, empty/error and recovery
interaction at the consumer; a hand-built simplified payload or a component
test alone cannot prove that wiring. Use the existing UI checks and selected
browser proof where rendering or interaction changes, with the independent
guards in `ui/src/design/DESIGN.md`.

Lifecycle source tests use disposable HOME, data/state directories and explicit
host/port settings before invoking the real caller. A sandbox HOME alone does
not isolate a probe that still targets the installed daemon. For an absent
source, reserve a non-listening port for the fixture lifetime; occupied-source
cases need their own owned endpoint and explicit expected refusal or handoff.
Use the real emitted upgrade identity, preparation/continuation result and
checkpoint refs where the claim crosses those owners. Mock only the declared
external/native edge, retain the full consumed contract and state its limits;
do not disable the guard or return a shortened success object to hide a failure.
The existing install-script sandbox and upgrade-caller regressions own these
checks; preserve them rather than adding another fixture framework.

These are source/fixture safeguards. They do not execute or establish the
deferred real installed-upgrade M2 proof, app restart, signing or notarization.
Those remain with their existing explicitly authorized lifecycle/release gate.

## Rule

Do not bypass StackOS internals during local integration testing. Mock only the
external vendor edge. Auth refs, credential storage, run-plan grants, action
validation, connector lookup, redaction, and audit must stay enabled.
